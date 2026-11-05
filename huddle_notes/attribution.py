"""Who said each word, from lit tiles, avatars and voices.

1. Whoever's tile was lit at that moment (Slack's indicator runs late at both ends),
   recognised by avatar against the library — never by tile position.
2. The voice where no tile was lit, and as a check: a lit tile that the voice
   clearly contradicts becomes "? (A или B)".
3. "?" whenever neither is sure.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

from huddle_notes.domain import Basis, GroupName, NamedBy, SpeakerGroup, Turn, Vector, Word
from huddle_notes.people import Sighting
from huddle_notes.speakers.tiles import fingerprint_distance
from huddle_notes.voices import best_match, voiceprint


@dataclass(frozen=True)
class Tuning:
    lag_on: float = 0.25  # indicator lights up ~0.25 s after the voice starts...
    lag_off: float = 0.75  # ...and stays lit ~1 s after it stops (measured on a real call)
    tol: float = 0.25  # a word may sit this far outside a lit stretch
    pause_in_turn: float = 3.0  # an unlit pause this short between one person's words is still theirs
    early_start: float = 1.5  # the next speaker talks this long before their tile lights up
    chunk_gap: float = 2.0  # silence that ends a chunk even for the same speaker
    min_frames: int = 3  # avatar groups seen in fewer frames are noise
    name_max: float = 25.0  # avatar distance: trusted match...
    name_margin: float = 10.0  # ...and its lead over the runner-up
    min_embed_sec: float = 1.0  # shorter speech gives no usable voice embedding
    voice_min: float = 0.4  # voice decides on its own above these
    voice_margin: float = 0.1
    veto_min: float = 0.5  # voice overrules a lit tile only this clearly
    veto_margin: float = 0.15
    veto_own: float = 0.2
    min_voice_sec: float = 1.5
    min_local_chunks: int = 3  # stretches needed to build a voiceprint from this call alone
    win: float = 3.0  # voice windows that cut a stretch with no lit tile
    win_min: float = 0.45
    win_margin: float = 0.1
    smooth_words: int = 4  # window labels are smoothed over this many words each side
    min_run_words: int = 4  # shorter voice runs join their neighbour


class Embedder(Protocol):
    def embed(self, start: float, end: float) -> Vector | None: ...


@dataclass
class Chunk:
    """Consecutive words with one tile owner (or none), and what was decided about them."""

    group: int | None
    start: float
    end: float
    words: list[Word]
    emb: Vector | None = None
    is_run: bool = False  # cut by voice windows from a stretch with no lit tile
    lab: str | None = None  # for a run: the voice its windows agree on
    who: str = ""
    why: Basis = Basis.DOUBT

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


@dataclass
class Attribution:
    chunks: list[Chunk]
    groups: list[SpeakerGroup]
    names: dict[int, GroupName]
    seconds: dict[Basis, float] = field(default_factory=dict)

    def turns(self) -> list[Turn]:
        """Chunks of one speaker in a row become one turn."""
        turns: list[Turn] = []
        for c in self.chunks:
            if turns and turns[-1].who == c.who:
                turns[-1] = Turn(turns[-1].start, c.who, f"{turns[-1].text} {c.text}")
            else:
                turns.append(Turn(c.start, c.who, c.text))
        return turns

    def share(self, basis: Basis) -> float:
        total = sum(self.seconds.values())
        return self.seconds.get(basis, 0.0) / total if total else 0.0


class SpeakerAttributor:
    def __init__(self, tuning: Tuning | None = None) -> None:
        self.t = tuning or Tuning()

    def attribute(
        self,
        words: list[Word],
        groups: Sequence[SpeakerGroup],
        embedder: Embedder,
        avatars: dict[str, list[list[float]]],
        voiceprints: dict[str, Vector],
        manual: dict[int, str],
    ) -> Attribution:
        seen = [g for g in groups if g.frames >= self.t.min_frames]
        chunks = self._chunks(words, self._tile_owners(words, seen))
        mean = self._embed(chunks, embedder)
        names = self._name_groups(seen, chunks, avatars, voiceprints, manual)
        prints = self._prints_for_call(names, chunks, voiceprints)
        chunks = self._split_untiled(chunks, embedder, mean, prints)
        return Attribution(chunks, seen, names, self._decide(chunks, names, prints))

    def sightings(self, result: Attribution) -> list[Sighting]:
        """People named by hand or by avatar, with the voice heard under their lit tile."""
        out = []
        for g in result.groups:
            n = result.names[g.id]
            if n.by in (NamedBy.UNKNOWN, NamedBy.VOICE):
                continue
            clean = self._voice(c for c in result.chunks if c.group == g.id and c.why == Basis.TILE)
            out.append(Sighting(n.name, g.fingerprint, clean))
        return out

    # 1. Tile at each word: undo the indicator's delays, allow tol either side
    def _tile_owners(self, words: list[Word], groups: list[SpeakerGroup]) -> list[int | None]:
        t = self.t

        def lit(s: float, e: float) -> int | None:
            best, best_o = None, 0.0
            for g in groups:
                for a, b in g.segments:
                    o = min(e, b - t.lag_off + t.tol) - max(s, a - t.lag_on - t.tol)
                    if o > best_o:
                        best, best_o = g.id, o
            return best

        owner = [lit(w.start, w.end) for w in words]
        for i in range(1, len(owner) - 1):
            if owner[i] is None and owner[i - 1] is not None:
                j = next((j for j in range(i, len(owner)) if owner[j] is not None), None)
                if j is None:
                    continue
                if owner[j] == owner[i - 1] and words[j].start - words[i - 1].end < t.pause_in_turn:
                    owner[i:j] = [owner[i - 1]] * (j - i)
                elif words[j].start - words[i].start < t.early_start:
                    owner[i:j] = [owner[j]] * (j - i)
        return owner

    # 2. Chunks of words with the same tile owner
    def _chunks(self, words: list[Word], owner: list[int | None]) -> list[Chunk]:
        chunks: list[Chunk] = []
        for w, g in zip(words, owner, strict=True):
            if chunks and chunks[-1].group == g and w.start - chunks[-1].end < self.t.chunk_gap:
                chunks[-1].end = w.end
                chunks[-1].words.append(w)
            else:
                chunks.append(Chunk(g, w.start, w.end, [w]))
        return chunks

    # 3. Voices: subtract what every voice in this call shares (codec, mic chain)
    def _embed(self, chunks: list[Chunk], embedder: Embedder) -> Vector | float:
        for c in chunks:
            c.emb = embedder.embed(c.start, c.end) if c.duration >= self.t.min_embed_sec else None
        embs = [c.emb for c in chunks if c.emb is not None]
        mean: Vector | float = np.mean(embs, axis=0) if embs else 0
        for c in chunks:
            c.emb = _centred(c.emb, mean)
        return mean

    # 4. Name each avatar group: by hand, by the avatar library, else by its voice
    def _name_groups(
        self,
        groups: list[SpeakerGroup],
        chunks: list[Chunk],
        avatars: dict[str, list[list[float]]],
        prints: dict[str, Vector],
        manual: dict[int, str],
    ) -> dict[int, GroupName]:
        t = self.t
        names = {}
        for g in groups:
            scores = sorted((min(fingerprint_distance(g.fingerprint, p) for p in ps), n) for n, ps in avatars.items())
            d1, cand = scores[0] if scores else (None, None)
            d2 = scores[1][0] if len(scores) > 1 else None
            own = self._voice(c for c in chunks if c.group == g.id)
            vname, v1, v2 = best_match(voiceprint(own), prints) if own and prints else (None, 0.0, 0.0)
            voice = (vname, round(v1, 2))
            if g.id in manual:
                names[g.id] = GroupName(manual[g.id], NamedBy.MANUAL, "вручную", voice)
            elif cand is not None and d1 is not None and d1 < t.name_max and (d2 is None or d2 - d1 > t.name_margin):
                detail = f"аватарка (расст. {d1:.0f}, след. {d2 or 0:.0f})"
                names[g.id] = GroupName(cand, NamedBy.AVATAR, detail, voice)
            elif vname and v1 >= t.veto_min and v1 - v2 >= t.veto_margin:
                names[g.id] = GroupName(vname, NamedBy.VOICE, f"голос ({v1:.2f}, след. {v2:.2f})", voice)
            else:
                names[g.id] = GroupName(f"Участник {g.id}?", NamedBy.UNKNOWN, "не опознан", voice)
        return names

    def _voice(self, chunks: Iterable[Chunk]) -> list[Vector]:
        """This call's embeddings long enough to judge a voice by."""
        return [c.emb for c in chunks if c.emb is not None and c.duration >= self.t.min_voice_sec]

    # Voices to compare against: only people seen on this call (the library also holds
    # people who weren't there), and for those who spoke under a lit tile here, this
    # call's own recording of their voice: same mic chain and codec, so it beats a print
    # from another call
    def _prints_for_call(
        self, names: dict[int, GroupName], chunks: list[Chunk], prints: dict[str, Vector]
    ) -> dict[str, Vector]:
        group_name = {gid: n.name for gid, n in names.items()}
        present = {n for n in group_name.values() if not n.endswith("?")}
        if not present:
            return prints
        local = {}
        for n in present:
            own = self._voice(c for c in chunks if c.group is not None and group_name.get(c.group) == n)
            if len(own) >= self.t.min_local_chunks:
                local[n] = voiceprint(own)
        return {n: local[n] if n in local else prints[n] for n in present if n in local or n in prints}

    # A stretch with no lit tile (screen share, hidden window) can hold several people:
    # label short overlapping windows by voice, give each word its window's label
    # (smoothed over neighbours), and cut the stretch where the label changes
    def _split_untiled(
        self, chunks: list[Chunk], embedder: Embedder, mean: Vector | float, prints: dict[str, Vector]
    ) -> list[Chunk]:
        t = self.t

        def window_voice(t0: float, t1: float) -> str | None:
            v = _centred(embedder.embed(t0, t1), mean)
            if v is None or not prints:
                return None
            name, s1, s2 = best_match(v, prints)
            return name if s1 >= t.win_min and s1 - s2 >= t.win_margin else None

        out: list[Chunk] = []
        for c in chunks:
            if c.group is not None or c.duration < 2 * t.win:
                out.append(c)
                continue
            marks = [(m + t.win / 2, window_voice(m, m + t.win)) for m in np.arange(c.start, c.end, t.win / 2)]
            labels = [_nearest(marks, (w.start + w.end) / 2) for w in c.words]
            runs: list[Chunk] = []
            for w, lab in zip(c.words, self._smooth(labels), strict=True):
                if runs and runs[-1].lab == lab:
                    runs[-1].end = w.end
                    runs[-1].words.append(w)
                else:
                    runs.append(Chunk(None, w.start, w.end, [w], is_run=True, lab=lab))
            merged: list[Chunk] = []
            for r in runs:  # a few words between two longer runs are window noise, not a new speaker
                if merged and (len(r.words) < t.min_run_words or len(merged[-1].words) < t.min_run_words):
                    keep = merged[-1] if len(merged[-1].words) >= len(r.words) else r
                    prev = merged[-1]
                    merged[-1] = Chunk(None, prev.start, r.end, prev.words + r.words, is_run=True, lab=keep.lab)
                else:
                    merged.append(r)
            for r in merged:
                r.emb = _centred(embedder.embed(r.start, r.end), mean) if r.duration >= t.min_embed_sec else None
            out += merged
        return out

    def _smooth(self, labels: list[str | None]) -> list[str | None]:
        """Each word takes the voice most of its neighbours have. A tie goes to the word's
        own voice, else to the nearest neighbour's: the same call always reads the same."""
        k = self.t.smooth_words
        out: list[str | None] = []
        for i, own in enumerate(labels):
            lo = max(0, i - k)
            near = [(abs(j - i), x) for j, x in enumerate(labels[lo : i + k + 1], start=lo) if x]
            if not near:
                out.append(None)
                continue
            counts = Counter(x for _, x in near)
            tied = {x for x, n in counts.items() if n == max(counts.values())}
            out.append(own if own in tied else min((d, x) for d, x in near if x in tied)[1])
        return out

    # 5. Decide each chunk
    def _decide(
        self, chunks: list[Chunk], names: dict[int, GroupName], prints: dict[str, Vector]
    ) -> dict[Basis, float]:
        t = self.t
        seconds = dict.fromkeys(Basis, 0.0)
        for c in chunks:
            vname, v1, v2 = best_match(c.emb, prints) if c.emb is not None and prints else (None, 0.0, 0.0)
            if c.group is not None:
                tile = names[c.group].name
                own = float(c.emb @ prints[tile]) if c.emb is not None and tile in prints else 1.0
                if (
                    c.duration >= t.min_voice_sec
                    and vname
                    and vname != tile
                    and v1 >= t.veto_min
                    and v1 - v2 >= t.veto_margin
                    and own < t.veto_own
                ):
                    c.who, c.why = f"? ({tile} или {vname})", Basis.DOUBT
                else:
                    c.who, c.why = tile, Basis.TILE
            elif vname and v1 >= t.voice_min and v1 - v2 >= t.voice_margin:
                c.who, c.why = vname, Basis.VOICE
            elif c.lab:  # too short or too mixed to judge whole, but its windows agree
                c.who, c.why = c.lab, Basis.VOICE
            else:
                c.who, c.why = "?", Basis.DOUBT
            seconds[c.why] += c.duration
        return seconds


def _centred(v: Vector | None, mean: Vector | float) -> Vector | None:
    """v without the sound every voice in the call shares, at unit length. None when
    nothing is left: a call with a single voice, or a single stretch of speech."""
    if v is None:
        return None
    d = v - mean
    n = np.linalg.norm(d)
    return d / n if n > 1e-6 else None


def _nearest(marks: list[tuple[float, str | None]], t: float) -> str | None:
    """The label of the voice window centred closest to t."""
    return min(marks, key=lambda m: abs(m[0] - t))[1]
