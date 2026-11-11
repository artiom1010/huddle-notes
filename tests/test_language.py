"""The call's language: set in .env, or heard by whisper in the middle of the call."""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from test_pipeline import FakeNotes, whisper_json
from test_speakers_off import MediaWithLength

from huddle_notes.media import Media
from huddle_notes.pipeline import CallProcessor
from huddle_notes.transcribe import WhisperTranscriber


class Whisper:
    """Whisper that hears a given language in the sample and writes a one-word transcript."""

    def __init__(self, language: str, hears: tuple[str, float] | None) -> None:
        self.language = language
        self.hears = hears
        self.samples: list[Path] = []
        self.given: list[str | None] = []

    def detect_language(self, sample: Path) -> tuple[str, float] | None:
        assert sample.exists()
        self.samples.append(sample)
        return self.hears

    def transcribe(self, audio: Path, out_prefix: Path, prompt: str, log_file: Path, language: str | None) -> Path:
        self.given.append(language)
        done = out_prefix.with_suffix(".json")
        done.write_text(json.dumps(whisper_json([(0.0, 1.0, [(0.1, "привет")])])), encoding="utf-8")
        return done


class Sampling(MediaWithLength):
    """Media that also extracts the audio and cuts the language sample."""

    def __init__(self, fails: bool = False) -> None:
        self.fails = fails
        self.lengths: list[float] = []

    def extract_audio(self, video: Path, wav: Path) -> None:
        wav.write_bytes(b"wav")

    def language_sample(self, audio: Path, out: Path, length: float) -> None:
        if self.fails:
            raise ValueError("no audio")
        self.lengths.append(length)
        out.write_bytes(b"sample")


def run(tmp_path: Path, whisper: Whisper, media: Sampling) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    video = tmp_path / "call.mov"
    video.write_bytes(b"")
    CallProcessor(
        media=media,  # type: ignore[arg-type]
        transcriber=whisper,  # type: ignore[arg-type]
        detector=None,  # type: ignore[arg-type]
        attributor=None,  # type: ignore[arg-type]
        library=None,  # type: ignore[arg-type]
        embedder_for=None,  # type: ignore[arg-type]
        work_root=tmp_path / "work",
        results_dir=tmp_path / "out",
        notes=FakeNotes(),
    ).process(video, "", {})


def test_the_language_is_heard_in_the_middle_of_the_call(tmp_path):
    whisper, media = Whisper("auto", ("ru", 0.99)), Sampling()
    run(tmp_path, whisper, media)
    assert whisper.given == ["ru"] and media.lengths == [125.0]
    assert not whisper.samples[0].exists()  # the sample is not kept


def test_a_language_set_in_env_is_used_as_it_is(tmp_path):
    whisper = Whisper("en", ("ru", 0.99))
    run(tmp_path, whisper, Sampling())
    assert whisper.given == ["en"] and whisper.samples == []


def test_when_the_sample_tells_nothing_whisper_guesses_itself(tmp_path):
    silent = Whisper("auto", None)
    run(tmp_path / "a", silent, Sampling())
    assert silent.given == ["auto"]
    unsampled = Whisper("auto", ("ru", 0.99))
    run(tmp_path / "b", unsampled, Sampling(fails=True))
    assert unsampled.given == ["auto"] and unsampled.samples == []


def test_the_sample_is_three_pieces_from_inside_the_call(monkeypatch):
    seen: list[list[str]] = []
    monkeypatch.setattr(Media, "_run", staticmethod(lambda cmd, timeout: seen.append(cmd) or b""))
    Media().language_sample(Path("a.wav"), Path("s.wav"), 600.0)
    cmd = seen[0]
    starts = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-ss"]
    assert starts == ["115.00", "295.00", "475.00"]  # around 20, 50 and 80 %: past a silent start
    assert cmd[cmd.index("-filter_complex") + 1] == "[0:a][1:a][2:a]concat=n=3:v=0:a=1"
    assert cmd[-3:] == ["-t", "30.00", "s.wav"]  # never longer than the pieces
    Media().language_sample(Path("a.wav"), Path("s.wav"), 20.0)
    assert [seen[1][i + 1] for i, a in enumerate(seen[1]) if a == "-ss"] == ["0.00", "5.00", "10.00"]


def test_whisper_says_which_language_it_heard(monkeypatch):
    said = b"whisper_full_with_state: auto-detected language: ru (p = 0.999248)\n"

    def whisper(cmd: list[str], **kw: Any) -> subprocess.CompletedProcess[bytes]:
        assert cmd[cmd.index("-l") + 1] == "auto" and "-dl" in cmd
        assert "-np" not in cmd  # it would silence the very line that names the language
        return subprocess.CompletedProcess(cmd, 0, b"", said)

    monkeypatch.setattr("huddle_notes.transcribe.subprocess.run", whisper)
    w = WhisperTranscriber("whisper-cli", Path("m.bin"), "large.v3.turbo")
    assert w.detect_language(Path("s.wav")) == ("ru", 0.999248)
    said = b"error: failed to open 's.wav'\n"
    assert w.detect_language(Path("s.wav")) is None

    def missing(cmd: list[str], **kw: Any) -> None:
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr("huddle_notes.transcribe.subprocess.run", missing)
    assert w.detect_language(Path("s.wav")) is None


def test_a_call_transcribed_before_is_not_listened_to_again(tmp_path):
    whisper = Whisper("auto", ("ru", 0.99))
    run(tmp_path, whisper, Sampling())
    run(tmp_path, whisper, Sampling())  # words.json is there: no sample, no second transcription
    assert whisper.given == ["ru"] and len(whisper.samples) == 1


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_the_sample_is_real_audio_whisper_can_read(tmp_path):
    audio, sample = tmp_path / "call.wav", tmp_path / "sample.wav"
    tone = ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=95", "-ar", "16000"]
    subprocess.run([*tone, str(audio)], check=True)
    media = Media()
    media.language_sample(audio, sample, media.duration(audio))
    assert media.duration(sample) == pytest.approx(30.0, abs=0.1)
    short = tmp_path / "short.wav"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(audio), "-t", "4", str(short)], check=True)
    media.language_sample(short, sample, media.duration(short))  # shorter than a piece: still a sample
    assert 0 < media.duration(sample) <= 12.1
