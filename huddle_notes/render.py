"""Text for people: the named transcript and a short report on how names were decided."""

from __future__ import annotations

from huddle_notes.attribution import Attribution
from huddle_notes.domain import Basis, NamedBy, Turn


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
