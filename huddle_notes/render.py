"""Text for people: the one document per call (notes, then the transcript) and a short
report on how names were decided, when they are."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from huddle_notes.attribution import Attribution
from huddle_notes.domain import Basis, NamedBy, Turn, Word
from huddle_notes.notes import split_title

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
    def minutes(self) -> int:
        return max(1, round(self.duration / 60))

    @property
    def byline(self) -> str:
        who = f" · Участники: {', '.join(self.participants)}" if self.participants else ""
        return f"{self.when} · {self.minutes} мин{who}"

    @property
    def file_name(self) -> str:
        return f"{self.started:%Y-%m-%d %H-%M}.md"


def hms(sec: float) -> str:
    return f"{int(sec // 3600):02d}:{int(sec % 3600 // 60):02d}:{int(sec % 60):02d}"


def transcript_markdown(turns: list[Turn]) -> str:
    """One paragraph per turn; the speaker's name in front when there is one."""
    return "".join(f"[{hms(t.start)}] {f'**{t.who}:** ' if t.who else ''}{t.text}\n\n" for t in turns)


SENTENCE_END = (".", "?", "!", "…")


def paragraphs(words: list[Word], pause: float = 2.0, longest: float = 45.0) -> list[Turn]:
    """A transcript without names: a new paragraph after a pause, and once it runs past
    `longest` seconds, at the next end of a sentence (or at twice that, if none comes),
    so a long stretch still gets times to find it by and no sentence is cut in two."""
    turns: list[Turn] = []
    start = end = 0.0
    for w in words:
        if turns:
            ran = w.end - start
            full_stop = turns[-1].text.endswith(SENTENCE_END)
            if w.start - end < pause and (ran <= longest or (not full_stop and ran <= 2 * longest)):
                turns[-1] = Turn(start, "", f"{turns[-1].text} {w.text}")
                end = w.end
                continue
        start = w.start
        turns.append(Turn(start, "", w.text))
        end = w.end
    return turns


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
        lines.append('name them: huddle-notes process <video> --names "<group>=Имя" --learn')
    return "\n".join(lines)


def call_document(info: CallInfo, turns: list[Turn], notes: str | None = None) -> str:
    """The one file a call ends with, as in Gemini notes: the notes first, then the transcript.
    The notes' own '# Тема' line becomes the document's title."""
    title, body = split_title(notes) if notes else (None, "")
    head = f"# {title or 'Звонок ' + info.when}\n\n{info.byline}\n\n"
    notes_part = f"## Заметки\n\n{body}\n\n" if body else ""
    return f"{head}{notes_part}## Расшифровка\n\n{transcript_markdown(turns)}"


def participants(turns: list[Turn]) -> list[str]:
    """Everyone named in the transcript, in order of first word; '?' and no name are nobody."""
    seen: list[str] = []
    for t in turns:
        if t.who and not t.who.startswith("?") and t.who not in seen:
            seen.append(t.who)
    return seen
