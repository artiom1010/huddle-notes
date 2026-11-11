"""All settings, read from the environment or a .env file, and checked before any work
starts. .env.example lists every one of them with an explanation.
"""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from huddle_notes import models
from huddle_notes.feedback import WISHES
from huddle_notes.models import ModelFile, WhisperModel
from huddle_notes.transcribe import LANGUAGES

GLOSSARY_NAME = re.compile(r"^[\w-]{1,64}$")  # a file name, never a path


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    models_dir: Path
    whisper: WhisperModel
    vad: ModelFile | None  # None: whisper hears the whole recording, silence too
    voice: ModelFile
    notes_model: str
    timezone: ZoneInfo
    inbox: Path
    results_dir: Path
    glossary_name: str | None = None
    whisper_language: str = "auto"  # detected per recording; a code such as ru forces one
    speakers: bool = False  # name who speaks (tiles, avatars, voices); off: a transcript without names
    whisper_bin: str = "whisper-cli"
    claude_bin: str = "claude"
    hwaccel: str | None = None
    slack_token: str | None = None
    slack_channel: str | None = None
    slack_notify_user: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> Settings:
        data = Path(env.get("HUDDLE_DATA_DIR", "data")).expanduser()
        notes = env.get("HUDDLE_NOTES_MODEL", "claude-opus-5-5")
        _pick("HUDDLE_NOTES_MODEL", notes, models.NOTES)
        return cls(
            data_dir=data,
            models_dir=Path(env.get("HUDDLE_MODELS_DIR") or data / "models").expanduser(),
            whisper=_pick("HUDDLE_WHISPER_MODEL", env.get("HUDDLE_WHISPER_MODEL", "large-v3-turbo"), models.WHISPER),
            vad=_vad(env.get("HUDDLE_VAD_MODEL", "")),
            voice=_pick("HUDDLE_VOICE_MODEL", env.get("HUDDLE_VOICE_MODEL", "wespeaker-resnet34"), models.VOICE),
            notes_model=notes,
            whisper_bin=env.get("HUDDLE_WHISPER_BIN", "whisper-cli"),
            hwaccel=env.get("HUDDLE_HWACCEL") or None,
            timezone=_zone(env.get("HUDDLE_TIMEZONE", "UTC")),
            # «Записи» and «Результаты» sit next to the data folder unless set otherwise
            inbox=Path(env.get("HUDDLE_INBOX") or data.parent / "Записи").expanduser(),
            results_dir=Path(env.get("HUDDLE_RESULTS") or data.parent / "Результаты").expanduser(),
            glossary_name=env.get("HUDDLE_GLOSSARY") or None,
            whisper_language=_language(env.get("HUDDLE_WHISPER_LANGUAGE", "auto")),
            speakers=_switch("HUDDLE_SPEAKERS", env.get("HUDDLE_SPEAKERS", "off")),
            claude_bin=env.get("HUDDLE_CLAUDE_BIN", "claude"),
            slack_token=env.get("SLACK_BOT_TOKEN") or None,
            slack_channel=env.get("SLACK_CHANNEL") or None,
            slack_notify_user=env.get("SLACK_NOTIFY_USER") or None,
        )

    def describe_models(self) -> str:
        speakers = f"voices {self.voice.name}" if self.speakers else "speakers off"
        vad = f"vad {self.vad.name}" if self.vad else "vad off"
        whisper = f"whisper {self.whisper.name}, language {self.whisper_language}, {vad}"
        return f"{whisper}, {speakers}, notes {self.notes_model}"

    @property
    def models_needed(self) -> list[ModelFile]:
        """The model files this setup uses: the voice model only when speakers are named."""
        return [self.whisper, *([self.vad] if self.vad else []), *([self.voice] if self.speakers else [])]

    @property
    def people_dir(self) -> Path:
        return self.data_dir / "people"

    @property
    def wishes_path(self) -> Path:
        """The team's wishes for the notes, from Slack replies and Claude Code."""
        return self.data_dir / WISHES

    @property
    def work_dir(self) -> Path:
        """Each recording's intermediate results (audio, whisper, tiles, notes)."""
        return self.data_dir / "work"

    def require_slack(self) -> tuple[str, str]:
        """The bot token and the channel ID, both needed to send results to Slack."""
        if not (self.slack_token and self.slack_channel):
            raise ConfigError("SLACK_BOT_TOKEN and SLACK_CHANNEL are needed to send results to Slack")
        return self.slack_token, self.slack_channel

    @property
    def whisper_model(self) -> Path:
        return self.models_dir / self.whisper.file

    @property
    def vad_model(self) -> Path | None:
        return self.models_dir / self.vad.file if self.vad else None

    @property
    def voice_model(self) -> Path:
        return self.models_dir / self.voice.file

    def glossary(self, name: str) -> str:
        """The prompt that teaches whisper a team's names and terms (data/glossaries/<name>.txt)."""
        if not GLOSSARY_NAME.match(name):
            raise ConfigError(f"glossary name {name!r}: letters, digits, '_' and '-' only")
        path = self.data_dir / "glossaries" / f"{name}.txt"
        if not path.is_file():
            raise ConfigError(f"no glossary {path}")
        return path.read_text(encoding="utf-8").strip()

    def require_voice_model(self) -> None:
        _require_file(self.voice_model, f"voice model {self.voice.name}")

    def require_transcription(self) -> None:
        if self.speakers:
            self.require_voice_model()
        _require_file(self.whisper_model, f"whisper model {self.whisper.name}")
        if shutil.which(self.whisper_bin) is None:
            raise ConfigError(f"{self.whisper_bin} not found: install whisper.cpp or set HUDDLE_WHISPER_BIN")
        for tool in ("ffmpeg", "ffprobe"):
            if shutil.which(tool) is None:
                raise ConfigError(f"{tool} not found")


def _pick[T](setting: str, name: str, catalogue: Mapping[str, T]) -> T:
    if name not in catalogue:
        raise ConfigError(f"{setting}={name!r}: choose one of {', '.join(catalogue)}")
    return catalogue[name]


def _switch(setting: str, value: str) -> bool:
    on, off = {"on", "true", "1", "yes"}, {"off", "false", "0", "no", ""}
    if value.strip().lower() in on | off:
        return value.strip().lower() in on
    raise ConfigError(f"{setting}={value!r}: on or off")


def _vad(name: str) -> ModelFile | None:
    """The VAD model; empty means the default one, like the other settings, and off none."""
    name = name.strip() or "silero-v5.1.2"
    if name.lower() == "off":
        return None
    return _pick("HUDDLE_VAD_MODEL", name, models.VAD | {"off": None})


def _language(value: str) -> str:
    code = value.strip().lower() or "auto"
    if code != "auto" and code not in LANGUAGES:
        raise ConfigError(f"HUDDLE_WHISPER_LANGUAGE={value!r}: auto, or a language code such as ru or en")
    return code


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise ConfigError(f"HUDDLE_TIMEZONE={name!r}: not a time zone, e.g. Europe/Chisinau") from e


def _require_file(path: Path, what: str) -> None:
    if not path.is_file():
        raise ConfigError(f"missing {what}: {path} (run: huddle-notes models download)")


def load_env_file(path: Path, env: dict[str, str] | None = None) -> None:
    """KEY=VALUE lines into the environment; values already set win. Quotes around a value
    are dropped, '#' starts a comment line. Works the same on Windows, where the shell
    can't source the file."""
    target = os.environ if env is None else env
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        target.setdefault(key, value)
