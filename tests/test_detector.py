from collections.abc import Iterator
from pathlib import Path

from conftest import frame

from huddle_notes.media import Media
from huddle_notes.speakers.detector import SpeakerDetector
from huddle_notes.speakers.tiles import Frame

ANNA = (100, 100, 300, 250)
BORIS = (600, 200, 800, 350)


class FakeMedia(Media):
    """A 540p window recording, frame by frame: who is lit in each frame at 4 fps."""

    def __init__(self, timeline: list[str]) -> None:
        super().__init__()
        self.timeline = timeline

    def _frame(self, who: str) -> Frame:
        if who == "anna":
            return frame(tile=ANNA, avatar=(90, 60, 120))
        if who == "boris":
            return frame(tile=BORIS, avatar=(200, 150, 40))
        return frame(tile=None)

    def size(self, video: Path) -> tuple[int, int]:
        return 960, 540

    def duration(self, video: Path) -> float:
        return len(self.timeline) / 4

    def frame_at(self, video: Path, t: float, size: tuple[int, int]) -> Frame | None:
        return self._frame(self.timeline[min(int(t * 4), len(self.timeline) - 1)])

    def frames(self, video: Path, fps: int, size: tuple[int, int]) -> Iterator[Frame]:
        assert size == (960, 540)
        for who in self.timeline:
            yield self._frame(who)


def test_groups_detections_by_avatar_and_joins_flicker_into_segments():
    timeline = ["anna"] * 8 + ["-"] * 2 + ["anna"] * 4 + ["-"] * 12 + ["boris"] * 6 + ["anna"] * 4
    detection = SpeakerDetector(FakeMedia(timeline)).detect(Path("call.mov"))

    assert detection.frames == len(timeline)
    assert detection.frame_size == (960, 540)
    anna, boris = detection.groups
    assert (anna.id, boris.id) == (1, 2)
    # a 0.5 s gap is indicator flicker; 3 s of silence starts a new segment
    assert anna.segments == [(0.0, 3.5), (8.0, 9.0)]
    assert boris.segments == [(6.5, 8.0)]
    assert anna.frames == 16
    assert anna.crop == (100, 94, 207, 156)  # x, y, width, height of the traced tile, full scale
    assert round(anna.fingerprint[0]) == 90


def test_picks_the_scale_that_finds_one_speaker():
    detector = SpeakerDetector(FakeMedia(["anna"] * 30))
    assert detector.calibrate(Path("call.mov"), (960, 540)) == 1
