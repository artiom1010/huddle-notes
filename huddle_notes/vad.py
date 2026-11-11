"""Where the words heard with whisper.cpp's VAD were said in the recording.

With --vad whisper transcribes only the speech its VAD model found: the pieces glued
together, each with 0.1 s of what follows it, and 0.1 s of silence between them. It maps
the times of its segments back onto the recording, but not those of their tokens, and
each word's time comes from its tokens: off by however much silence was cut before it.
Whisper's log lists every piece it kept ("vad_segment_info: orig_start … vad_end …"); the
table built from it maps the tokens the same way whisper maps the segments
(map_processed_to_original_time in src/whisper.cpp).
"""

from __future__ import annotations

import bisect
import json
import re
from pathlib import Path
from typing import Any

PIECE = re.compile(
    r"vad_segment_info: orig_start: ([\d.]+), orig_end: ([\d.]+), vad_start: ([\d.]+), vad_end: ([\d.]+)"
)
GAP = 10  # centiseconds of silence whisper puts between two pieces of speech
MAPPED = 1000  # ms: tokens this close to their segments' starts are in the recording's time already

Table = list[tuple[int, int]]  # (time in the glued speech, time in the recording), centiseconds


def time_table(log: str) -> Table:
    """The mapping whisper used, from its log; empty if the log has none."""
    pieces = [tuple(round(float(x) * 100) for x in found) for found in PIECE.findall(log)]
    points: Table = []
    for i, (orig_start, orig_end, vad_start, vad_end) in enumerate(pieces):
        points += [(vad_start, orig_start), (vad_end, orig_end)]
        if i + 1 < len(pieces):  # the 0.1 s of silence stands for the whole pause between them
            next_orig, _, next_vad, _ = pieces[i + 1]
            points += [(next_vad - GAP, orig_end), (next_vad, next_orig)]
    table: Table = []
    for point in sorted(points):
        if not table or table[-1][0] != point[0]:
            table.append(point)
    return table


class Timeline:
    """Times in the glued speech → times in the recording, both in centiseconds."""

    def __init__(self, table: Table) -> None:
        self.table = table
        self.glued = [p for p, _ in table]

    def __call__(self, t: int) -> int:
        table = self.table
        if t <= table[0][0]:
            return table[0][1]
        if t >= table[-1][0]:
            return table[-1][1]
        k = bisect.bisect_left(self.glued, t)
        (p0, o0), (p1, o1) = table[k - 1], table[k]
        if p1 == t:
            return o1
        return o0 + (t - p0) * (o1 - o0) // (p1 - p0)


def restore_times(path: Path, table: Table) -> bool:
    """Puts the tokens of whisper's JSON at their times in the recording, never outside their
    segment (whisper's per-word alignment can drift, and the cut pauses would stretch that).
    False, and the file as it was, if the tokens are in the recording's time already."""
    data = json.loads(path.read_text(encoding="utf-8"))
    segments = data.get("transcription", [])
    if _in_recording_time(segments):
        return False
    to_recording = Timeline(table)
    for segment in segments:
        start, end = segment["offsets"]["from"], segment["offsets"]["to"]
        for token in segment.get("tokens", []):
            if "offsets" in token:
                token["offsets"] = {
                    k: min(max(to_recording(ms // 10) * 10, start), end) for k, ms in token["offsets"].items()
                }
            if token.get("t_dtw", -1) >= 0:
                token["t_dtw"] = min(max(to_recording(int(token["t_dtw"])), start // 10), end // 10)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return True


def _in_recording_time(segments: list[dict[str, Any]]) -> bool:
    """Whether every segment's first token starts where the segment does (a whisper that
    maps its tokens itself), rather than earlier by the silence cut before it."""
    firsts = []
    for segment in segments:
        words = [t for t in segment.get("tokens", []) if "offsets" in t and not t["text"].startswith("[_")]
        if words:
            firsts.append((segment["offsets"]["from"], words[0]["offsets"]["from"]))
    return bool(firsts) and all(abs(seg - tok) <= MAPPED for seg, tok in firsts)
