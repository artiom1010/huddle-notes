"""Speech to words with whisper.cpp, and reading its output back."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from huddle_notes.domain import Word

# The codes whisper.cpp knows (g_lang in src/whisper.cpp); it exits 0 on any other.
_CODES = """
en zh de es ru ko fr ja pt tr pl ca nl ar sv it id hi fi vi he uk el ms cs ro da hu ta no th ur hr
bg lt la mi ml cy sk te fa lv bn sr az sl kn et mk br eu is hy ne mn bs kk sq sw gl mr pa si km sn
yo so af oc ka be tg sd gu am yi lo uz fo ht ps tk nn mt sa lb my bo tl mg as tt haw ln ha ba jw su
yue
"""
LANGUAGES = frozenset(_CODES.split())
DETECTED = re.compile(r"auto-detected language: ([a-z]+) \(p = ([0-9.]+)\)")
DETECT_TIMEOUT = 300  # seconds to load the model and hear one short sample

# whisper's stock phrases for silence, learned from subtitled videos
JUNK = (
    "продолжение следует",
    "субтитры",
    "спасибо за просмотр",
    "подписывайтесь на канал",
    "редактор субтитров",
    "корректор",
)


class TranscriptionError(RuntimeError):
    pass


class WhisperTranscriber:
    def __init__(
        self,
        binary: str,
        model: Path,
        dtw_preset: str,
        language: str = "auto",
        timeout: float = 3 * 3600,
    ) -> None:
        self.binary = binary
        self.model = model
        # auto: the language is found per call (detect_language). Russian forced on an English
        # call, with the Russian glossary as the prompt, made whisper start every line with a
        # word from the prompt and loop on one phrase for the rest of the call.
        self.language = language
        self.dtw_preset = dtw_preset  # must match the model: per-word timing from its attention heads
        self.timeout = timeout

    def command(self, audio: Path, out_prefix: Path, prompt: str, language: str | None = None) -> list[str]:
        # -nfa -dtw: per-word timing, so a phrase can be split where the speaker changes.
        # Not -mc 0: it stops loops but also drops the prompt, punctuation and some speech;
        # read_words throws the repeated segments away instead.
        return [
            self.binary,
            "-m",
            str(self.model),
            "-l",
            language or self.language,
            "-f",
            str(audio),
            "-ojf",
            "-nfa",
            "-dtw",
            self.dtw_preset,
            "-of",
            str(out_prefix),
            "-np",
            *(["--prompt", prompt] if prompt else []),
        ]

    def detect_language(self, sample: Path) -> tuple[str, float] | None:
        """The language whisper hears in a short sample, with its probability; None if it
        can't tell. Whisper itself only listens to the first 30 seconds of a recording."""
        cmd = [self.binary, "-m", str(self.model), "-l", "auto", "-dl", "-f", str(sample)]
        try:
            done = subprocess.run(cmd, capture_output=True, timeout=DETECT_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired):
            return None
        found = DETECTED.search((done.stdout + done.stderr).decode(errors="replace"))
        return (found[1], float(found[2])) if found else None

    def transcribe(
        self, audio: Path, out_prefix: Path, prompt: str, log_file: Path, language: str | None = None
    ) -> Path:
        """Writes <out_prefix>.json and returns its path; language overrides the set one."""
        with log_file.open("wb") as log:
            try:
                subprocess.run(
                    self.command(audio, out_prefix, prompt, language),
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=True,
                    timeout=self.timeout,
                )
            except subprocess.CalledProcessError as e:
                raise TranscriptionError(f"whisper failed (exit {e.returncode}), see {log_file}") from e
            except subprocess.TimeoutExpired as e:
                raise TranscriptionError(f"whisper took longer than {self.timeout:.0f} s") from e
        done = out_prefix.with_suffix(".json")
        if not done.is_file():  # an unknown language: whisper says so and exits 0
            raise TranscriptionError(f"whisper wrote nothing, see {log_file}")
        return done


@dataclass
class _Draft:
    """A word while tokens are still being glued to it; end is set when its segment ends."""

    start: float
    text: str
    end: float | None = None


def language_of(path: Path) -> str:
    """The language whisper transcribed in: the one it detected, or the one it was given."""
    result = json.loads(path.read_text(encoding="utf-8")).get("result") or {}
    return str(result.get("language") or "unknown")


def read_words(path: Path) -> list[Word]:
    """Words with their timing; repeated segments (whisper loops) and stock phrases dropped."""
    out: list[_Draft] = []
    recent: list[str] = []
    for seg in json.loads(path.read_text(encoding="utf-8"))["transcription"]:
        key = re.sub(r"[^а-яёa-z0-9 ]", "", seg["text"].strip().lower()).strip()[:30]
        if len(key) > 10 and key in recent:  # whisper repeating itself, usually over silence
            continue
        recent = [*recent, key][-3:]
        first = len(out)
        toks = [t for t in seg["tokens"] if not t["text"].startswith("[_")]
        cut = _junk_positions("".join(t["text"] for t in toks).lower())
        pos = 0
        for tok in toks:
            span = range(pos, pos + len(tok["text"]))
            pos += len(tok["text"])
            if tok["text"].strip() and all(i in cut or tok["text"][i - span.start].isspace() for i in span):
                continue
            t = tok["t_dtw"] / 100 if tok.get("t_dtw", -1) >= 0 else tok["offsets"]["from"] / 1000
            if tok["text"].startswith(" ") or not out or out[-1].end is not None:
                out.append(_Draft(t, tok["text"]))
            else:
                out[-1].text += tok["text"]
        for w in out[first:]:
            if w.end is None:
                w.end = seg["offsets"]["to"] / 1000
        # a word ends no later than the next one starts, the previous segment's last word included
        for a, b in zip(out[max(0, first - 1) :], out[max(1, first) :], strict=False):
            a.end = min(_end(a), max(b.start, a.start))
    return [Word(w.start, max(_end(w), w.start + 0.05), w.text.strip()) for w in out if w.text.strip()]


def _end(w: _Draft) -> float:
    if w.end is None:
        raise ValueError(f"word {w.text!r} has no end")
    return w.end


def _junk_positions(low: str) -> set[int]:
    """Character positions covered by a stock phrase and its trailing dots."""
    cut: set[int] = set()
    for junk in JUNK:
        for m in re.finditer(re.escape(junk) + r"[\s.,!?…]*", low):
            cut.update(range(m.start(), m.end()))
    return cut
