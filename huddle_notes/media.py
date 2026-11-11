"""ffmpeg and ffprobe: frames, audio and crops of a recording.

Recordings come from outside, so every call passes arguments as a list (no shell)
and has a time limit.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import numpy as np

from huddle_notes.domain import Crop
from huddle_notes.speakers.tiles import Frame

QUICK = 120  # seconds for a probe, one frame or one crop
LANGUAGE_AT = (0.2, 0.5, 0.8)  # where in a call the pieces of its language sample come from


class MediaError(RuntimeError):
    pass


class Media:
    def __init__(self, hwaccel: str | None = None, audio_timeout: float = 1800) -> None:
        self.hwaccel = hwaccel  # e.g. "videotoolbox" on a Mac; None decodes on the CPU
        self.audio_timeout = audio_timeout

    def size(self, video: Path) -> tuple[int, int]:
        out = self._probe(video, "-select_streams", "v:0", "-show_entries", "stream=width,height")
        w, h = map(int, out.split(","))
        return w, h

    def duration(self, video: Path) -> float:
        return float(self._probe(video, "-show_entries", "format=duration"))

    def recorded_at(self, video: Path) -> datetime | None:
        """When the recording was made, if the file says so (a Mac screen recording does)."""
        tag = self._probe(video, "-show_entries", "format_tags=creation_time")
        try:
            return datetime.fromisoformat(tag) if tag else None
        except ValueError:
            return None

    def frame_at(self, video: Path, t: float, size: tuple[int, int]) -> Frame | None:
        """One full-size frame at t seconds, or None if the video has none there."""
        w, h = size
        cmd = ["ffmpeg", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1"]
        raw = self._run([*cmd, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], QUICK)
        if len(raw) != w * h * 3:
            return None
        return np.frombuffer(raw, np.uint8).reshape(h, w, 3)

    def frames(self, video: Path, fps: int, size: tuple[int, int]) -> Iterator[Frame]:
        """Every frame at fps, scaled to size by nearest neighbour (keeps the 1 px border sharp)."""
        w, h = size
        cmd = ["ffmpeg", "-loglevel", "error"]
        if self.hwaccel:
            cmd += ["-hwaccel", self.hwaccel]
        cmd += ["-i", str(video), "-vf", f"fps={fps},scale={w}:{h}:flags=neighbor"]
        cmd += ["-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
        frame_bytes = w * h * 3
        with subprocess.Popen(cmd, stdout=subprocess.PIPE) as proc:
            if proc.stdout is None:
                raise MediaError("ffmpeg gave no output stream")
            try:
                while len(buf := proc.stdout.read(frame_bytes)) == frame_bytes:
                    yield np.frombuffer(buf, np.uint8).reshape(h, w, 3)
            except BaseException:  # the caller stopped early or failed: don't leave ffmpeg running
                proc.kill()
                raise
            if proc.wait() != 0:
                raise MediaError(f"ffmpeg could not decode {video.name}")

    def extract_audio(self, video: Path, wav: Path) -> None:
        """16 kHz mono, what both whisper and the voice model expect."""
        cmd = ["ffmpeg", "-loglevel", "error", "-y", "-i", str(video), "-vn", "-ar", "16000", "-ac", "1", str(wav)]
        self._run(cmd, self.audio_timeout)

    def language_sample(self, audio: Path, out: Path, length: float, piece: float = 10.0) -> None:
        """Pieces from 20, 50 and 80 % of a call, glued into one short sample: how the call
        sounds once people talk, not the silence while they wait for each other."""
        starts = [max(0.0, min(length * at - piece / 2, length - piece)) for at in LANGUAGE_AT]
        cmd = ["ffmpeg", "-loglevel", "error", "-y"]
        for start in starts:
            cmd += ["-ss", f"{start:.2f}", "-t", f"{piece:.2f}", "-i", str(audio)]
        glue = "".join(f"[{i}:a]" for i in range(len(starts))) + f"concat=n={len(starts)}:v=0:a=1"
        cmd += ["-filter_complex", glue, "-ar", "16000", "-ac", "1", "-t", f"{piece * len(starts):.2f}", str(out)]
        self._run(cmd, QUICK)

    def save_crop(self, video: Path, t: float, crop: Crop, out: Path, width: int = 160) -> None:
        x, y, w, h = crop
        cmd = ["ffmpeg", "-loglevel", "error", "-y", "-ss", str(t), "-i", str(video), "-frames:v", "1"]
        self._run([*cmd, "-vf", f"crop={w}:{h}:{x}:{y},scale={width}:-1", str(out)], QUICK)

    def save_marked(self, video: Path, t: float, crop: Crop, out: Path, width: int = 1280) -> None:
        """The whole frame at t with the crop outlined in red: who it is, among everyone."""
        x, y, w, h = crop
        box = f"drawbox=x={x}:y={y}:w={w}:h={h}:color=red:t={max(4, w // 40)}"
        cmd = ["ffmpeg", "-loglevel", "error", "-y", "-ss", str(t), "-i", str(video), "-frames:v", "1"]
        self._run([*cmd, "-vf", f"{box},scale={width}:-2", str(out)], QUICK)

    def _probe(self, video: Path, *args: str) -> str:
        out = self._run(["ffprobe", "-v", "error", *args, "-of", "csv=p=0", str(video)], QUICK)
        return out.decode().strip()

    @staticmethod
    def _run(cmd: list[str], timeout: float) -> bytes:
        try:
            return subprocess.run(cmd, capture_output=True, check=True, timeout=timeout).stdout
        except subprocess.CalledProcessError as e:
            raise MediaError(f"{cmd[0]} failed: {e.stderr.decode(errors='replace').strip()[:500]}") from e
        except subprocess.TimeoutExpired as e:
            raise MediaError(f"{cmd[0]} took longer than {timeout:.0f} s") from e
