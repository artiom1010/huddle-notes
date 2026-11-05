"""All settings, read from the environment (on a server: the .env file) and checked
before any work starts. .env.example lists every one of them with an explanation.
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
from huddle_notes.models import ModelFile, WhisperModel

GLOSSARY_NAME = re.compile(r"^[\w-]{1,64}$")  # a file name, never a path


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    models_dir: Path
    whisper: WhisperModel
    voice: ModelFile
    notes_model: str
    timezone: ZoneInfo
    whisper_bin: str = "whisper-cli"
    hwaccel: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> Settings:
        data = Path(env.get("HUDDLE_DATA_DIR", "data")).expanduser()
        notes = env.get("HUDDLE_NOTES_MODEL", "claude-opus-5-5")
        _pick("HUDDLE_NOTES_MODEL", notes, models.NOTES)
        return cls(
            data_dir=data,
            models_dir=Path(env.get("HUDDLE_MODELS_DIR") or data / "models").expanduser(),
            whisper=_pick("HUDDLE_WHISPER_MODEL", env.get("HUDDLE_WHISPER_MODEL", "large-v3-turbo"), models.WHISPER),
            voice=_pick("HUDDLE_VOICE_MODEL", env.get("HUDDLE_VOICE_MODEL", "wespeaker-resnet34"), models.VOICE),
            notes_model=notes,
            whisper_bin=env.get("HUDDLE_WHISPER_BIN", "whisper-cli"),
            hwaccel=env.get("HUDDLE_HWACCEL") or None,
            timezone=_zone(env.get("HUDDLE_TIMEZONE", "UTC")),
        )

    def describe_models(self) -> str:
        return f"whisper {self.whisper.name}, voices {self.voice.name}, notes {self.notes_model}"

    @property
    def people_dir(self) -> Path:
        return self.data_dir / "people"

    @property
    def whisper_model(self) -> Path:
        return self.models_dir / self.whisper.file

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


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise ConfigError(f"HUDDLE_TIMEZONE={name!r}: not a time zone, e.g. Europe/Chisinau") from e


def _require_file(path: Path, what: str) -> None:
    if not path.is_file():
        raise ConfigError(f"missing {what}: {path} (run: huddle-notes models download)")
