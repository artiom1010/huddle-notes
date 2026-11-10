"""huddle-notes: notes and named transcripts of Slack huddle recordings.

  huddle-notes watch                     process every new recording in the inbox, send results
  huddle-notes process VIDEO [--names "3=Вася"] [--learn] [--notify] [--no-notes]
  huddle-notes models [download]
  huddle-notes regress DIR [--accept]

Settings come from the environment and from a .env file (--env, default ./.env).
This is also where the objects are put together; nothing else reads the environment.
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import sys
from pathlib import Path

import truststore

from huddle_notes import models
from huddle_notes.attribution import SpeakerAttributor
from huddle_notes.config import ConfigError, Settings, load_env_file
from huddle_notes.deliver import SlackDelivery
from huddle_notes.feedback import SlackFeedback, Wishes
from huddle_notes.media import Media
from huddle_notes.models import download
from huddle_notes.notes import ClaudeCodeNotes
from huddle_notes.people import PeopleLibrary
from huddle_notes.pipeline import CallProcessor
from huddle_notes.regression import run_regression
from huddle_notes.speakers.detector import SpeakerDetector
from huddle_notes.transcribe import WhisperTranscriber
from huddle_notes.voices import VoiceEmbedder
from huddle_notes.watch import JOURNAL, Journal, Watcher

log = logging.getLogger("huddle_notes")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    # names and arrows print as UTF-8 even into a Windows pipe, whose default code page
    # can't hold them and would stop the run halfway
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="replace")
    # HTTPS (Slack, model downloads) trusts what the operating system trusts, like a browser:
    # company networks that re-sign TLS with their own certificate then still work
    truststore.inject_into_ssl()
    load_env_file(Path(args.env))
    _logging(args)
    try:
        settings = Settings.from_env()
        log.info("models: %s", settings.describe_models())
        if args.command == "watch":
            return _watch(settings)
        if args.command == "process":
            return _process(settings, args)
        if args.command == "models":
            return _models(settings, args)
        return _regress(settings, args)
    except ConfigError as e:
        log.error("%s", e)
        return 2


def _logging(args: argparse.Namespace) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if args.log:
        Path(args.log).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(args.log, encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=handlers)


def _processor(settings: Settings, notes: bool) -> CallProcessor:
    settings.require_transcription()
    media = Media(settings.hwaccel)
    return CallProcessor(
        media=media,
        transcriber=WhisperTranscriber(settings.whisper_bin, settings.whisper_model, settings.whisper.dtw_preset),
        detector=SpeakerDetector(media),
        attributor=SpeakerAttributor(),
        library=PeopleLibrary.load(settings.people_dir),
        embedder_for=lambda wav: VoiceEmbedder(wav, settings.voice_model),
        work_root=settings.work_dir,
        results_dir=settings.results_dir,
        notes=ClaudeCodeNotes(settings.claude_bin, settings.notes_model) if notes else None,
        timezone=settings.timezone,
        wishes=Wishes(settings.wishes_path),
    )


def _delivery(settings: Settings) -> SlackDelivery:
    token, channel = settings.require_slack()
    return SlackDelivery(token, channel, settings.slack_notify_user)


def _glossary(settings: Settings, name: str | None) -> str:
    name = name or settings.glossary_name
    return settings.glossary(name) if name else ""


def _process(settings: Settings, args: argparse.Namespace) -> int:
    video = Path(args.video).resolve()
    if not video.is_file():
        raise ConfigError(f"no recording {video}")
    delivery = _delivery(settings) if args.notify else None
    result = _processor(settings, notes=not args.no_notes).process(
        video, _glossary(settings, args.glossary), _names(args.names), learn=args.learn
    )
    print(result.report)
    if result.notes_error:
        print(f"no notes: {result.notes_error}")
    print(f"→ {result.document}")
    if delivery:
        delivery.deliver(result)
        print("→ sent to Slack")
    return 0


def _watch(settings: Settings) -> int:
    delivery = _delivery(settings) if settings.slack_token else None
    if delivery is None:
        log.warning("no SLACK_BOT_TOKEN: results only go to %s", settings.results_dir)
    processor = _processor(settings, notes=True)
    glossary = _glossary(settings, None)
    feedback = None
    if delivery is not None:
        feedback = SlackFeedback(
            delivery.client,
            delivery.channel,
            Wishes(settings.wishes_path),
            settings.data_dir / "slack-feedback.json",
            settings.timezone,
        ).poll
    watcher = Watcher(
        settings.inbox,
        Journal(settings.results_dir / JOURNAL),
        lambda video: processor.process(video, glossary, {}),
        delivery,
        feedback=feedback,
    )
    watcher.run_forever()
    return 0


def _models(settings: Settings, args: argparse.Namespace) -> int:
    """The catalogue, what the settings chose, and what is downloaded."""
    if args.action == "download":
        for model in (settings.whisper, settings.voice):
            download(model, settings.models_dir)
    print(f"chosen: {settings.describe_models()}")
    for setting, catalogue in (("HUDDLE_WHISPER_MODEL", models.WHISPER), ("HUDDLE_VOICE_MODEL", models.VOICE)):
        print(setting)
        for m in catalogue.values():
            state = "downloaded" if (settings.models_dir / m.file).is_file() else "not downloaded"
            print(f"  {m.name}: {m.about} [{state}]")
    print("HUDDLE_NOTES_MODEL")
    for name, about in models.NOTES.items():
        print(f"  {name}: {about}")
    return 0


def _regress(settings: Settings, args: argparse.Namespace) -> int:
    settings.require_voice_model()
    results = run_regression(
        Path(args.dir),
        SpeakerAttributor(),
        lambda wav: VoiceEmbedder(wav, settings.voice_model),
        accept=args.accept,
    )
    for r in results:
        print(f"== {r.name}: {'same' if r.same else 'CHANGED' + (' (accepted)' if args.accept else '')}")
        print(r.report)
        print("\n".join(r.diff[:60]))
    return 0 if args.accept or all(r.same for r in results) else 1


def _names(raw: str) -> dict[int, str]:
    """--names "3=Вася, 5=Петя" (or JSON '{"3": "Вася"}'): avatar group ids to names.
    The plain form survives any shell's quoting, PowerShell's included."""
    try:
        pairs: list[tuple[object, object]] = []
        if raw.strip().startswith("{"):
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("expected an object")
            pairs = list(data.items())
        else:
            for part in filter(str.strip, raw.split(",")):
                key, sep, value = part.partition("=")
                if not sep:
                    raise ValueError('expected "3=Вася, 5=Петя"')
                pairs.append((key, value))
        return {int(str(k)): str(v).strip() for k, v in pairs if str(v).strip()}
    except ValueError as e:
        raise ConfigError(f"--names: {e}") from e


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="huddle-notes", description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--env", default=".env", help="settings file (default: .env in the current folder)")
    p.add_argument("--log", help="also write the log to this file")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("watch", help="process every new recording in the inbox and send the results")
    proc = sub.add_parser("process", help="recording → one document (notes, transcript) in the results folder")
    proc.add_argument("video")
    proc.add_argument("--glossary", help="data/glossaries/<NAME>.txt (default: HUDDLE_GLOSSARY)")
    proc.add_argument("--names", default="", help='name avatar groups by hand, e.g. "3=Вася, 5=Петя"')
    proc.add_argument("--learn", action="store_true", help="remember this call's named avatars and voices")
    proc.add_argument("--notify", action="store_true", help="also send the result to Slack")
    proc.add_argument("--no-notes", action="store_true", help="transcript only, without asking Claude")
    mod = sub.add_parser("models", help="which models are chosen; download fetches and checks them")
    mod.add_argument("action", nargs="?", choices=["download"])
    reg = sub.add_parser("regress", help="check speaker naming against accepted transcripts")
    reg.add_argument("dir")
    reg.add_argument("--accept", action="store_true", help="make the new output the expected one")
    return p


if __name__ == "__main__":
    sys.exit(main())
