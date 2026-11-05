import json

import numpy as np
import pytest
from conftest import unit

from huddle_notes.cli import _names
from huddle_notes.config import ConfigError, Settings
from huddle_notes.domain import Turn
from huddle_notes.people import PeopleLibrary, Sighting
from huddle_notes.render import hms, transcript_markdown


def test_transcript_lines_carry_time_and_name():
    assert hms(3725.9) == "01:02:05"
    turns = [Turn(5, "Аня", "Начнём."), Turn(3725, "?", "Да.")]
    assert transcript_markdown(turns) == "[00:00:05] **Аня:** Начнём.\n\n[01:02:05] **?:** Да.\n\n"


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
    for bad in ("[1]", '{"x": "Вася"}', "{"):
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
