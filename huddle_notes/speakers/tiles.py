"""Find the tiles of people speaking in one frame of a Slack huddle window.

Slack marks the active speaker with a thin white border around their tile and a
white pill with green level bars in the tile's bottom-left corner. Tiles are
reshuffled all the time (join order, raised hands, people joining or leaving,
screen share), so no position is trusted: every frame is searched for that
corner and the tile is traced along its border.

Sizes below are pixels at processing scale, where the pill is ~18x14 px.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

Frame = NDArray[np.uint8]  # height x width x RGB
Mask = NDArray[np.bool_]
Tile = tuple[int, int, int, int]  # x0, y0, x1, y1

RUN = 24  # straight border length we require
GAP = 3  # px outside the border that must be background, not white
THUMB = 8  # avatar fingerprint: THUMB x THUMB colour blocks
TALL = 1.25  # height / width above which a traced tile is wrong: Slack's tiles are about square


def shift(a: Mask, dy: int, dx: int) -> Mask:
    """b[y, x] = a[y + dy, x + dx], False outside the frame."""
    b = np.zeros_like(a)
    h, w = a.shape
    b[max(0, -dy) : h - max(0, dy), max(0, -dx) : w - max(0, dx)] = a[
        max(0, dy) : h - max(0, -dy), max(0, dx) : w - max(0, -dx)
    ]
    return b


def vrun(m: Mask) -> Mask:
    """True where m holds for RUN pixels straight up, ending here."""
    c = np.cumsum(m, axis=0, dtype=np.int32)
    out = np.zeros_like(m)
    out[RUN:] = (c[RUN:] - c[:-RUN]) == RUN
    return out


def hrun(m: Mask) -> Mask:
    """True where m holds for RUN pixels straight right, starting here."""
    c = np.cumsum(m, axis=1, dtype=np.int32)
    out = np.zeros_like(m)
    out[:, 1 : -RUN + 1] = (c[:, RUN:] - c[:, :-RUN]) == RUN
    return out


def run_length(line: Mask, max_gap: int = 4) -> int:
    """Length of a border line from its start, stepping over short breaks
    (compression, a light shirt next to the border) but not the gap between tiles."""
    idx = np.flatnonzero(line)
    if not len(idx) or idx[0] > max_gap:
        return 0
    breaks = np.flatnonzero(np.diff(idx) > max_gap)
    return int(idx[breaks[0]] if len(breaks) else idx[-1]) + 1


def fingerprint(fr: Frame, tile: Tile) -> NDArray[np.float32]:
    """Average colours of the avatar's middle, ignoring labels and the pill."""
    x0, y0, x1, y1 = tile
    w, h = x1 - x0, y1 - y0
    crop = fr[y0 + int(h * 0.2) : y1 - int(h * 0.25), x0 + int(w * 0.2) : x1 - int(w * 0.2)].astype(np.float32)
    ys = np.linspace(0, crop.shape[0], THUMB + 1).astype(int)
    xs = np.linspace(0, crop.shape[1], THUMB + 1).astype(int)
    blocks = [crop[ys[i] : ys[i + 1], xs[j] : xs[j + 1]].mean(axis=(0, 1)) for i in range(THUMB) for j in range(THUMB)]
    return np.array(blocks).ravel()


def fingerprint_distance(a: object, b: object) -> float:
    """How different two avatar fingerprints are (arrays, or lists read from JSON)."""
    d = np.asarray(a) - np.asarray(b)
    return float(np.sqrt((d**2).mean()))


def speaking_tiles(fr: Frame) -> list[Tile]:
    """Tiles with a speaker border and a green pill."""
    white = (fr > 200).all(axis=2)
    r, g, b = (fr[..., i].astype(np.int16) for i in range(3))
    green = (g > 110) & (g > r + 40) & (g > b + 15)
    vw, hw = vrun(white), hrun(white)
    vd, hd = vrun(~white), hrun(~white)
    # a thin white line with background just outside it: the left and bottom border
    left = vw & shift(vd, 0, -GAP)
    bottom = hw & shift(hd, GAP, 0)
    left_ok = np.zeros_like(white)
    bottom_ok = np.zeros_like(white)
    for d in range(3, 11):  # the corner itself is rounded
        left_ok |= shift(left, -d, 0)
        bottom_ok |= shift(bottom, 0, d)
    bright = (fr > 170).all(axis=2)
    # light huddle background (black margins around a window capture don't count):
    # trace only the border itself, which is pure white
    if bright.sum() > 0.25 * (fr >= 30).any(axis=2).sum():
        bright = white
    tiles: list[Tile] = []
    for cy, cx in np.argwhere(left_ok & bottom_ok):
        # one tile, several corner points (a thick border or a large rounded corner)
        if any(t[0] - 8 <= cx <= t[2] and t[1] <= cy <= t[3] + 8 for t in tiles):
            continue
        pill = (slice(max(0, cy - 17), cy - 3), slice(cx + 3, cx + 23))
        if white[pill].mean() < 0.35 or green[pill].sum() < 2:
            continue
        top = cy - 8 - run_length(bright[: cy - 8, cx : cx + 2].any(axis=1)[::-1]) - 6
        # width from both the bottom and the top border: a logo or shirt at one edge can break a line
        runs = (
            run_length(bright[cy - 1 : cy + 2, cx + 8 :].any(axis=0)),
            run_length(bright[max(0, top - 1) : top + 7, cx + 8 :].any(axis=0)),
        )
        right = _right(cx, cy, top, runs)
        if cy - top > TALL * (right - cx):
            # taller than any tile: the left border ran on through something white above it
            # (a neighbour's white photo), or a tooltip over the bottom border cut the width
            own = _own_top(white, bright, int(cx), int(cy), int(top))
            if own is None:  # no top border of its own: not a tile we can trust
                continue
            top, right = own
        if right - cx > 2 * RUN and cy - top > 2 * RUN:
            tiles.append((int(cx), int(top), int(right), int(cy)))
    return tiles


def _right(cx: int, cy: int, top: int, runs: tuple[int, int]) -> int:
    """Right edge from the bottom and top border lengths; the shorter one when the longer
    makes the tile too wide (the speaker's glow or a light photo bridged into the next tile)."""
    right = cx + 8 + 6 + max(runs)
    if right - cx > 1.3 * (cy - top):
        right = cx + 8 + 6 + min(runs)
    return right


def _own_top(white: Mask, bright: Mask, cx: int, cy: int, top: int) -> tuple[int, int] | None:
    """The tile's own top border: going up from the bottom, the first white line about as
    wide as the bottom border with background just above it (a white photo has more white
    above). With it, the top and the width it gives; None when there is no such line.
    The width to match comes from the bottom border alone: the top run measured at a wrong
    top belongs to whatever the trace ran into."""
    bottom = run_length(bright[cy - 1 : cy + 2, cx + 8 :].any(axis=0))
    need = max(2 * RUN, int(0.8 * bottom))  # a tile is never narrower than 2 * RUN
    for y in range(cy - 2 * RUN, top + 5, -1):  # down to the traced top border itself, at top + 6
        run = run_length(white[y : y + 2, cx + 8 :].any(axis=0))  # the line's top row is y
        if run < need or (y >= GAP and white[y - GAP, cx + 8 : cx + 8 + run].mean() > 0.2):
            continue
        return y - 6, _right(cx, cy, y - 6, (bottom, run))
    return None
