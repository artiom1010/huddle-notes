import os
import stat
from datetime import datetime
from pathlib import Path

import pytest
from slack_sdk.errors import SlackApiError

from huddle_notes.config import Settings, load_env_file
from huddle_notes.deliver import DeliveryError, SlackDelivery, slack_text
from huddle_notes.notes import ClaudeCodeNotes, NotesError, split_title
from huddle_notes.pipeline import CallResult
from huddle_notes.render import CallInfo
from huddle_notes.watch import Journal, Watcher

NOTES = (
    "# Планёрка: загрузки\n### Краткое содержание\nРешили загрузить кабинет.\n"
    "### Подробности\n**Трактор.** кабинет (00:01:00)\n"
    "### Следующие шаги\n- [ ] **Петя:** загрузить кабинет\n- [ ] **Кто — не ясно:** перенести в Plane"
)


def fake_claude(tmp_path: Path, body: str) -> str:
    """A stand-in for the claude command: a script that prints body (or fails)."""
    script = tmp_path / "claude"
    # through env: a shebang can't hold a path with spaces, like this project's own folder
    script.write_text(f"#!/usr/bin/env python3\nimport sys\nsys.stdin.read()\n{body}\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


@pytest.mark.skipif(os.name == "nt", reason="the fake claude is a script with a shebang")
def test_notes_come_back_from_claude_code(tmp_path):
    writer = ClaudeCodeNotes(fake_claude(tmp_path, f"print({NOTES!r})"), "claude-opus-5-5")
    assert writer.write("[00:00:01] **Петя:** привет", "Трактор") == NOTES


@pytest.mark.skipif(os.name == "nt", reason="the fake claude is a script with a shebang")
def test_claude_failures_become_notes_errors(tmp_path):
    with pytest.raises(NotesError, match="failed"):
        ClaudeCodeNotes(fake_claude(tmp_path, "sys.exit('not logged in')")).write("", "")
    with pytest.raises(NotesError, match="not notes"):
        ClaudeCodeNotes(fake_claude(tmp_path, "print('Sorry, I cannot help')")).write("", "")
    with pytest.raises(NotesError, match="not installed"):
        ClaudeCodeNotes(str(tmp_path / "missing")).write("", "")


def test_claude_runs_without_any_tools():
    cmd = ClaudeCodeNotes("claude", "claude-opus-5-5").command()
    assert cmd[cmd.index("--tools") + 1] == ""
    assert "--strict-mcp-config" in cmd
    assert cmd[cmd.index("--setting-sources") + 1] == ""  # nothing from the owner's own settings
    assert cmd[cmd.index("--model") + 1] == "claude-opus-5-5"


def test_title_of_the_notes():
    title, rest = split_title(NOTES)
    assert title == "Планёрка: загрузки" and rest.startswith("### Краткое содержание")
    assert split_title("### Только разделы") == (None, "### Только разделы")


def result(tmp_path: Path, notes: str | None = NOTES, error: str | None = None, faces: int = 0) -> CallResult:
    doc = tmp_path / "2026-11-09 10-01.md"
    doc.write_text("документ", encoding="utf-8")
    face_files = []
    for i in range(faces):
        f = tmp_path / f"Участник {i + 3}.jpg"
        f.write_bytes(b"jpg")
        face_files.append(f)
    info = CallInfo(datetime(2026, 11, 9, 10, 1), 1320, ["Тони", "Петя"])
    return CallResult(doc, info, None, "report", notes, error, face_files)  # type: ignore[arg-type]


def test_slack_message_is_the_title_and_only_the_questions(tmp_path):
    assert slack_text(result(tmp_path), mention="U0TONY") == "<@U0TONY> *Планёрка: загрузки*"
    text = slack_text(result(tmp_path, faces=1))
    assert text.startswith("*Планёрка: загрузки*\nНе узнал: Участник 3. На снимке во вложении — в красной рамке.")
    assert "«Участник 3 — это Вася»" in text


def test_slack_message_says_why_notes_are_missing(tmp_path):
    text = slack_text(result(tmp_path, notes=None, error="Claude did not answer"))
    assert text == (
        "*Звонок 9 ноября 2026, 10:01*\nЗаметки не получились (Claude did not answer), в файле только расшифровка."
    )


class FakeSlack:
    def __init__(self, fail: str | None = None) -> None:
        self.fail = fail
        self.sent: list[dict[str, object]] = []

    def files_upload_v2(self, **kwargs: object) -> None:
        if self.fail:
            raise SlackApiError("failed", {"ok": False, "error": self.fail})
        self.sent.append(kwargs)


def test_slack_gets_the_file_and_the_faces(tmp_path):
    client = FakeSlack()
    SlackDelivery("xoxb-test", "C0CHANNEL", "U0TONY", client=client).deliver(result(tmp_path, faces=1))
    (sent,) = client.sent
    assert sent["channel"] == "C0CHANNEL"
    assert [f["title"] for f in sent["file_uploads"]] == ["2026-11-09 10-01.md", "Участник 3"]  # type: ignore[union-attr, index]


def test_slack_errors_are_delivery_errors(tmp_path):
    with pytest.raises(DeliveryError, match="not_in_channel"):
        SlackDelivery("xoxb-test", "C0", client=FakeSlack("not_in_channel")).deliver(result(tmp_path))


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Recorder:
    def __init__(self, tmp_path: Path, fail: Exception | None = None, notes_error: str | None = None) -> None:
        self.tmp_path, self.fail, self.notes_error, self.calls = tmp_path, fail, notes_error, []

    def __call__(self, video: Path) -> CallResult:
        self.calls.append(video.name)
        if self.fail:
            raise self.fail
        if self.notes_error:
            return result(self.tmp_path, notes=None, error=self.notes_error)
        return result(self.tmp_path)


class FakeDelivery:
    def __init__(self, fail: bool = False) -> None:
        self.fail, self.sent, self.failures = fail, [], []

    def deliver(self, r: CallResult) -> None:
        if self.fail:
            raise DeliveryError("Slack unreachable")
        self.sent.append(r.notes is not None)

    def report_failure(self, recording: str, error: str) -> None:
        self.failures.append((recording, error))


def inbox(tmp_path: Path) -> Path:
    folder = tmp_path / "Записи"
    folder.mkdir()
    (folder / "2026-11-09 10-01-05.mkv").write_bytes(b"x" * 10)
    (folder / "notes.txt").write_text("not a recording")
    return folder


def test_a_recording_is_processed_once_it_stops_growing_and_only_once(tmp_path):
    folder, clock, process, delivery = inbox(tmp_path), Clock(), Recorder(tmp_path), FakeDelivery()
    journal = Journal(tmp_path / "Результаты" / "journal.json")
    watcher = Watcher(folder, journal, process, delivery, settle=30, clock=clock)

    assert watcher.tick() == []  # first sight: OBS may still be writing
    (folder / "2026-11-09 10-01-05.mkv").write_bytes(b"x" * 20)
    clock.now += 40
    assert watcher.tick() == []  # it grew: wait again
    clock.now += 40
    assert watcher.tick() == ["2026-11-09 10-01-05.mkv"]
    clock.now += 40
    assert watcher.tick() == []
    assert process.calls == ["2026-11-09 10-01-05.mkv"] and delivery.sent == [True]

    restarted = Watcher(folder, Journal(journal.path), process, delivery, settle=30, clock=clock)
    restarted.tick()
    clock.now += 40
    assert restarted.tick() == []  # the journal remembers it after a restart


def test_failures_are_retried_later_and_reported_after_three_attempts(tmp_path):
    folder, clock, delivery = inbox(tmp_path), Clock(), FakeDelivery()
    process = Recorder(tmp_path, fail=RuntimeError("whisper failed"))
    journal = Journal(tmp_path / "journal.json")
    watcher = Watcher(folder, journal, process, delivery, settle=0, clock=clock)
    for _ in range(10):
        watcher.tick()
        clock.now += 700
    assert len(process.calls) == 3
    entry = journal.get("2026-11-09 10-01-05.mkv")
    assert entry["status"] == "failed" and entry["error"] == "whisper failed"
    assert delivery.failures == [("2026-11-09 10-01-05.mkv", "whisper failed")] and delivery.sent == []


def test_missing_notes_are_asked_for_again_and_sent_when_they_come(tmp_path):
    folder, clock, delivery = inbox(tmp_path), Clock(), FakeDelivery()
    process = Recorder(tmp_path, notes_error="usage limit reached")
    watcher = Watcher(folder, Journal(tmp_path / "journal.json"), process, delivery, settle=0, clock=clock)
    watcher.tick()
    watcher.tick()
    assert delivery.sent == [False]  # the transcript goes out at once, without notes
    clock.now += 1900
    watcher.tick()
    assert delivery.sent == [False]  # still no notes: nothing new to send
    process.notes_error = None
    clock.now += 1900
    watcher.tick()
    clock.now += 1900
    watcher.tick()
    assert delivery.sent == [False, True] and len(process.calls) == 3


def test_a_recording_that_grows_after_processing_is_processed_again(tmp_path):
    folder, clock, process, delivery = inbox(tmp_path), Clock(), Recorder(tmp_path), FakeDelivery()
    watcher = Watcher(folder, Journal(tmp_path / "journal.json"), process, delivery, settle=0, clock=clock)
    watcher.tick()
    watcher.tick()
    (folder / "2026-11-09 10-01-05.mkv").write_bytes(b"x" * 30)  # OBS paused, then went on
    watcher.tick()
    watcher.tick()
    assert len(process.calls) == 2 and delivery.sent == [True, True]


def test_calls_done_without_slack_are_not_sent_once_a_token_appears(tmp_path):
    folder, clock, process = inbox(tmp_path), Clock(), Recorder(tmp_path)
    journal = Journal(tmp_path / "journal.json")
    without_slack = Watcher(folder, journal, process, None, settle=0, clock=clock)
    without_slack.tick()
    without_slack.tick()
    delivery = FakeDelivery()
    later = Watcher(folder, Journal(journal.path), process, delivery, settle=0, clock=clock)
    later.tick()
    later.tick()
    assert len(process.calls) == 1 and delivery.sent == []


def test_a_message_that_could_not_be_sent_is_sent_later(tmp_path):
    folder, clock, process = inbox(tmp_path), Clock(), Recorder(tmp_path)
    delivery = FakeDelivery(fail=True)
    watcher = Watcher(folder, Journal(tmp_path / "journal.json"), process, delivery, settle=0, clock=clock)
    watcher.tick()
    watcher.tick()
    assert watcher.tick() == []  # Slack down: wait before trying again
    delivery.fail = False
    clock.now += 700
    assert watcher.tick() == ["2026-11-09 10-01-05.mkv"]
    assert delivery.sent == [True]


def test_env_file_fills_what_the_environment_does_not_set(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        '\ufeff# comment\nHUDDLE_DATA_DIR="C:\\\\Huddle Notes\\\\данные"\nSLACK_CHANNEL=C0CEZ\nHUDDLE_TIMEZONE=UTC\n',
        encoding="utf-8",
    )
    env = {"HUDDLE_TIMEZONE": "Europe/Chisinau"}
    load_env_file(env_file, env)
    assert env == {
        "HUDDLE_TIMEZONE": "Europe/Chisinau",
        "HUDDLE_DATA_DIR": "C:\\\\Huddle Notes\\\\данные",
        "SLACK_CHANNEL": "C0CEZ",
    }
    load_env_file(tmp_path / "missing.env", env)  # no file, no change


def test_inbox_and_results_sit_next_to_the_data_folder(tmp_path):
    s = Settings.from_env({"HUDDLE_DATA_DIR": str(tmp_path / "данные")})
    assert (s.inbox, s.results_dir, s.work_dir) == (
        tmp_path / "Записи",
        tmp_path / "Результаты",
        tmp_path / "данные" / "work",
    )


def test_slack_reports_a_recording_given_up_on(tmp_path):
    client = FakeSlack()
    client.chat_postMessage = lambda **kw: client.sent.append(kw)  # type: ignore[method-assign]
    SlackDelivery("xoxb-test", "C0", "U0TONY", client=client).report_failure("a.mkv", "no ffmpeg")
    (sent,) = client.sent
    assert sent["text"].startswith("<@U0TONY> Не получилось обработать запись «a.mkv»: no ffmpeg.")  # type: ignore[union-attr]
