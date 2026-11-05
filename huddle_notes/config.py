"""Settings from the environment, checked before any work starts.

HUDDLE_DATA_DIR     people library and glossaries (default ./data)
HUDDLE_MODELS_DIR   whisper and voice models (default <data>/models)
HUDDLE_WHISPER_BIN  whisper.cpp command line tool (default whisper-cli)
HUDDLE_HWACCEL      ffmpeg hardware decoding, e.g. videotoolbox on a Mac (default: none)
"""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

WHISPER_MODEL = "ggml-large-v3-turbo.bin"
VOICE_MODEL = "wespeaker_en_voxceleb_resnet34.onnx"
GLOSSARY_NAME = re.compile(r"^[\w-]{1,64}$")  # a file name, never a path


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    models_dir: Path
    whisper_bin: str = "whisper-cli"
    hwaccel: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> Settings:
        data = Path(env.get("HUDDLE_DATA_DIR", "data")).expanduser()
        return cls(
            data_dir=data,
            models_dir=Path(env.get("HUDDLE_MODELS_DIR", str(data / "models"))).expanduser(),
            whisper_bin=env.get("HUDDLE_WHISPER_BIN", "whisper-cli"),
            hwaccel=env.get("HUDDLE_HWACCEL") or None,
        )

    @property
    def people_dir(self) -> Path:
        return self.data_dir / "people"

    @property
    def whisper_model(self) -> Path:
        return self.models_dir / WHISPER_MODEL

    @property
    def voice_model(self) -> Path:
        return self.models_dir / VOICE_MODEL

    def glossary(self, name: str) -> str:
        """The prompt that teaches whisper a team's names and terms (data/glossaries/<name>.txt)."""
        if not GLOSSARY_NAME.match(name):
            raise ConfigError(f"glossary name {name!r}: letters, digits, '_' and '-' only")
        path = self.data_dir / "glossaries" / f"{name}.txt"
        if not path.is_file():
            raise ConfigError(f"no glossary {path}")
        return path.read_text(encoding="utf-8").strip()

    def require_voice_model(self) -> None:
        _require_file(self.voice_model, "voice model (scripts/download-models.sh)")

    def require_transcription(self) -> None:
        self.require_voice_model()
        _require_file(self.whisper_model, "whisper model (scripts/download-models.sh)")
        if shutil.which(self.whisper_bin) is None:
            raise ConfigError(f"{self.whisper_bin} not found: install whisper.cpp or set HUDDLE_WHISPER_BIN")
        for tool in ("ffmpeg", "ffprobe"):
            if shutil.which(tool) is None:
                raise ConfigError(f"{tool} not found")


def _require_file(path: Path, what: str) -> None:
    if not path.is_file():
        raise ConfigError(f"missing {what}: {path}")
