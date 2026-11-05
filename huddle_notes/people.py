"""The team's avatars and voices, learned from calls where people were named.

On disk (a data folder, never in git: voiceprints are biometric data):
  avatars.json  {name: [fingerprint, ...]}  one per picture the person has used
  voices.json   {name: {"vector": [...], "chunks": n, "calls": n}}
  faces/        one picture per person, for whoever checks the names
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from huddle_notes.domain import Vector
from huddle_notes.speakers.tiles import fingerprint_distance

log = logging.getLogger(__name__)

NEW_LOOK = 5.0  # a fingerprint this far from all of a person's known ones is a new picture
VOICE_WEIGHT_CAP = 200  # older calls count as at most this many stretches, so voices can drift


@dataclass(frozen=True)
class Sighting:
    """What a call teaches about one person named with confidence."""

    name: str
    fingerprint: list[float]
    voice_stretches: list[Vector]  # this call's clean embeddings of their voice


class PeopleLibrary:
    def __init__(self, root: Path, avatars: dict[str, list[list[float]]], voices: dict[str, dict[str, Any]]) -> None:
        self.root = root
        self.avatars = avatars
        self.voices = voices

    @classmethod
    def load(cls, root: Path) -> PeopleLibrary:
        return cls(root, _read(root / "avatars.json"), _read(root / "voices.json"))

    @property
    def faces(self) -> Path:
        return self.root / "faces"

    def voiceprints(self) -> dict[str, Vector]:
        return {n: np.array(v["vector"], np.float32) for n, v in self.voices.items()}

    def learn(self, sightings: list[Sighting]) -> None:
        for s in sightings:
            looks = self.avatars.setdefault(s.name, [])
            if not looks or min(fingerprint_distance(s.fingerprint, p) for p in looks) > NEW_LOOK:
                looks.append(s.fingerprint)
            if s.voice_stretches:
                old = self.voices.get(s.name, {"vector": None, "chunks": 0, "calls": 0})
                total = np.sum(s.voice_stretches, axis=0)
                if old["vector"] is not None:
                    total = total + np.array(old["vector"]) * min(old["chunks"], VOICE_WEIGHT_CAP)
                if not np.isfinite(total).all() or np.linalg.norm(total) < 1e-6:
                    log.warning("not learning %s's voice: the stretches cancel out", s.name)
                    continue
                self.voices[s.name] = {
                    "vector": [round(float(x), 5) for x in total / np.linalg.norm(total)],
                    "chunks": old["chunks"] + len(s.voice_stretches),
                    "calls": old["calls"] + 1,
                }

    def save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        _write(self.root / "avatars.json", self.avatars)
        _write(self.root / "voices.json", self.voices)
        log.info("library: %d avatars, %d voices", len(self.avatars), len(self.voices))


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _write(path: Path, data: object) -> None:
    """Write next to the old file and swap, so a crash never leaves half a library."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
