"""Check that a new version names speakers exactly as the accepted one did.

A regression folder (kept out of git: recordings and voices) holds one folder per call
with words.json, speakers.json, audio.wav, names.json and the accepted expected.md,
plus people/: the library as it was when expected.md was accepted.
"""

from __future__ import annotations

import difflib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from huddle_notes.attribution import Embedder, SpeakerAttributor
from huddle_notes.domain import Detection
from huddle_notes.people import PeopleLibrary
from huddle_notes.render import report, transcript_markdown
from huddle_notes.transcribe import read_words

CASE_FILES = ("words.json", "speakers.json", "audio.wav", "names.json", "expected.md")


@dataclass(frozen=True)
class CaseResult:
    name: str
    same: bool
    report: str
    diff: list[str]


def run_regression(
    root: Path,
    attributor: SpeakerAttributor,
    embedder_for: Callable[[Path], Embedder],
    accept: bool = False,
) -> list[CaseResult]:
    """Every case in root; with accept, the new output becomes the expected one."""
    library = PeopleLibrary.load(root / "people")
    cases = sorted(p for p in root.iterdir() if p.is_dir() and p.name != "people")
    for case in cases:  # a missing file must fail the check, not quietly shrink it
        if missing := [f for f in CASE_FILES if not (case / f).exists()]:
            raise FileNotFoundError(f"regression case {case.name} lacks {', '.join(missing)}")
    if not cases:
        raise FileNotFoundError(f"no regression cases in {root}")
    results = []
    for case in cases:
        words = read_words(case / "words.json")
        detection = Detection.from_dict(json.loads((case / "speakers.json").read_text(encoding="utf-8")))
        manual = {int(k): v for k, v in json.loads((case / "names.json").read_text(encoding="utf-8")).items()}
        result = attributor.attribute(
            words, detection.groups, embedder_for(case / "audio.wav"), library.avatars, library.voiceprints(), manual
        )
        got = transcript_markdown(result.turns())
        expected = (case / "expected.md").read_text(encoding="utf-8")
        if accept and got != expected:
            (case / "expected.md").write_text(got, encoding="utf-8")
        diff = list(difflib.unified_diff(expected.splitlines(), got.splitlines(), "expected", "new", lineterm=""))
        results.append(CaseResult(case.name, got == expected, report(result, len(words)), diff))
    return results
