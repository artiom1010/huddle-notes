import hashlib
import json
from datetime import datetime

import numpy as np
import pytest
from conftest import unit

from huddle_notes.cli import _names
from huddle_notes.config import ConfigError, Settings
from huddle_notes.domain import Turn
from huddle_notes.models import ModelFile, download
from huddle_notes.people import PeopleLibrary, Sighting
from huddle_notes.render import CallInfo, call_document, hms, participants, transcript_markdown


def test_transcript_lines_carry_time_and_name():
    assert hms(3725.9) == "01:02:05"
    turns = [Turn(5, "Аня", "Начнём."), Turn(3725, "?", "Да.")]
    assert transcript_markdown(turns) == "[00:00:05] **Аня:** Начнём.\n\n[01:02:05] **?:** Да.\n\n"


def test_one_document_per_call_notes_first_then_transcript():
    info = CallInfo(datetime(2026, 11, 5, 10, 1), 2700, ["Аня", "Борис"])
    turns = [Turn(0, "Аня", "Начнём."), Turn(3, "? (Аня или Борис)", "Да."), Turn(5, "Борис", "Давайте.")]
    doc = call_document(info, turns, notes="### Краткое содержание\nПланёрка.")
    assert doc.startswith("# Звонок 5 ноября 2026, 10:01\n\n5 ноября 2026, 10:01 · 45 мин · Участники: Аня, Борис")
    assert doc.index("## Заметки") < doc.index("### Краткое содержание") < doc.index("## Расшифровка")
    assert doc.endswith(transcript_markdown(turns))
    assert "## Заметки" not in call_document(info, turns)
    assert participants(turns) == ["Аня", "Борис"]
    assert info.file_name == "2026-11-05 10-01.md"


def test_models_are_chosen_in_one_place():
    s = Settings.from_env({})
    assert (s.whisper.name, s.voice.name, s.notes_model) == ("large-v3-turbo", "wespeaker-resnet34", "claude-opus-5-5")
    assert s.whisper.dtw_preset == "large.v3.turbo"
    other = Settings.from_env({"HUDDLE_WHISPER_MODEL": "large-v3", "HUDDLE_NOTES_MODEL": "claude-sonnet-5-5"})
    assert other.whisper_model.name == "ggml-large-v3.bin"
    assert other.whisper.dtw_preset == "large.v3"
    with pytest.raises(ConfigError, match="choose one of large-v3-turbo, large-v3"):
        Settings.from_env({"HUDDLE_WHISPER_MODEL": "tiny"})
    with pytest.raises(ConfigError, match="HUDDLE_TIMEZONE"):
        Settings.from_env({"HUDDLE_TIMEZONE": "Марс/Олимп"})


def test_whisper_finds_the_language_itself_unless_one_is_set():
    s = Settings.from_env({})
    assert s.whisper_language == "auto" and "language auto" in s.describe_models()
    assert Settings.from_env({"HUDDLE_WHISPER_LANGUAGE": " RU "}).whisper_language == "ru"
    assert Settings.from_env({"HUDDLE_WHISPER_LANGUAGE": ""}).whisper_language == "auto"
    assert Settings.from_env({"HUDDLE_WHISPER_LANGUAGE": "yue"}).whisper_language == "yue"
    for wrong in ("russian", "rus", "eng", "xx", "ru en", "-l"):  # whisper would exit 0 and write nothing
        with pytest.raises(ConfigError, match="HUDDLE_WHISPER_LANGUAGE"):
            Settings.from_env({"HUDDLE_WHISPER_LANGUAGE": wrong})


def test_a_downloaded_model_is_used_only_if_its_checksum_matches(tmp_path):
    source = tmp_path / "model.bin"
    source.write_bytes(b"weights")
    good = ModelFile("m", "", "m.bin", source.as_uri(), hashlib.sha256(b"weights").hexdigest())
    assert download(good, tmp_path / "models").read_bytes() == b"weights"
    bad = ModelFile("m", "", "bad.bin", source.as_uri(), "0" * 64)
    with pytest.raises(ValueError, match="checksum"):
        download(bad, tmp_path / "models")
    assert sorted(p.name for p in (tmp_path / "models").iterdir()) == ["m.bin"]


def test_settings_default_to_a_data_folder(tmp_path):
    s = Settings.from_env({"HUDDLE_DATA_DIR": str(tmp_path)})
    assert s.people_dir == tmp_path / "people"
    assert s.whisper_model == tmp_path / "models" / "ggml-large-v3-turbo.bin"
    assert s.hwaccel is None
    assert Settings.from_env({"HUDDLE_HWACCEL": "videotoolbox"}).hwaccel == "videotoolbox"


def test_missing_models_stop_before_any_work(tmp_path):
    with pytest.raises(ConfigError, match="voice model"):
        Settings.from_env({"HUDDLE_DATA_DIR": str(tmp_path)}).require_voice_model()


def test_glossary_is_a_name_never_a_path(tmp_path):
    (tmp_path / "glossaries").mkdir()
    (tmp_path / "glossaries" / "староста.txt").write_text("Планёрка команды.\n", encoding="utf-8")
    s = Settings.from_env({"HUDDLE_DATA_DIR": str(tmp_path)})
    assert s.glossary("староста") == "Планёрка команды."
    for bad in ("../secrets", "a/b", "", "x" * 65):
        with pytest.raises(ConfigError):
            s.glossary(bad)
    with pytest.raises(ConfigError, match="no glossary"):
        s.glossary("нет_такого")


def test_names_from_the_command_line():
    assert _names('{"3": "Вася", "4": " "}') == {3: "Вася"}
    assert _names("3=Вася, 5 = Петя") == {3: "Вася", 5: "Петя"}
    assert _names("") == {}
    for bad in ("[1]", '{"x": "Вася"}', "{", "Вася", "x=Вася"):
        with pytest.raises(ConfigError):
            _names(bad)


def test_library_learns_new_pictures_and_averages_voices_across_calls(tmp_path):
    lib = PeopleLibrary.load(tmp_path)
    lib.learn([Sighting("Аня", [0.0] * 192, [unit(1, 0), unit(1, 0)])])
    lib.learn([Sighting("Аня", [1.0] * 192, [unit(0, 1)])])  # nearly the same picture, a new call
    lib.learn([Sighting("Аня", [90.0] * 192, [])])  # a new picture, no clean voice
    assert lib.avatars["Аня"] == [[0.0] * 192, [90.0] * 192]
    voice = lib.voices["Аня"]
    assert (voice["chunks"], voice["calls"]) == (3, 2)
    assert np.allclose(voice["vector"], unit(2, 1), atol=1e-5)  # earlier calls weigh by their stretches

    lib.save()
    again = PeopleLibrary.load(tmp_path)
    assert again.avatars == lib.avatars
    assert np.allclose(again.voiceprints()["Аня"], unit(2, 1), atol=1e-5)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["avatars.json", "voices.json"]


def test_library_files_stay_valid_json(tmp_path):
    lib = PeopleLibrary.load(tmp_path)
    lib.learn([Sighting("Аня", [0.0] * 192, [unit(1, 0), unit(-1, 0)])])  # stretches that cancel out
    lib.save()
    assert "Аня" not in json.loads((tmp_path / "voices.json").read_text(encoding="utf-8"))
