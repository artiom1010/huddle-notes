import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from conftest import FakeEmbedder, group, unit

from huddle_notes.attribution import SpeakerAttributor
from huddle_notes.config import Settings
from huddle_notes.domain import Detection
from huddle_notes.media import Media
from huddle_notes.notes import NotesError
from huddle_notes.people import PeopleLibrary
from huddle_notes.pipeline import CallProcessor, _face_file, _safe_name
from huddle_notes.regression import run_regression
from huddle_notes.speakers.detector import SpeakerDetector
from huddle_notes.transcribe import WhisperTranscriber
from huddle_notes.voices import VoiceEmbedder

NOTES = "# Планёрка\n### Краткое содержание\nНачали планёрку.\n### Следующие шаги\n- [ ] **Борис:** давать"


class Forbidden:
    """Stands in for a slow step that must not run again."""

    def __getattr__(self, name: str) -> None:
        raise AssertionError(f"{name} was called although its result was already there")


class RecordedAt(Forbidden):
    """Media that only knows when the recording was made."""

    def recorded_at(self, video: Path) -> datetime:
        return datetime(2026, 11, 5, 8, 1, tzinfo=UTC)


class FakeNotes:
    model = "claude-test"

    def __init__(self, answer: str | Exception = NOTES) -> None:
        self.answer = answer
        self.calls = 0

    def write(self, transcript: str, glossary: str) -> str:
        self.calls += 1
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


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


def finished_steps(tmp_path: Path) -> tuple[Path, CallProcessor, FakeNotes]:
    """A recording whose slow steps (audio, whisper, video) are already done."""
    video = tmp_path / "Записи" / "call.mov"
    video.parent.mkdir()
    video.write_bytes(b"")
    work = tmp_path / "data" / "work" / "call"
    work.mkdir(parents=True)
    (work / "audio.wav").write_bytes(b"")
    segments = [(0.5, 1.6, [(0.5, "Начнём"), (1.0, "планёрку.")]), (6.5, 7.6, [(6.5, "Да,"), (7.0, "давайте.")])]
    (work / "words.json").write_text(json.dumps(whisper_json(segments), ensure_ascii=False), encoding="utf-8")
    detection = Detection("", 4, 40, (960, 540), [group(1, [(0, 2)], 0.0), group(2, [(6, 8)], 100.0)])
    (work / "speakers.json").write_text(json.dumps(detection.to_dict()), encoding="utf-8")
    notes = FakeNotes()
    processor = CallProcessor(
        media=RecordedAt(),  # type: ignore[arg-type]
        transcriber=Forbidden(),  # type: ignore[arg-type]
        detector=Forbidden(),  # type: ignore[arg-type]
        attributor=SpeakerAttributor(),
        library=PeopleLibrary(tmp_path / "people", {"Аня": [[0.0] * 192], "Борис": [[100.0] * 192]}, {}),
        embedder_for=lambda wav: FakeEmbedder(lambda t: unit(1, 0)),
        work_root=tmp_path / "data" / "work",
        results_dir=tmp_path / "Результаты",
        notes=notes,
        timezone=ZoneInfo("Europe/Chisinau"),
    )
    return video, processor, notes


def test_one_document_with_notes_lands_in_the_results_folder(tmp_path):
    video, processor, _ = finished_steps(tmp_path)
    result = processor.process(video, "глоссарий", {})

    assert result.document == tmp_path / "Результаты" / "2026-11-05 10-01.md"
    assert result.document.read_text(encoding="utf-8") == (
        "# Планёрка\n\n"
        "5 ноября 2026, 10:01 · 1 мин · Участники: Аня, Борис\n\n"
        "## Заметки\n\n### Краткое содержание\nНачали планёрку.\n### Следующие шаги\n- [ ] **Борис:** давать\n\n"
        "## Расшифровка\n\n"
        "[00:00:00] **Аня:** Начнём планёрку.\n\n[00:00:06] **Борис:** Да, давайте.\n\n"
    )
    assert "by tile 100.0%" in result.report
    assert [f.name for f in (tmp_path / "Записи").iterdir()] == ["call.mov"]  # the inbox stays clean


def test_the_same_transcript_never_asks_claude_twice(tmp_path):
    video, processor, notes = finished_steps(tmp_path)
    processor.process(video, "глоссарий", {})
    processor.process(video, "глоссарий", {})
    assert notes.calls == 1
    processor.process(video, "глоссарий", {2: "Вася"})  # a renamed speaker changes the transcript
    assert notes.calls == 2


def test_another_recording_from_the_same_minute_gets_its_own_file(tmp_path):
    video, processor, _ = finished_steps(tmp_path)
    work = tmp_path / "data" / "work"
    shutil.copytree(work / "call", work / "call-2")  # a second recording, same start minute
    other = video.with_name("call-2.mov")
    other.write_bytes(b"")
    first = processor.process(video, "", {}).document
    second = processor.process(other, "", {}).document
    assert (first.name, second.name) == ("2026-11-05 10-01.md", "2026-11-05 10-01 (2).md")
    assert processor.process(other, "", {}).document == second  # again: the same file, overwritten
    assert first.exists()


def test_without_notes_the_call_still_gets_its_transcript(tmp_path):
    video, processor, notes = finished_steps(tmp_path)
    notes.answer = NotesError("Claude did not answer in 900 s")
    result = processor.process(video, "", {})
    assert result.notes is None
    assert result.notes_error == "Claude did not answer in 900 s"
    text = result.document.read_text(encoding="utf-8")
    assert text.startswith("# Звонок 5 ноября 2026, 10:01") and "## Заметки" not in text and "## Расшифровка" in text


def test_a_failed_step_leaves_no_half_written_result(tmp_path):
    class BrokenMedia(Media):
        def extract_audio(self, video: Path, wav: Path) -> None:
            wav.write_bytes(b"half")
            raise RuntimeError("ffmpeg died")

    video = tmp_path / "call.mov"
    video.write_bytes(b"")
    processor = CallProcessor(
        BrokenMedia(),
        WhisperTranscriber("whisper-cli", Path("m.bin"), "large.v3.turbo"),
        SpeakerDetector(BrokenMedia()),
        SpeakerAttributor(),
        PeopleLibrary(tmp_path, {}, {}),
        lambda wav: FakeEmbedder(lambda t: None),
        work_root=tmp_path / "work",
        results_dir=tmp_path / "out",
    )
    with pytest.raises(RuntimeError):
        processor.process(video, "", {})
    assert not (tmp_path / "work" / "call" / "audio.wav").exists()


def test_a_typed_name_never_becomes_a_path(tmp_path):
    assert _face_file(tmp_path, "../../etc/x") == tmp_path / "etc_x.jpg"
    assert _face_file(tmp_path, "Вася Пупкин") == tmp_path / "Вася Пупкин.jpg"
    assert _face_file(tmp_path, "..") == tmp_path / "unnamed.jpg"
    assert _safe_name("../2026-11-09 10-01") == "2026-11-09 10-01"
    assert _safe_name("..") == "unnamed"


def test_a_regression_case_missing_a_file_fails_the_check(tmp_path):
    (tmp_path / "call").mkdir()
    (tmp_path / "call" / "words.json").write_text("{}")
    with pytest.raises(FileNotFoundError, match="lacks"):
        run_regression(tmp_path, SpeakerAttributor(), lambda wav: FakeEmbedder(lambda t: None))


REGRESSION = os.environ.get("HUDDLE_REGRESSION_DIR")


@pytest.mark.skipif(not REGRESSION, reason="needs the regression calls (HUDDLE_REGRESSION_DIR), kept out of git")
def test_real_calls_are_named_as_accepted():
    settings = Settings.from_env()
    settings.require_voice_model()
    results = run_regression(
        Path(str(REGRESSION)), SpeakerAttributor(), lambda wav: VoiceEmbedder(wav, settings.voice_model)
    )
    assert [(r.name, r.same) for r in results] == [(r.name, True) for r in results]
