"""Results to people: a Slack message with the short notes and the call's file attached."""

from __future__ import annotations

from typing import Any, Protocol

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from huddle_notes.notes import split_title
from huddle_notes.pipeline import CallResult


class DeliveryError(RuntimeError):
    pass


class Delivery(Protocol):
    def deliver(self, result: CallResult) -> None: ...

    def report_failure(self, recording: str, error: str) -> None: ...


def slack_text(result: CallResult, mention: str | None = None) -> str:
    """Short on purpose: the call's title, the recording it came from, then only what needs
    the reader. Everything else is in the attached file."""
    title = (split_title(result.notes)[0] if result.notes else None) or f"Звонок {result.info.when}"
    lines = [f"{f'<@{mention}> ' if mention else ''}*{_plain(title)}*"]
    if result.recording:
        lines.append(f"Запись: {_plain(result.recording)}")
    if not result.notes:
        lines.append(
            f"Заметки не получились ({result.notes_error or 'причина неизвестна'}), в файле только расшифровка."
        )
    if result.unknown_faces:
        who = ", ".join(f.stem for f in result.unknown_faces)
        lines.append(
            f"Не узнал: {who}. На снимке во вложении — в красной рамке. Кто это? Ответьте в Claude Code "
            f"в папке Call Recaps, например: «{result.unknown_faces[0].stem} — это Вася»."
        )
    return "\n".join(lines)


def _plain(text: str) -> str:
    """Words, not Slack markup: no <!channel> or link can slip in through a title or file name."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class SlackDelivery:
    def __init__(self, token: str, channel: str, mention: str | None = None, client: Any = None) -> None:
        self.client = client or WebClient(token=token)
        self.channel = channel  # a channel ID, e.g. C0123ABC: Slack's upload API takes no names
        self.mention = mention  # a member ID to @mention, e.g. U0123ABC

    def deliver(self, result: CallResult) -> None:
        files = [{"file": str(result.document), "title": result.document.name}]
        files += [{"file": str(f), "title": f.stem} for f in result.unknown_faces]
        self._send(
            self.client.files_upload_v2,
            channel=self.channel,
            file_uploads=files,
            initial_comment=slack_text(result, self.mention),
        )

    def report_failure(self, recording: str, error: str) -> None:
        """A recording the watcher gave up on: said out loud, not only in the log."""
        text = (
            f"{f'<@{self.mention}> ' if self.mention else ''}Не получилось обработать запись «{recording}»: "
            f"{error[:300]}. Подробности в данные\\logs\\watch.log."
        )
        self._send(self.client.chat_postMessage, channel=self.channel, text=text)

    @staticmethod
    def _send(call: Any, **kwargs: Any) -> None:
        try:
            call(**kwargs)
        except SlackApiError as e:
            raise DeliveryError(f"Slack: {e.response.get('error', e)}") from e
        except OSError as e:
            raise DeliveryError(f"Slack unreachable: {e}") from e
