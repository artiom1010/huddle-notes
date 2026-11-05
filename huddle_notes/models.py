"""Every model the service can use, in one catalogue.

Which one is used is a setting (.env: HUDDLE_WHISPER_MODEL, HUDDLE_VOICE_MODEL,
HUDDLE_NOTES_MODEL). Model files are downloaded with `huddle-notes models download`
and checked against the checksums below, so a broken or tampered file is never used.
"""

from __future__ import annotations

import hashlib
import logging
import urllib.request
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelFile:
    name: str  # what goes into .env
    about: str
    file: str
    url: str
    sha256: str


@dataclass(frozen=True)
class WhisperModel(ModelFile):
    dtw_preset: str  # whisper.cpp's per-word timing must match the model


WHISPER = {
    m.name: m
    for m in (
        WhisperModel(
            "large-v3-turbo",
            "быстрая версия large-v3, почти та же точность (1,6 ГБ)",
            "ggml-large-v3-turbo.bin",
            "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin",
            "1fc70f774d38eb169993ac391eea357ef47c88757ef72ee5943879b7e8e2bc69",
            "large.v3.turbo",
        ),
        WhisperModel(
            "large-v3",
            "самая точная, в несколько раз медленнее (3,1 ГБ)",
            "ggml-large-v3.bin",
            "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3.bin",
            "64d182b440b98d5203c4f9bd541544d84c605196c4f7b845dfa11fb23594d1e2",
            "large.v3",
        ),
    )
}

VOICE = {
    m.name: m
    for m in (
        ModelFile(
            "wespeaker-resnet34",
            "отпечатки голосов (25 МБ)",
            "wespeaker_en_voxceleb_resnet34.onnx",
            "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/wespeaker_en_voxceleb_resnet34.onnx",
            "5ef208a9da1453335308a6b6f4e6dfbd7e183a38b604de0a57664f45d257fe94",
        ),
    )
}

# Claude models for the notes, called through the company account
NOTES = {
    "claude-opus-5-5": "самая сильная, заметки точнее всего",
    "claude-sonnet-5-5": "быстрее и бережнее к лимиту аккаунта",
}


def download(model: ModelFile, folder: Path) -> Path:
    """The model's file in folder, downloaded unless an intact copy is already there."""
    path = folder / model.file
    if path.is_file() and sha256(path) == model.sha256:
        log.info("%s: already there", model.name)
        return path
    folder.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f"{path.name}.partial")
    log.info("%s: downloading %s", model.name, model.url)
    with urllib.request.urlopen(model.url, timeout=60) as response, partial.open("wb") as out:  # noqa: S310 (fixed https URLs)
        while block := response.read(1 << 20):
            out.write(block)
    if sha256(partial) != model.sha256:
        partial.unlink()
        raise ValueError(f"{model.name}: downloaded file does not match its checksum, not used")
    partial.replace(path)
    return path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(1 << 20):
            h.update(block)
    return h.hexdigest()
