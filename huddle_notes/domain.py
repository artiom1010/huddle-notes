"""What a call is made of: words, people seen speaking on video, and who said what.

Plain data only: no files, processes or network here.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import numpy as np
from numpy.typing import NDArray

Vector = NDArray[np.float32]
Crop = tuple[int, int, int, int]  # x, y, width, height in source pixels


@dataclass(frozen=True)
class Word:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class SpeakerGroup:
    """One avatar seen speaking on video: a person, or one of their pictures."""

    id: int
    frames: int
    talk_seconds: float
    sample_t: float
    crop: Crop
    fingerprint: list[float]
    segments: list[tuple[float, float]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "frames": self.frames,
            "talk_seconds": self.talk_seconds,
            "sample": {"t": self.sample_t, "crop": list(self.crop)},
            "fingerprint": self.fingerprint,
            "segments": [list(s) for s in self.segments],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SpeakerGroup:
        x, y, w, h = d["sample"]["crop"]
        return cls(
            id=d["id"],
            frames=d["frames"],
            talk_seconds=d["talk_seconds"],
            sample_t=d["sample"]["t"],
            crop=(x, y, w, h),
            fingerprint=d["fingerprint"],
            segments=[(a, b) for a, b in d["segments"]],
        )


@dataclass(frozen=True)
class Detection:
    """Everything the video says about who spoke when (speakers.json)."""

    video: str
    fps: int
    frames: int
    frame_size: tuple[int, int]
    groups: list[SpeakerGroup]

    def to_dict(self) -> dict[str, Any]:
        return {
            "video": self.video,
            "fps": self.fps,
            "frames": self.frames,
            "frame_size": list(self.frame_size),
            "people": [g.to_dict() for g in self.groups],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Detection:
        w, h = d["frame_size"]
        return cls(
            video=d.get("video", ""),
            fps=d["fps"],
            frames=d["frames"],
            frame_size=(w, h),
            groups=[SpeakerGroup.from_dict(p) for p in d["people"]],
        )


class Basis(StrEnum):
    """Why a stretch of speech got its speaker."""

    TILE = "tile"
    VOICE = "voice"
    DOUBT = "doubt"


class NamedBy(StrEnum):
    """How an avatar group got its name."""

    MANUAL = "manual"
    AVATAR = "avatar"
    VOICE = "voice"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class GroupName:
    name: str
    by: NamedBy
    detail: str  # human-readable evidence, e.g. "аватарка (расст. 12, след. 31)"
    voice: tuple[str | None, float]  # best voice match for the group and its score


@dataclass(frozen=True)
class Turn:
    start: float
    who: str
    text: str
