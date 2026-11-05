import json
import os
from pathlib import Path

import pytest
from conftest import FakeEmbedder, group, unit

from huddle_notes.attribution import SpeakerAttributor
from huddle_notes.config import Settings
from huddle_notes.domain import Detection
from huddle_notes.media import Media
from huddle_notes.people import PeopleLibrary
from huddle_notes.pipeline import CallProcessor, _face_file
from huddle_notes.regression import run_regression
from huddle_notes.speakers.detector import SpeakerDetector
from huddle_notes.transcribe import WhisperTranscriber
from huddle_notes.voices import VoiceEmbedder


class Forbidden:
    """Stands in for a slow step that must not run again."""

    def __getattr__(self, name: str) -> None:
        raise AssertionError(f"{name} was called although its result was already there")


def whisper_json(segments: list[tuple[float, float, list[tuple[float, str]]]]) -> dict[str, object]:
    def tokens(words: list[tuple[float, str]]) -> list[dict[str, object]]:
        return [{"text": f" {w}", "offsets": {"from": int(t * 1000), "to": int(t * 1000) + 300}} for t, w in words]

    return {
        "transcription": [
            {
                "text": " ".join(w for _, w in ws),
                "offsets": {"from": int(a * 1000), "to": int(b * 1000)},
                "tokens": tokens(ws),
            }
            for a, b, ws in segments
        ]
    }


def test_picks_up_finished_steps_instead_of_redoing_them(tmp_path):
    video = tmp_path / "call.mov"
    video.write_bytes(b"")
    work = tmp_path / "call" / "_work"
    work.mkdir(parents=True)
    (work / "audio.wav").write_bytes(b"")
    segments = [(0.5, 1.6, [(0.5, "Начнём"), (1.0, "планёрку.")]), (6.5, 7.6, [(6.5, "Да,"), (7.0, "давайте.")])]
    (work / "words.json").write_text(json.dumps(whisper_json(segments), ensure_ascii=False), encoding="utf-8")
    detection = Detection("", 4, 40, (960, 540), [group(1, [(0, 2)], 0.0), group(2, [(6, 8)], 100.0)])
    (work / "speakers.json").write_text(json.dumps(detection.to_dict()), encoding="utf-8")
    library = PeopleLibrary(tmp_path / "people", {"Аня": [[0.0] * 192], "Борис": [[100.0] * 192]}, {})

    processor = CallProcessor(
        media=Forbidden(),  # type: ignore[arg-type]
        transcriber=Forbidden(),  # type: ignore[arg-type]
        detector=Forbidden(),  # type: ignore[arg-type]
        attributor=SpeakerAttributor(),
        library=library,
        embedder_for=lambda wav: FakeEmbedder(lambda t: unit(1, 0)),
    )
    result = processor.process(video, "глоссарий", {})

    assert result.transcript == tmp_path / "call" / "Расшифровка.md"
    assert result.transcript.read_text(encoding="utf-8") == (
        "[00:00:00] **Аня:** Начнём планёрку.\n\n[00:00:06] **Борис:** Да, давайте.\n\n"
    )
    assert "by tile 100.0%" in result.report


def test_a_failed_step_leaves_no_half_written_result(tmp_path):
    class BrokenMedia(Media):
        def extract_audio(self, video: Path, wav: Path) -> None:
            wav.write_bytes(b"half")
            raise RuntimeError("ffmpeg died")

    video = tmp_path / "call.mov"
    video.write_bytes(b"")
    processor = CallProcessor(
        BrokenMedia(),
        WhisperTranscriber("whisper-cli", Path("m.bin")),
        SpeakerDetector(BrokenMedia()),
        SpeakerAttributor(),
        PeopleLibrary(tmp_path, {}, {}),
        lambda wav: FakeEmbedder(lambda t: None),
    )
    with pytest.raises(RuntimeError):
        processor.process(video, "", {})
    assert not (tmp_path / "call" / "_work" / "audio.wav").exists()


REGRESSION = os.environ.get("HUDDLE_REGRESSION_DIR")


@pytest.mark.skipif(not REGRESSION, reason="needs the regression calls (HUDDLE_REGRESSION_DIR), kept out of git")
def test_real_calls_are_named_as_accepted():
    settings = Settings.from_env()
    settings.require_voice_model()
    results = run_regression(
        Path(str(REGRESSION)), SpeakerAttributor(), lambda wav: VoiceEmbedder(wav, settings.voice_model)
    )
    assert [(r.name, r.same) for r in results] == [(r.name, True) for r in results]


def test_a_typed_name_never_becomes_a_path(tmp_path):
    assert _face_file(tmp_path, "../../etc/x") == tmp_path / "etc_x.jpg"
    assert _face_file(tmp_path, "Вася Пупкин") == tmp_path / "Вася Пупкин.jpg"
    assert _face_file(tmp_path, "..") == tmp_path / "unnamed.jpg"


def test_a_regression_case_missing_a_file_fails_the_check(tmp_path):
    (tmp_path / "call").mkdir()
    (tmp_path / "call" / "words.json").write_text("{}")
    with pytest.raises(FileNotFoundError, match="lacks"):
        run_regression(tmp_path, SpeakerAttributor(), lambda wav: FakeEmbedder(lambda t: None))


def test_a_recording_needs_an_extension(tmp_path):
    processor = CallProcessor(Forbidden(), Forbidden(), Forbidden(), SpeakerAttributor(), Forbidden(), Forbidden())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="extension"):
        processor.process(tmp_path / "huddle", "", {})
