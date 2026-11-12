"""Watch the recordings folder: every new recording is processed once and its result sent.

A recording counts as finished when its size has stopped changing for a while, so a file
OBS is still writing is left alone; if it grows again later (a paused recording), it is
processed again. A journal next to the results remembers what was done, what failed and
how often, so a restart neither repeats work nor loses a call.

What goes wrong is retried on its own: a recording that fails three times is reported in
Slack; a message Slack refused is sent again; notes Claude could not write (a usage limit,
say) are asked for again for a few hours, and sent when they come.

Every few minutes the watcher also reads the team's feedback under its messages in Slack.

When a new recording is taken into work, the owner can be told on screen; the retries of a
call already taken are not announced again.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from huddle_notes.deliver import Delivery, DeliveryError
from huddle_notes.pipeline import CallResult

log = logging.getLogger(__name__)

VIDEO = {".mp4", ".mov", ".mkv", ".m4v", ".webm", ".flv"}
JOURNAL = ".huddle-notes-journal.json"
RETRY = 600  # seconds before trying a failed recording or message again
NOTES_RETRY = 1800  # seconds before asking Claude again for notes it could not write
NOTES_TRIES = 12  # 6 hours: longer than a subscription's usage limit lasts
FEEDBACK_EVERY = 300  # seconds between reads of the replies under the bot's messages


class Journal:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.entries: dict[str, dict[str, Any]] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def get(self, name: str) -> dict[str, Any]:
        return self.entries.get(name, {})

    def update(self, name: str, **fields: Any) -> None:
        self.entries[name] = {**self.get(name), **fields, "at": datetime.now(UTC).isoformat(timespec="seconds")}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.entries, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)


class Watcher:
    def __init__(
        self,
        inbox: Path,
        journal: Journal,
        process: Callable[[Path], CallResult],
        delivery: Delivery | None = None,
        settle: float = 30,
        max_attempts: int = 3,
        clock: Callable[[], float] = time.time,
        feedback: Callable[[], object] | None = None,
        taken: Callable[[str], object] | None = None,
    ) -> None:
        self.inbox = inbox
        self.journal = journal
        self.process = process
        self.delivery = delivery
        self.settle = settle  # seconds a file must keep its size before it counts as finished
        self.max_attempts = max_attempts
        self.clock = clock
        self.feedback = feedback  # reads the team's replies in Slack; None without Slack
        self.taken = taken  # tells the owner a new recording is taken into work; None: nobody
        self._feedback_due = 0.0
        self._seen: dict[str, tuple[int, float]] = {}  # name -> (size, when that size was first seen)

    def run_forever(self, interval: float = 30) -> None:
        log.info("watching %s", self.inbox)
        while True:
            try:
                self.tick()
            except Exception:  # one bad file or a full disk must not stop the watcher for good
                log.exception("watch cycle failed")
            self.read_feedback()
            time.sleep(interval)

    def read_feedback(self) -> None:
        """The team's replies in Slack, every FEEDBACK_EVERY seconds; never stops the watcher."""
        if self.feedback is None or self.clock() < self._feedback_due:
            return
        self._feedback_due = self.clock() + FEEDBACK_EVERY
        try:
            self.feedback()
        except Exception:
            log.exception("reading feedback failed")

    def tick(self) -> list[str]:
        """One pass over the folder; returns the recordings handled in it."""
        handled = []
        for video, size in self._recordings():
            entry = self.journal.get(video.name)
            if entry.get("size") not in (None, size):  # it grew after it was processed
                entry = {}
            if not self._due(entry) or not self._finished(video, size):
                continue
            self._handle(video, size, entry)
            handled.append(video.name)
        return handled

    def _recordings(self) -> list[tuple[Path, int]]:
        """Recordings with their sizes, oldest first; a file deleted meanwhile is skipped."""
        if not self.inbox.is_dir():
            return []
        found = []
        for p in self.inbox.iterdir():
            if p.suffix.lower() not in VIDEO or p.name.startswith("."):
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            if p.is_file():
                found.append((st.st_mtime, p, st.st_size))
        return [(p, size) for _, p, size in sorted(found)]

    def _due(self, entry: dict[str, Any]) -> bool:
        if (entry.get("retry_after") or 0) > self.clock():
            return False
        status = entry.get("status")
        if status is None:
            return True
        if status == "failed":
            return int(entry.get("attempts", 0)) < self.max_attempts
        unsent = self.delivery is not None and entry.get("delivered") is False
        notes_missing = bool(entry.get("notes_error")) and entry.get("notes_tries", 0) < NOTES_TRIES
        return unsent or notes_missing

    def _finished(self, video: Path, size: int) -> bool:
        now = self.clock()
        known = self._seen.get(video.name)
        if known is None or known[0] != size:
            self._seen[video.name] = (size, now)
            return False
        return now - known[1] >= self.settle

    def _handle(self, video: Path, size: int, entry: dict[str, Any]) -> None:
        # a call done earlier (message not sent, notes missing) is processed again to rebuild
        # its result: every step is cached, so this costs seconds, not another transcription
        log.info("%s %s", "retrying" if entry.get("status") == "done" else "processing", video.name)
        if entry.get("status") is None:  # new, or grown since: not a retry
            self._announce(video.name)
        try:
            result = self.process(video)
        except Exception as e:
            attempts = entry.get("attempts", 0) + 1
            log.exception("%s failed (attempt %d of %d)", video.name, attempts, self.max_attempts)
            self.journal.update(
                video.name,
                status="failed",
                attempts=attempts,
                size=size,
                error=str(e)[:500],
                retry_after=self.clock() + RETRY,
            )
            if attempts >= self.max_attempts:
                self._report_failure(video.name, str(e))
            return

        notes_tries = entry.get("notes_tries", 0) + (1 if result.notes_error else 0)
        self.journal.update(
            video.name,
            status="done",
            attempts=0,
            size=size,
            document=str(result.document),
            notes_error=result.notes_error,
            notes_tries=notes_tries,
            retry_after=self.clock() + NOTES_RETRY if result.notes_error else None,
        )
        if self.delivery is None:
            return
        if entry.get("delivered") is True and result.notes_error:
            return  # sent already, and the notes are still missing: nothing new to say
        self.journal.update(video.name, delivered=False)
        try:
            self.delivery.deliver(result)
        except DeliveryError as e:
            log.warning("%s: not delivered yet: %s", video.name, e)
            self.journal.update(video.name, delivery_error=str(e)[:500], retry_after=self.clock() + RETRY)
            return
        self.journal.update(video.name, delivered=True, delivery_error=None)

    def _announce(self, name: str) -> None:
        """A note on screen; never stops the call itself."""
        if self.taken is None:
            return
        try:
            self.taken(name)
        except Exception:
            log.exception("%s: notification failed", name)

    def _report_failure(self, name: str, error: str) -> None:
        if self.delivery is None:
            return
        try:
            self.delivery.report_failure(name, error)
        except DeliveryError as e:
            log.warning("%s: failure not reported: %s", name, e)
