"""Text for people: the one document per call (notes, then the named transcript) and a
short report on how names were decided."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from huddle_notes.attribution import Attribution
from huddle_notes.domain import Basis, NamedBy, Turn

MONTHS = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)


@dataclass(frozen=True)
class CallInfo:
    started: datetime
    duration: float  # seconds
    participants: list[str]

    @property
    def when(self) -> str:
        s = self.started
        return f"{s.day} {MONTHS[s.month - 1]} {s.year}, {s:%H:%M}"

    @property
    def file_name(self) -> str:
        return f"{self.started:%Y-%m-%d %H-%M}.md"


def hms(sec: float) -> str:
    return f"{int(sec // 3600):02d}:{int(sec % 3600 // 60):02d}:{int(sec % 60):02d}"


def transcript_markdown(turns: list[Turn]) -> str:
    return "".join(f"[{hms(t.start)}] **{t.who}:** {t.text}\n\n" for t in turns)


def report(result: Attribution, words: int) -> str:
    """How much was decided by tile, by voice and left in doubt; how each group was named."""
    lines = [
        f"words {words}, chunks {len(result.chunks)}; "
        f"by tile {100 * result.share(Basis.TILE):.1f}%, "
        f"by voice {100 * result.share(Basis.VOICE):.1f}%, "
        f"doubtful '?' {100 * result.share(Basis.DOUBT):.1f}%"
    ]
    for g in result.groups:
        n = result.names[g.id]
        lines.append(f"  group {g.id}: {n.name} — {n.detail}; voice says {n.voice[0]} {n.voice[1]}")
    if any(n.by == NamedBy.UNKNOWN for n in result.names.values()):
        lines.append('name them: huddle-notes process <video> --names \'{"<group>": "Имя"}\' --learn')
    return "\n".join(lines)


def call_document(info: CallInfo, turns: list[Turn], notes: str | None = None) -> str:
    """The one file a call ends with, as in Gemini notes: the notes first, then the transcript."""
    minutes = max(1, round(info.duration / 60))
    head = f"# Звонок {info.when}\n\n{info.when} · {minutes} мин · Участники: {', '.join(info.participants)}\n\n"
    body = f"## Заметки\n\n{notes.strip()}\n\n" if notes else ""
    return f"{head}{body}## Расшифровка\n\n{transcript_markdown(turns)}"


def participants(turns: list[Turn]) -> list[str]:
    """Everyone named in the transcript, in order of first word; '?' is nobody."""
    seen: list[str] = []
    for t in turns:
        if not t.who.startswith("?") and t.who not in seen:
            seen.append(t.who)
    return seen
