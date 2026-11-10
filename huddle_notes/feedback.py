"""What the team wants from the notes, and how it gets there.

Wishes live in one file people can read and edit (данные/пожелания.md); Claude gets them
with every call's transcript. They come in two ways: a reply in the thread under the
bot's message in Slack, read here, or the owner asking Claude Code in the Huddle Notes
folder, which edits the same file.

A 👎 without words says nothing to fix, so the bot asks in the thread what is wrong.
Nothing is learned by the model itself: each call's notes are written from scratch,
with the wishes in the request.
"""

from __future__ import annotations

import html
import json
import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from typing import Any

from slack_sdk.errors import SlackApiError

log = logging.getLogger(__name__)

WISHES = "пожелания.md"
HEADER = """\
# Пожелания к заметкам

Claude учитывает их, когда пишет заметки к каждому новому звонку. Сюда попадают ответы в треде
под сообщением бота в Slack и то, что владелец попросил записать в Claude Code. Пожелание —
строка, которая начинается с «- »; ненужные можно удалить.

"""
KEEP = 40  # the newest wishes that go to Claude; older ones stay in the file
LONGEST = 500  # characters of one wish


class Wishes:
    def __init__(self, path: Path, keep: int = KEEP) -> None:
        self.path = path
        self.keep = keep

    def lines(self) -> list[str]:
        """The newest wishes, oldest first."""
        if not self.path.is_file():
            return []
        found = [line.strip() for line in self.path.read_text(encoding="utf-8-sig").splitlines()]
        return [line for line in found if line.startswith("- ") and len(line) > 2][-self.keep :]

    def for_prompt(self) -> str:
        return "\n".join(self.lines())

    def add(self, when: datetime, who: str, where: str, text: str) -> str:
        """One wish as one line: who, when, from where, and what they want."""
        line = f"- {when:%d.%m.%Y}, {_one_line(who, 80)} ({where}): {_one_line(text, LONGEST)}\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        old = self.path.read_text(encoding="utf-8-sig") if self.path.is_file() else HEADER
        if old and not old.endswith("\n"):
            old += "\n"
        tmp = self.path.with_name(f"partial-{self.path.name}")
        tmp.write_text(old + line, encoding="utf-8")
        tmp.replace(self.path)
        return line.strip()


ASK = "Что не так с заметками? Напишите здесь в ответ — учту со следующего звонка."
THANKS = "Записал в пожелания к заметкам, учту со следующего звонка."
DAYS = 14  # how far back replies under the bot's messages are read


class SlackFeedback:
    """Replies and 👎 under the bot's own call messages in the channel, read on a timer.

    A state file remembers the newest reply read in each thread and where the bot has
    already asked, so nothing is written or asked twice, also after a restart.
    """

    def __init__(
        self,
        client: Any,
        channel: str,
        wishes: Wishes,
        state: Path,
        timezone: tzinfo = UTC,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.client = client
        self.channel = channel
        self.wishes = wishes
        self.state_path = state
        self.timezone = timezone
        self.clock = clock or (lambda: datetime.now(UTC).timestamp())
        self._me: str | None = None
        self._names: dict[str, str] = {}

    def poll(self) -> int:
        """Reads what is new; returns how many wishes it wrote. Slack being down or
        refusing is logged and tried again on the next poll."""
        try:
            return self._poll()
        except SlackApiError as e:
            log.warning("feedback not read: Slack: %s", e.response.get("error", e))
        except OSError as e:
            log.warning("feedback not read: Slack unreachable: %s", e)
        return 0

    def _poll(self) -> int:
        me = self._bot_user()
        since = self.clock() - DAYS * 86400
        state = self._load()
        # threads older than the window are never read again: forget them
        state["seen"] = {ts: last for ts, last in state["seen"].items() if float(ts) >= since}
        state["asked"] = [ts for ts in state["asked"] if float(ts) >= since]
        oldest = f"{since:.6f}"
        history = self.client.conversations_history(channel=self.channel, oldest=oldest, limit=200)
        written = 0
        for message in history.get("messages", []):
            if message.get("user") != me or not message.get("files") or message.get("subtype"):
                continue  # not a call the bot sent: a join, a failure report, another app
            ts = message["ts"]
            if message.get("reply_count"):
                written += self._read_thread(message, me, state)
            elif _disliked(message) and ts not in state["asked"]:
                state["asked"].append(ts)
                self._save(state)  # before asking: a failed question is not asked twice
                self._say(ts, ASK)
        self._save(state)
        return written

    def _read_thread(self, message: dict[str, Any], me: str, state: dict[str, Any]) -> int:
        ts = message["ts"]
        seen = state["seen"].get(ts, ts)
        replies = self.client.conversations_replies(channel=self.channel, ts=ts, oldest=seen, limit=200)
        new = [r for r in replies.get("messages", []) if float(r["ts"]) > float(seen)]
        if not new:
            return 0
        call = Path(message["files"][0].get("name") or message["files"][0].get("title") or "").stem
        written = 0
        for r in new:
            text = r.get("text", "").strip()
            if r.get("user") == me or r.get("bot_id") or r.get("subtype") or not text:
                continue  # the bot's own answers, joins, empty or edited-away messages
            when = datetime.fromtimestamp(float(r["ts"]), UTC).astimezone(self.timezone)
            line = self.wishes.add(when, self._name(r.get("user", "")), f"Slack, звонок {call}", html.unescape(text))
            log.info("wish from Slack: %s", line)
            written += 1
        state["seen"][ts] = max((r["ts"] for r in new), key=float)
        self._save(state)  # before answering: a failed answer must not write the wish twice
        if written:
            self._say(ts, THANKS)
        return written

    def _say(self, thread: str, text: str) -> None:
        try:
            self.client.chat_postMessage(channel=self.channel, thread_ts=thread, text=text)
        except SlackApiError as e:  # the wish is kept either way
            log.warning("no answer in the thread: Slack: %s", e.response.get("error", e))

    def _bot_user(self) -> str:
        if self._me is None:
            self._me = str(self.client.auth_test()["user_id"])
        return self._me

    def _name(self, user: str) -> str:
        """A person's name in Slack; their member ID when it can't be read."""
        if user not in self._names:
            try:
                profile = self.client.users_info(user=user)["user"]
                p = profile.get("profile", {})
                self._names[user] = p.get("display_name") or p.get("real_name") or profile.get("name") or user
            except SlackApiError:
                self._names[user] = user
        return self._names[user]

    def _load(self) -> dict[str, Any]:
        state: dict[str, Any] = {"seen": {}, "asked": []}
        if self.state_path.is_file():
            state.update(json.loads(self.state_path.read_text(encoding="utf-8")))
        return state

    def _save(self, state: dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_name(f"partial-{self.state_path.name}")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.state_path)


def _disliked(message: dict[str, Any]) -> bool:
    """A 👎 on the message, in any skin tone."""
    return any(r.get("name", "").split("::")[0] == "-1" for r in message.get("reactions", []))


def _one_line(text: str, limit: int) -> str:
    """A wish is one line of the file: line breaks become spaces, and it has an end."""
    flat = re.sub(r"\s+", " ", text).strip()
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"
