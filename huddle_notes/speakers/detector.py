"""Who is speaking when, from the video: lit tiles grouped into people by avatar.

Naming the groups is attribution's job; here a group is just "the same picture".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from huddle_notes.domain import Detection, SpeakerGroup
from huddle_notes.media import Media
from huddle_notes.speakers.tiles import Tile, fingerprint, fingerprint_distance, speaking_tiles

log = logging.getLogger(__name__)

FPS = 4
SAME = 20.0  # fingerprint distance under which two detections are one picture
BRIDGE = 1.5  # seconds of indicator flicker joined into one segment
SAMPLES = 24  # frames used to pick the processing scale


@dataclass
class _Group:
    centre: NDArray[np.floating[Any]]
    tile: Tile
    first: int
    count: int = 1
    frames: list[int] = field(default_factory=list)


class SpeakerDetector:
    def __init__(self, media: Media, fps: int = FPS) -> None:
        self.media = media
        self.fps = fps

    def detect(self, video: Path) -> Detection:
        width, height = self.media.size(video)
        k = self.calibrate(video, (width, height))
        size = (width // k // 2 * 2, height // k // 2 * 2)
        hits: list[tuple[int, Tile, NDArray[np.float32]]] = []
        n = 0
        for n, fr in enumerate(self.media.frames(video, self.fps, size), start=1):
            hits += [(n - 1, tile, fingerprint(fr, tile)) for tile in speaking_tiles(fr)]
        groups = self._group(hits)
        log.info("frames %d, detections %d, avatar groups %d", n, len(hits), len(groups))
        return Detection(
            video=str(video.resolve()),
            fps=self.fps,
            frames=n,
            frame_size=(width, height),
            groups=[self._describe(i + 1, g, k) for i, g in enumerate(groups)],
        )

    def calibrate(self, video: Path, size: tuple[int, int], n: int = SAMPLES) -> int:
        """Downscale that brings the speaker pill to the ~18x14 px speaking_tiles expects.
        Frame height alone can't tell: a Retina window capture draws the UI twice as large
        as a 1080p screen capture. So try a few scales on sample frames and keep the one
        that finds a single speaker most often."""
        default = max(1, round(size[1] / 540))
        duration = self.media.duration(video)
        frames = [f for i in range(n) if (f := self.media.frame_at(video, duration * (i + 0.5) / n, size)) is not None]
        scores = {
            k: sum(len(speaking_tiles(np.ascontiguousarray(f[::k, ::k]))) == 1 for f in frames)
            for k in range(max(1, default - 1), default + 4)
        }
        k = max(scores, key=lambda k: (scores[k], -abs(k - default)))
        log.info("scale 1/%d (speakers found on sample frames: %s)", k, scores)
        return k

    @staticmethod
    def _group(hits: list[tuple[int, Tile, NDArray[np.float32]]]) -> list[_Group]:
        """Detections into people by avatar: online nearest centre, then merge close groups."""
        groups: list[_Group] = []
        for f, tile, fp in hits:
            best = min(groups, key=lambda g: fingerprint_distance(g.centre, fp), default=None)
            if best is not None and fingerprint_distance(best.centre, fp) < SAME:
                best.centre = (best.centre * best.count + fp) / (best.count + 1)
                best.count += 1
                best.frames.append(f)
            else:
                groups.append(_Group(centre=fp, tile=tile, first=f, frames=[f]))
        merged = True
        while merged:
            merged = False
            for i in range(len(groups)):
                for j in range(i + 1, len(groups)):
                    if fingerprint_distance(groups[i].centre, groups[j].centre) < SAME:
                        a, b = groups[i], groups.pop(j)
                        a.centre = (a.centre * a.count + b.centre * b.count) / (a.count + b.count)
                        a.count += b.count
                        a.frames += b.frames
                        merged = True
                        break
                if merged:
                    break
        return sorted(groups, key=lambda g: min(g.frames))

    def _describe(self, group_id: int, g: _Group, k: int) -> SpeakerGroup:
        frames = sorted(set(g.frames))
        segments, start, last = [], frames[0], frames[0]
        for f in frames[1:]:
            if f - last > self.fps * BRIDGE:
                segments.append((start / self.fps, (last + 1) / self.fps))
                start = f
            last = f
        segments.append((start / self.fps, (last + 1) / self.fps))
        x0, y0, x1, y1 = (v * k for v in g.tile)
        return SpeakerGroup(
            id=group_id,
            frames=len(frames),
            talk_seconds=round(sum(e - s for s, e in segments), 1),
            sample_t=round(g.first / self.fps, 2),
            crop=(x0, y0, x1 - x0, y1 - y0),
            fingerprint=[round(float(v), 1) for v in g.centre],
            segments=[(round(s, 2), round(e, 2)) for s, e in segments],
        )
