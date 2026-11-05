"""Synthetic calls: frames with a lit tile, groups, words and voices, no recordings needed."""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

from huddle_notes.domain import SpeakerGroup, Vector, Word
from huddle_notes.speakers.tiles import Frame

BACKGROUND = (30, 30, 30)
WHITE = (255, 255, 255)
GREEN = (40, 200, 60)


def frame(
    tile: tuple[int, int, int, int] | None = (100, 100, 300, 250),
    size: tuple[int, int] = (960, 540),
    avatar: tuple[int, int, int] = (90, 60, 120),
    border: bool = True,
    pill: bool = True,
    bars: bool = True,
) -> Frame:
    """A dark huddle window with one avatar tile; lit like Slack's active speaker by default."""
    w, h = size
    fr = np.full((h, w, 3), BACKGROUND, np.uint8)
    if tile is None:
        return fr
    x0, y0, x1, y1 = tile
    fr[y0 : y1 + 1, x0 : x1 + 1] = avatar
    if border:
        fr[y0, x0 : x1 + 1] = fr[y1, x0 : x1 + 1] = WHITE
        fr[y0 : y1 + 1, x0] = fr[y0 : y1 + 1, x1] = WHITE
    if pill:
        fr[y1 - 16 : y1 - 3, x0 + 4 : x0 + 23] = WHITE
        if bars:
            for x in (x0 + 8, x0 + 12, x0 + 16):
                fr[y1 - 12 : y1 - 7, x] = GREEN
    return fr


def group(gid: int, segments: list[tuple[float, float]], fingerprint: float = 0.0, frames: int = 40) -> SpeakerGroup:
    return SpeakerGroup(
        id=gid,
        frames=frames,
        talk_seconds=sum(b - a for a, b in segments),
        sample_t=segments[0][0],
        crop=(0, 0, 10, 10),
        fingerprint=[fingerprint] * 192,
        segments=segments,
    )


def words(start: float, end: float, step: float = 0.5, text: str = "слово") -> list[Word]:
    """Evenly spaced words from start to end."""
    out = []
    t = start
    while t + step <= end + 1e-9:
        out.append(Word(t, t + step * 0.8, text))
        t += step
    return out


def unit(*xs: float) -> Vector:
    v = np.array(xs, np.float32)
    return v / np.linalg.norm(v)  # type: ignore[no-any-return]


class FakeEmbedder:
    """The voice heard in the middle of a stretch, from a function of time."""

    def __init__(self, voice_at: Callable[[float], Vector | None]) -> None:
        self.voice_at = voice_at
        self.calls = 0

    def embed(self, start: float, end: float) -> Vector | None:
        self.calls += 1
        return self.voice_at((start + end) / 2)
