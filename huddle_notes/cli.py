"""huddle-notes: named transcripts of Slack huddle recordings.

  huddle-notes process VIDEO [--glossary NAME] [--names JSON] [--learn]
  huddle-notes regress DIR [--accept]

This is also where the objects are put together; nothing else reads the environment.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from huddle_notes.attribution import SpeakerAttributor
from huddle_notes.config import ConfigError, Settings
from huddle_notes.media import Media
from huddle_notes.people import PeopleLibrary
from huddle_notes.pipeline import CallProcessor
from huddle_notes.regression import run_regression
from huddle_notes.speakers.detector import SpeakerDetector
from huddle_notes.transcribe import WhisperTranscriber
from huddle_notes.voices import VoiceEmbedder

log = logging.getLogger("huddle_notes")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings.from_env()
    try:
        if args.command == "process":
            return _process(settings, args)
        return _regress(settings, args)
    except ConfigError as e:
        log.error("%s", e)
        return 2


def _process(settings: Settings, args: argparse.Namespace) -> int:
    settings.require_transcription()
    video = Path(args.video).resolve()
    if not video.is_file():
        raise ConfigError(f"no recording {video}")
    media = Media(settings.hwaccel)
    processor = CallProcessor(
        media=media,
        transcriber=WhisperTranscriber(settings.whisper_bin, settings.whisper_model),
        detector=SpeakerDetector(media),
        attributor=SpeakerAttributor(),
        library=PeopleLibrary.load(settings.people_dir),
        embedder_for=lambda wav: VoiceEmbedder(wav, settings.voice_model),
    )
    result = processor.process(
        video, settings.glossary(args.glossary) if args.glossary else "", _names(args.names), learn=args.learn
    )
    print(result.report)
    print(f"→ {result.transcript}")
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
    """--names '{"3": "Вася"}': avatar group ids from speakers.json to names."""
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("expected an object")
        return {int(k): str(v).strip() for k, v in data.items() if str(v).strip()}
    except ValueError as e:
        raise ConfigError(f"--names: {e}") from e


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="huddle-notes", description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    proc = sub.add_parser("process", help="recording → Расшифровка.md next to it")
    proc.add_argument("video")
    proc.add_argument("--glossary", help="data/glossaries/<NAME>.txt: the team's names and terms for whisper")
    proc.add_argument("--names", default="{}", help='name avatar groups by hand, e.g. \'{"3": "Вася"}\'')
    proc.add_argument("--learn", action="store_true", help="remember this call's named avatars and voices")
    reg = sub.add_parser("regress", help="check speaker naming against accepted transcripts")
    reg.add_argument("dir")
    reg.add_argument("--accept", action="store_true", help="make the new output the expected one")
    return p


if __name__ == "__main__":
    sys.exit(main())
