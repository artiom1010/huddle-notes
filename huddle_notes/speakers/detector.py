"""Who is speaking when, from the video: lit tiles grouped into people by avatar.

Naming the groups is attribution's job; here a group is just "the same picture".
A tile counts only when it stands still: while the window opens, closes or reshuffles,
tiles move between frames, and a tile caught moving is cut in the wrong place.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from itertools import pairwise
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
STEADY = 2  # px (processing scale) a tile may shift between frames and still stand still

Hit = tuple[int, Tile, NDArray[np.float32]]  # frame, tile, avatar fingerprint


@dataclass
class _Group:
    centre: NDArray[np.floating[Any]]
    hits: list[tuple[int, Tile]]
    count: int = 1


class SpeakerDetector:
    def __init__(self, media: Media, fps: int = FPS) -> None:
        self.media = media
        self.fps = fps

    def detect(self, video: Path) -> Detection:
        width, height = self.media.size(video)
        k = self.calibrate(video, (width, height))
        size = (width // k // 2 * 2, height // k // 2 * 2)
        hits: list[Hit] = []
        n = 0
        for n, fr in enumerate(self.media.frames(video, self.fps, size), start=1):
            hits += [(n - 1, tile, fingerprint(fr, tile)) for tile in speaking_tiles(fr)]
        steady = _steady(hits, near=round(self.fps * BRIDGE))
        groups = self._group(steady)
        log.info(
            "frames %d, detections %d (%d moving, dropped), avatar groups %d",
            n,
            len(hits),
            len(hits) - len(steady),
            len(groups),
        )
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
    def _group(hits: list[Hit]) -> list[_Group]:
        """Detections into people by avatar: online nearest centre, then merge close groups."""
        groups: list[_Group] = []
        for f, tile, fp in hits:
            best = min(groups, key=lambda g: fingerprint_distance(g.centre, fp), default=None)
            if best is not None and fingerprint_distance(best.centre, fp) < SAME:
                best.centre = (best.centre * best.count + fp) / (best.count + 1)
                best.count += 1
                best.hits.append((f, tile))
            else:
                groups.append(_Group(centre=fp, hits=[(f, tile)]))
        merged = True
        while merged:
            merged = False
            for i in range(len(groups)):
                for j in range(i + 1, len(groups)):
                    if fingerprint_distance(groups[i].centre, groups[j].centre) < SAME:
                        a, b = groups[i], groups.pop(j)
                        a.centre = (a.centre * a.count + b.centre * b.count) / (a.count + b.count)
                        a.count += b.count
                        a.hits += b.hits
                        merged = True
                        break
                if merged:
                    break
        return sorted(groups, key=lambda g: min(f for f, _ in g.hits))

    def _describe(self, group_id: int, g: _Group, k: int) -> SpeakerGroup:
        frames = sorted({f for f, _ in g.hits})
        segments, start, last = [], frames[0], frames[0]
        for f in frames[1:]:
            if f - last > self.fps * BRIDGE:
                segments.append((start / self.fps, (last + 1) / self.fps))
                start = f
            last = f
        segments.append((start / self.fps, (last + 1) / self.fps))
        sample_frame, tile = _sample(g.hits)
        x0, y0, x1, y1 = (v * k for v in tile)
        return SpeakerGroup(
            id=group_id,
            frames=len(frames),
            talk_seconds=round(sum(e - s for s, e in segments), 1),
            sample_t=round(sample_frame / self.fps, 2),
            crop=(x0, y0, x1 - x0, y1 - y0),
            fingerprint=[round(float(v), 1) for v in g.centre],
            segments=[(round(s, 2), round(e, 2)) for s, e in segments],
        )


def _same_place(a: Tile, b: Tile) -> bool:
    return all(abs(p - q) <= STEADY for p, q in zip(a, b, strict=True))


def _steady(hits: list[Hit], near: int) -> list[Hit]:
    """Detections whose tile is in the same place in another frame close by. A tile on a
    moving window is somewhere else in every frame; a speaker whose indicator flickers, or
    who alternates with someone, is lit every other frame but always in one place."""
    tiles_at: dict[int, list[Tile]] = defaultdict(list)
    for f, tile, _ in hits:
        tiles_at[f].append(tile)
    return [
        h
        for h in hits
        if any(_same_place(h[1], t) for d in range(1, near + 1) for f in (h[0] - d, h[0] + d) for t in tiles_at[f])
    ]


def _sample(hits: list[tuple[int, Tile]]) -> tuple[int, Tile]:
    """The frame to show the group by: the middle of its longest stretch with the tile in
    one place, where the picture is surely the settled tile and not a frame in motion."""
    ordered = sorted(hits)
    best: list[tuple[int, Tile]] = []
    run = ordered[:1]
    for prev, cur in pairwise(ordered):
        if cur[0] - prev[0] == 1 and _same_place(cur[1], prev[1]):
            run.append(cur)
        else:
            best, run = max(best, run, key=len), [cur]
    best = max(best, run, key=len)
    return best[len(best) // 2]
