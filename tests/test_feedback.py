import os
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from slack_sdk.errors import SlackApiError

from huddle_notes.feedback import ASK, HEADER, THANKS, SlackFeedback, Wishes
from huddle_notes.notes import ClaudeCodeNotes
from huddle_notes.watch import FEEDBACK_EVERY, Journal, Watcher

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC).timestamp()
BOT = "U0BOT"


def test_a_wish_is_one_line_under_a_header_people_can_read(tmp_path):
    wishes = Wishes(tmp_path / "данные" / "пожелания.md")
    assert wishes.lines() == [] and wishes.for_prompt() == ""
    line = wishes.add(datetime(2026, 11, 10, 9, 5), "Артём", "Claude Code", "пиши\nкороче,  без воды")
    assert line == "- 10.11.2026, Артём (Claude Code): пиши короче, без воды"
    assert wishes.path.read_text(encoding="utf-8") == HEADER + line + "\n"
    wishes.add(datetime(2026, 11, 11), "Тони", "Claude Code", "x" * 600)
    assert wishes.lines()[0] == line and wishes.lines()[1].endswith("x…")
    assert len(wishes.lines()[1]) < 600


def test_only_the_newest_wishes_go_to_claude_and_hand_edits_count(tmp_path):
    path = tmp_path / "пожелания.md"
    path.write_text("# Пожелания\nсвой текст, не пожелание\n- раз\n-\n- два\n- три\n", encoding="utf-8")
    assert Wishes(path, keep=2).for_prompt() == "- два\n- три"
    Wishes(path).add(datetime(2026, 11, 10), "Тони", "Claude Code", "четыре")  # no header added again
    assert path.read_text(encoding="utf-8").startswith("# Пожелания\nсвой текст")


def call(ts: str, replies: int = 0, reactions: list[str] | None = None, **extra: Any) -> dict[str, Any]:
    m: dict[str, Any] = {"ts": ts, "user": BOT, "bot_id": "B0BOT", "files": [{"name": "2026-11-09 11-17.md"}]}
    if replies:
        m["reply_count"] = replies
    if reactions:
        m["reactions"] = [{"name": r, "count": 1, "users": ["U0ANNA"]} for r in reactions]
    return {**m, **extra}


class FakeSlack:
    def __init__(self, messages: list[dict[str, Any]], threads: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.messages, self.threads = messages, threads or {}
        self.said: list[tuple[str, str]] = []
        self.fail: str | None = None
        self.history_calls: list[dict[str, Any]] = []

    def auth_test(self) -> dict[str, str]:
        return {"user_id": BOT, "bot_id": "B0BOT"}

    def conversations_history(self, **kw: Any) -> dict[str, Any]:
        if self.fail:
            raise SlackApiError("failed", {"ok": False, "error": self.fail})
        self.history_calls.append(kw)
        return {"messages": self.messages}

    def conversations_replies(self, channel: str, ts: str, oldest: str, limit: int) -> dict[str, Any]:
        # like Slack: the parent first, then replies newer than oldest
        parent = next(m for m in self.messages if m["ts"] == ts)
        return {"messages": [parent] + [r for r in self.threads.get(ts, []) if float(r["ts"]) > float(oldest)]}

    def users_info(self, user: str) -> dict[str, Any]:
        if user == "U0GONE":
            raise SlackApiError("failed", {"ok": False, "error": "user_not_found"})
        return {"user": {"name": "anna", "profile": {"display_name": "", "real_name": "Анна"}}}

    def chat_postMessage(self, channel: str, thread_ts: str, text: str) -> None:
        self.said.append((thread_ts, text))
        reply = {"ts": f"{float(thread_ts) + 100 + len(self.said):.6f}", "user": BOT, "bot_id": "B0BOT", "text": text}
        self.threads.setdefault(thread_ts, []).append(reply)
        for m in self.messages:
            if m["ts"] == thread_ts:
                m["reply_count"] = m.get("reply_count", 0) + 1


def feedback(tmp_path: Path, slack: FakeSlack) -> SlackFeedback:
    return SlackFeedback(
        slack, "C0CHANNEL", Wishes(tmp_path / "пожелания.md"), tmp_path / "slack-feedback.json", clock=lambda: NOW
    )


def reply(ts: str, text: str, user: str = "U0ANNA", **extra: Any) -> dict[str, Any]:
    return {"ts": ts, "user": user, "text": text, **extra}


def test_a_reply_under_the_bots_call_becomes_a_wish_once(tmp_path):
    parent = "1794300000.000100"
    slack = FakeSlack(
        [call(parent, replies=2)],
        {
            parent: [
                reply("1794300100.000100", "слишком длинно &amp; сухо"),
                reply("1794300200.000100", ""),
                reply("1794300300.000100", "и в канал", subtype="thread_broadcast"),
                reply("1794300400.000100", "", subtype="channel_join"),
            ]
        },
    )
    assert feedback(tmp_path, slack).poll() == 2
    assert Wishes(tmp_path / "пожелания.md").lines() == [
        "- 10.11.2026, Анна (Slack, звонок 2026-11-09 11-17): слишком длинно & сухо",
        "- 10.11.2026, Анна (Slack, звонок 2026-11-09 11-17): и в канал",
    ]
    assert slack.said == [(parent, THANKS)]
    assert slack.history_calls[0]["oldest"] == f"{NOW - 14 * 86400:.6f}"

    again = feedback(tmp_path, slack)  # a restart: the state file remembers what was read
    assert again.poll() == 0 and len(slack.said) == 1
    slack.threads[parent].append(reply("1794300900.000100", "и задачи с датами", user="U0GONE"))
    assert again.poll() == 1
    last = Wishes(tmp_path / "пожелания.md").lines()[-1]
    assert last.endswith("U0GONE (Slack, звонок 2026-11-09 11-17): и задачи с датами")  # no name: the member ID


def test_only_people_under_the_bots_call_messages_count(tmp_path):
    parent = "1794300000.000100"
    slack = FakeSlack(
        [
            call(parent, replies=2),
            call("1794301000.000100", replies=1, files=[]),  # a failure report: no file
            call("1794302000.000100", replies=1, user="U0ANNA"),  # someone else's upload
            {"ts": "1794303000.000100", "subtype": "channel_join", "user": "U0ANNA"},
        ],
        {
            parent: [
                reply("1794300100.000100", "Записал", user=BOT),
                reply("1794300200.000100", "от другого бота", user="U0APP", bot_id="B0APP"),
            ],
            "1794301000.000100": [reply("1794301100.000100", "почему упало?")],
            "1794302000.000100": [reply("1794302100.000100", "моё")],
        },
    )
    assert feedback(tmp_path, slack).poll() == 0
    assert slack.said == [] and not (tmp_path / "пожелания.md").exists()


def test_a_thumbs_down_without_words_gets_one_question(tmp_path):
    parent = "1794300000.000100"
    slack = FakeSlack([call(parent, reactions=["-1::skin-tone-3"]), call("1794301000.000100", reactions=["+1"])])
    fb = feedback(tmp_path, slack)
    assert fb.poll() == 0 and slack.said == [(parent, ASK)]
    fb.poll()
    feedback(tmp_path, slack).poll()
    assert slack.said == [(parent, ASK)]  # asked once, also after a restart
    slack.threads[parent].append(reply("1794309000.000100", "нет задач"))
    assert fb.poll() == 1 and slack.said[-1] == (parent, THANKS)


def test_slack_trouble_is_logged_not_raised(tmp_path, caplog):
    slack = FakeSlack([call("1794300000.000100")])
    slack.fail = "missing_scope"
    assert feedback(tmp_path, slack).poll() == 0
    assert "missing_scope" in caplog.text


def test_old_threads_are_forgotten(tmp_path):
    state = tmp_path / "slack-feedback.json"
    state.write_text('{"seen": {"1.000000": "2.000000"}, "asked": ["1.000000"]}', encoding="utf-8")
    feedback(tmp_path, FakeSlack([])).poll()
    assert state.read_text(encoding="utf-8").count("1.000000") == 0


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_the_watcher_reads_feedback_every_few_minutes_and_survives_its_errors(tmp_path):
    clock, calls = Clock(), []

    def read() -> None:
        calls.append(clock.now)
        raise RuntimeError("boom")

    watcher = Watcher(tmp_path, Journal(tmp_path / "j.json"), lambda v: None, None, clock=clock, feedback=read)  # type: ignore[arg-type, return-value]
    watcher.read_feedback()
    clock.now += 30
    watcher.read_feedback()
    clock.now += FEEDBACK_EVERY
    watcher.read_feedback()
    assert calls == [1000.0, 1000.0 + 30 + FEEDBACK_EVERY]


@pytest.mark.skipif(os.name == "nt", reason="the fake claude is a script with a shebang")
def test_claude_gets_the_wishes_with_the_transcript(tmp_path):
    seen = tmp_path / "prompt.txt"
    script = tmp_path / "claude"
    script.write_text(
        "#!/usr/bin/env python3\nimport sys\n"
        f"open({str(seen)!r}, 'w', encoding='utf-8').write(sys.stdin.read())\n"
        "print('# Тема\\n### Краткое содержание\\nок')\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    ClaudeCodeNotes(str(script)).write("[00:00:01] **Петя:** привет", "Трактор", "- короче")
    assert seen.read_text(encoding="utf-8") == (
        "Словарь команды: Трактор\n\nПожелания команды к заметкам:\n- короче\n\n"
        "Расшифровка:\n[00:00:01] **Петя:** привет"
    )
