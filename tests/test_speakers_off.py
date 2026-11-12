"""Speakers not named (HUDDLE_SPEAKERS=off, the default): a transcript without names."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_notes_deliver_watch import result
from test_pipeline import FakeNotes, Forbidden, whisper_json

from huddle_notes import cli
from huddle_notes.config import ConfigError, Settings
from huddle_notes.deliver import slack_text
from huddle_notes.domain import Word
from huddle_notes.media import MediaError
from huddle_notes.notes import ClaudeCodeNotes, prompt
from huddle_notes.pipeline import CallProcessor
from huddle_notes.render import CallInfo, call_document, paragraphs


class MediaWithLength(Forbidden):
    """Media that knows when the recording was made and how long it is, and nothing else:
    with speakers off, the video is never decoded."""

    def recorded_at(self, video: Path) -> datetime:
        return datetime(2026, 11, 5, 8, 1, tzinfo=UTC)

    def duration(self, video: Path) -> float:
        return 125.0


def transcribed(tmp_path: Path) -> tuple[Path, CallProcessor, FakeNotes]:
    video = tmp_path / "Записи" / "2026-11-05 10-01-00.mkv"
    video.parent.mkdir()
    video.write_bytes(b"")
    work = tmp_path / "data" / "work" / "2026-11-05 10-01-00"
    work.mkdir(parents=True)
    (work / "audio.wav").write_bytes(b"")
    segments = [(0.5, 1.6, [(0.5, "Начнём"), (1.0, "планёрку.")]), (6.5, 7.6, [(6.5, "Петь,"), (7.0, "начнём.")])]
    (work / "words.json").write_text(json.dumps(whisper_json(segments), ensure_ascii=False), encoding="utf-8")
    notes = FakeNotes()
    processor = CallProcessor(
        media=MediaWithLength(),  # type: ignore[arg-type]
        transcriber=Forbidden(),  # type: ignore[arg-type]
        detector=Forbidden(),  # type: ignore[arg-type]  # never looks at the video
        attributor=Forbidden(),  # type: ignore[arg-type]
        library=Forbidden(),  # type: ignore[arg-type]  # no faces or voices needed
        embedder_for=lambda wav: Forbidden(),  # type: ignore[arg-type, return-value]
        work_root=tmp_path / "data" / "work",
        results_dir=tmp_path / "Результаты",
        notes=notes,
        timezone=UTC,
        speakers=False,
    )
    return video, processor, notes


def test_without_speakers_the_transcript_has_times_and_no_names(tmp_path):
    video, processor, notes = transcribed(tmp_path)
    r = processor.process(video, "словарь", {})
    assert r.attribution is None and r.unknown_faces == [] and r.recording == "2026-11-05 10-01-00.mkv"
    assert r.info.participants == [] and r.info.duration == 125.0
    assert r.document.read_text(encoding="utf-8") == (
        "# Планёрка\n\n"
        "5 ноября 2026, 08:01 · 2 мин\n\n"
        "## Заметки\n\n### Краткое содержание\nНачали планёрку.\n### Следующие шаги\n- [ ] **Борис:** давать\n\n"
        "## Расшифровка\n\n"
        "[00:00:00] Начнём планёрку.\n\n[00:00:06] Петь, начнём.\n\n"
    )
    assert notes.calls == 1
    assert not (tmp_path / "data" / "work" / "2026-11-05 10-01-00" / "speakers.json").exists()
    assert not (tmp_path / "Результаты" / "Не опознаны").exists()


def test_a_transcript_without_names_breaks_at_pauses_and_at_sentence_ends():
    words = [Word(0, 1, "а"), Word(1.5, 2, "б"), Word(5, 6, "в")]
    talk = [Word(10 + i, 10.5 + i, "раз." if i in (30, 50) else "слово") for i in range(70)]
    turns = paragraphs(words + talk, pause=2.0, longest=45.0)
    assert [(t.start, t.who) for t in turns] == [(0, ""), (5, ""), (10, ""), (61, "")]
    assert turns[0].text == "а б" and turns[1].text == "в"
    assert turns[2].text.endswith("раз.")  # past 45 s, cut at the end of a sentence, not mid-sentence
    endless = [Word(i, i + 0.5, "слово") for i in range(200)]
    assert [t.start for t in paragraphs(endless)] == [0, 90, 180]  # no sentence end: every 90 s
    assert paragraphs([]) == []


def test_a_call_without_names_lists_no_participants():
    info = CallInfo(datetime(2026, 11, 5, 10, 1), 600, [])
    assert info.byline == "5 ноября 2026, 10:01 · 10 мин"
    assert call_document(info, paragraphs([Word(0, 1, "привет")])).endswith("[00:00:00] привет\n\n")


def test_slack_says_which_recording_the_notes_are_from(tmp_path):
    r = result(tmp_path)
    named = type(r)(**{**r.__dict__, "recording": "2026-11-10 16-13-05 <!channel>.mp4"})
    assert (
        slack_text(named, "U0TONY")
        == "<@U0TONY> *Планёрка: загрузки*\nЗапись: 2026-11-10 16-13-05 &lt;!channel&gt;.mp4"
    )


def test_claude_is_told_there_are_no_names_to_trust():
    unnamed, named = prompt(named=False), prompt(named=True)
    assert "нет подписей" in unnamed and "Имена бери из подписей" not in unnamed
    assert "Имена бери из подписей" in named and "нет подписей" not in named
    cmd = ClaudeCodeNotes("claude", named=False).command()
    assert cmd[cmd.index("--system-prompt") + 1] == unnamed


def test_notes_are_wide_by_topic_and_name_only_who_was_named():
    for named in (False, True):
        p = " ".join(prompt(named).split())  # the text wraps; the rules are what matters
        assert "Подробнее лучше, чем короче" in p and "### <Тема разговора> (ЧЧ:ММ:СС)" in p
        assert p.count("«кто — не ясно»") == 1  # once, and only as what not to write
        assert "без «кто — не ясно»" in p or "Не пиши «кто — не ясно»" in p
        assert "- [ ]" not in p  # next steps are plain items, not checkboxes
    assert "«это на <имя>»" in prompt(named=False)  # a name only when the call says who does it
    for named in (False, True):  # placeholders, not this team's names: an example must not become a fact
        assert not any(name in prompt(named) for name in ("Вася", "Вова", "Петя", "Caveman"))


def test_speakers_are_off_unless_switched_on(tmp_path):
    off = Settings.from_env({"HUDDLE_DATA_DIR": str(tmp_path)})
    assert off.speakers is False and off.models_needed == [off.whisper, off.vad]
    assert "speakers off" in off.describe_models()
    on = Settings.from_env({"HUDDLE_DATA_DIR": str(tmp_path), "HUDDLE_SPEAKERS": "On"})
    assert on.speakers is True and on.models_needed == [on.whisper, on.vad, on.voice]
    with pytest.raises(ConfigError, match="HUDDLE_SPEAKERS"):
        Settings.from_env({"HUDDLE_SPEAKERS": "maybe"})


def test_without_speakers_the_voice_model_is_not_required(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda tool: f"/bin/{tool}")
    (tmp_path / "models").mkdir()
    s = Settings.from_env({"HUDDLE_DATA_DIR": str(tmp_path)})
    s.whisper_model.write_bytes(b"model")
    s.require_transcription()  # no voice model, no error
    with pytest.raises(ConfigError, match="voice model"):
        Settings.from_env({"HUDDLE_DATA_DIR": str(tmp_path), "HUDDLE_SPEAKERS": "on"}).require_transcription()


def test_naming_by_hand_needs_speakers_on(tmp_path, monkeypatch, caplog):
    for key in ("HUDDLE_SPEAKERS", "SLACK_BOT_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HUDDLE_DATA_DIR", str(tmp_path))
    video = tmp_path / "call.mkv"
    video.write_bytes(b"")
    for flag in (["--names", "1=Вася"], ["--learn"]):
        caplog.clear()
        assert cli.main(["--env", str(tmp_path / "none.env"), "process", str(video), *flag]) == 2
        assert "HUDDLE_SPEAKERS=on" in caplog.text  # stopped by this check, not by a missing model


def ready(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    """Settings whose tools and whisper model are in place."""
    monkeypatch.setattr("shutil.which", lambda tool: f"/bin/{tool}")
    s = Settings.from_env({"HUDDLE_DATA_DIR": str(tmp_path), **env})
    s.models_dir.mkdir(parents=True, exist_ok=True)
    s.whisper_model.write_bytes(b"model")
    return s


def test_the_watcher_and_process_get_the_switch(tmp_path, monkeypatch):
    s = ready(tmp_path, monkeypatch)
    (tmp_path / "people").mkdir()
    (tmp_path / "people" / "avatars.json").write_text("not json: never read with names off", encoding="utf-8")
    p = cli._processor(s, notes=True)
    assert p.speakers is False and p.library.avatars == {} and p.library.voices == {}
    assert p.notes is not None and p.notes.prompt == prompt(named=False)
    assert p.transcriber.language == "auto"
    assert (
        cli._processor(ready(tmp_path, monkeypatch, HUDDLE_WHISPER_LANGUAGE="ru"), notes=False).transcriber.language
        == "ru"
    )


def test_without_speakers_there_is_nothing_to_regress(tmp_path, monkeypatch, capsys):
    s = ready(tmp_path, monkeypatch)
    assert cli._regress(s, None) == 0  # type: ignore[arg-type]
    assert "nothing to check" in capsys.readouterr().out


class MediaWithoutLength(MediaWithLength):
    def duration(self, video: Path) -> float:
        raise MediaError("ffprobe failed")


def test_a_recording_without_a_known_length_takes_its_last_word(tmp_path):
    video, processor, _ = transcribed(tmp_path)
    processor.media = MediaWithoutLength()  # type: ignore[assignment]
    assert processor.process(video, "", {}).info.duration == 7.6


def test_other_instructions_mean_other_notes(tmp_path):
    video, processor, notes = transcribed(tmp_path)
    processor.process(video, "", {})
    processor.process(video, "", {})
    assert notes.calls == 1
    notes.prompt = "other instructions"
    processor.process(video, "", {})
    assert notes.calls == 2
