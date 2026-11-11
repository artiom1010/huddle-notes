"""Whisper with VAD: speech only, and every word back at its time in the recording."""

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from huddle_notes import cli
from huddle_notes.config import ConfigError, Settings
from huddle_notes.transcribe import TranscriptionError, WhisperTranscriber, read_words
from huddle_notes.vad import Timeline, restore_times, time_table

# whisper.cpp's own lines (Windows ends them with CRLF): two pieces of speech, 10–12 s and
# 100–101.5 s of the recording, glued with 0.1 s of overlap and 0.1 s of silence between them
LOG = (
    "whisper_vad: detected 2 speech segments\r\n"
    "whisper_vad: vad_segment_info: orig_start: 10.00, orig_end: 12.00, vad_start: 0.00, vad_end: 2.00\r\n"
    "whisper_vad: vad_segment_info: orig_start: 100.00, orig_end: 101.50, vad_start: 2.20, vad_end: 3.70\r\n"
    "whisper_vad: Created time mapping table with 5 points\r\n"
)


def test_the_time_table_is_the_one_whisper_used():
    table = time_table(LOG)
    assert table == [(0, 1000), (200, 1200), (210, 1200), (220, 10000), (370, 10150)]
    to_recording = Timeline(table)
    assert to_recording(100) == 1100  # inside the first piece
    assert to_recording(220) == 10000  # exactly at a point
    assert to_recording(230) == 10010  # 0.1 s into the second piece: 100.1 s
    assert to_recording(-5) == 1000 and to_recording(999) == 10150  # clamped, like whisper
    assert time_table("whisper_full: no VAD\n") == []


def segment(text: str, at_ms: int, original_ms: int, length_ms: int = 500) -> dict[str, Any]:
    """A segment whisper mapped onto the recording, its tokens still in glued time."""
    tokens = [
        {"text": "[_BEG_]", "offsets": {"from": at_ms, "to": at_ms}, "t_dtw": -1},
        {"text": f" {text}", "offsets": {"from": at_ms, "to": at_ms + length_ms}, "t_dtw": at_ms // 10},
    ]
    return {"text": f" {text}", "offsets": {"from": original_ms, "to": original_ms + length_ms}, "tokens": tokens}


def write(tmp_path: Path, segments: list[dict[str, Any]]) -> Path:
    path = tmp_path / "words.json"
    path.write_text(json.dumps({"transcription": segments}, ensure_ascii=False), encoding="utf-8")
    return path


def test_tokens_get_their_times_in_the_recording_but_never_leave_their_segment(tmp_path):
    # glued 2.3 s is 100.1 s of the recording, glued 1.0 s is 11.0 s (all times below in ms)
    path = write(tmp_path, [segment("Привет", 2300, 100100), segment("пока", 1000, 11000)])
    data = json.loads(path.read_text(encoding="utf-8"))
    data["transcription"][0]["tokens"][1]["t_dtw"] = 213  # alignment drifted into the 0.1 s pause (cs)
    data["transcription"][1]["tokens"].append({"text": " без", "t_dtw": 120})  # a token without offsets
    path.write_text(json.dumps(data), encoding="utf-8")
    assert restore_times(path, time_table(LOG))
    first, second = json.loads(path.read_text(encoding="utf-8"))["transcription"]
    assert first["tokens"][1]["offsets"] == {"from": 100100, "to": 100600}
    assert first["tokens"][1]["t_dtw"] == 10010  # not 38.4 s inside the cut pause: its segment's start
    assert second["tokens"][1]["offsets"] == {"from": 11000, "to": 11500}
    assert second["tokens"][2]["t_dtw"] == 1120


def test_tokens_already_in_the_recordings_time_are_left_alone(tmp_path):
    path = write(tmp_path, [segment("Привет", 100100, 100100), segment("пока", 11000, 11000)])
    before = path.read_text(encoding="utf-8")
    assert not restore_times(path, time_table(LOG))  # a whisper that maps its tokens itself
    assert path.read_text(encoding="utf-8") == before


class Whisper:
    """Stands in for whisper-cli: writes the JSON and its log, or fails as told."""

    def __init__(self, runs: list[str | int]) -> None:
        self.runs = runs  # per run: the log to write, or an exit code to fail with
        self.commands: list[list[str]] = []

    def __call__(self, cmd: list[str], stdout: Any, **kw: Any) -> None:
        self.commands.append(cmd)
        run = self.runs[len(self.commands) - 1]
        if isinstance(run, int):
            raise subprocess.CalledProcessError(run, cmd)
        if run == "timeout":
            raise subprocess.TimeoutExpired(cmd, 1)
        stdout.write(run.encode())
        vad = "--vad" in cmd
        # with VAD the token is 0.9 s into the second piece of glued speech, which starts at 1.1 s
        words = [segment("Привет", 2000 if vad else 100900, 100900)] if run else []
        Path(cmd[cmd.index("-of") + 1] + ".json").write_text(json.dumps({"transcription": words}), encoding="utf-8")


TABLE_LOG = (
    "whisper_vad: vad_segment_info: orig_start: 5.00, orig_end: 6.00, vad_start: 0.00, vad_end: 1.00\n"
    "whisper_vad: vad_segment_info: orig_start: 100.00, orig_end: 103.00, vad_start: 1.10, vad_end: 4.10\n"
)


def transcribe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, whisper: Whisper) -> Path:
    monkeypatch.setattr("huddle_notes.transcribe.subprocess.run", whisper)
    w = WhisperTranscriber("whisper-cli", Path("m.bin"), "large.v3.turbo", "ru", vad_model=tmp_path / "vad.bin")
    return w.transcribe(tmp_path / "a.wav", tmp_path / "words", "", tmp_path / "whisper.log")


def test_with_vad_each_word_is_put_back_at_its_time_in_the_recording(tmp_path, monkeypatch):
    whisper = Whisper([TABLE_LOG])
    [word] = read_words(transcribe(tmp_path, monkeypatch, whisper))
    cmd = whisper.commands[0]
    assert cmd[cmd.index("-vm") + 1] == str(tmp_path / "vad.bin") and "-np" not in cmd  # the log keeps its table
    assert word.text == "Привет" and word.start == pytest.approx(100.9, abs=0.01)  # 100 s + 0.9 s


@pytest.mark.parametrize("first", ["whisper of another version\n", 10])  # no table; VAD failed (exit 10)
def test_without_its_table_whisper_hears_the_call_again_without_vad(tmp_path, monkeypatch, caplog, first):
    whisper = Whisper([first, "whisper_full: done\n"])
    [word] = read_words(transcribe(tmp_path, monkeypatch, whisper))
    assert "--vad" in whisper.commands[0] and "--vad" not in whisper.commands[1] and "-np" in whisper.commands[1]
    assert word.start == pytest.approx(100.9, abs=0.01)
    assert "without VAD" in caplog.text
    assert (tmp_path / "whisper.log").read_text() == "whisper_full: done\n"
    if isinstance(first, str):
        assert (tmp_path / "whisper-vad.log").read_text() == first  # the first log is kept


def test_a_whisper_too_slow_with_vad_is_not_run_again(tmp_path, monkeypatch):
    whisper = Whisper(["timeout"])
    with pytest.raises(TranscriptionError, match="longer than"):
        transcribe(tmp_path, monkeypatch, whisper)
    assert len(whisper.commands) == 1


def test_no_speech_at_all_is_no_reason_to_listen_again(tmp_path, monkeypatch):
    whisper = Whisper([""])  # no table, no words
    transcribe(tmp_path, monkeypatch, whisper)
    assert len(whisper.commands) == 1


def test_vad_is_on_unless_switched_off(tmp_path):
    s = Settings.from_env({"HUDDLE_DATA_DIR": str(tmp_path)})
    assert s.vad is not None and s.vad.name == "silero-v5.1.2" and s.vad in s.models_needed
    assert "vad silero-v5.1.2" in s.describe_models()
    assert Settings.from_env({"HUDDLE_VAD_MODEL": " "}).vad == s.vad  # empty: the default, like other settings
    off = Settings.from_env({"HUDDLE_DATA_DIR": str(tmp_path), "HUDDLE_VAD_MODEL": "Off"})
    assert off.vad is None and off.vad_model is None and "vad off" in off.describe_models()
    with pytest.raises(ConfigError, match="HUDDLE_VAD_MODEL"):
        Settings.from_env({"HUDDLE_VAD_MODEL": "silero-v4"})


def test_a_missing_vad_model_costs_the_loops_not_the_watcher(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr("shutil.which", lambda tool: f"/bin/{tool}")
    s = Settings.from_env({"HUDDLE_DATA_DIR": str(tmp_path)})
    s.models_dir.mkdir(parents=True)
    s.whisper_model.write_bytes(b"model")
    assert cli._processor(s, notes=False).transcriber.vad_model is None
    assert "no VAD model" in caplog.text and "models download" in caplog.text
    assert s.vad_model is not None
    s.vad_model.write_bytes(b"vad")
    assert cli._processor(s, notes=False).transcriber.vad_model == s.vad_model


def test_models_lists_the_vad_model(tmp_path, capsys):
    s = Settings.from_env({"HUDDLE_DATA_DIR": str(tmp_path)})
    cli._models(s, type("Args", (), {"action": None})())  # type: ignore[arg-type]
    assert "HUDDLE_VAD_MODEL\n  silero-v5.1.2:" in capsys.readouterr().out
