"""One recording in, a named transcript out.

Every step keeps its result in <recording>/_work, so a failed or repeated run picks
up where it stopped instead of redoing the slow parts (whisper, video).
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from huddle_notes.attribution import Attribution, Embedder, SpeakerAttributor
from huddle_notes.domain import Crop, Detection, NamedBy
from huddle_notes.media import Media, MediaError
from huddle_notes.people import PeopleLibrary
from huddle_notes.render import report, transcript_markdown
from huddle_notes.speakers.detector import SpeakerDetector
from huddle_notes.transcribe import WhisperTranscriber, read_words

log = logging.getLogger(__name__)

TRANSCRIPT = "Расшифровка.md"
UNKNOWN_FACES = "Не опознаны"


@dataclass(frozen=True)
class CallResult:
    transcript: Path
    attribution: Attribution
    report: str


class CallProcessor:
    def __init__(
        self,
        media: Media,
        transcriber: WhisperTranscriber,
        detector: SpeakerDetector,
        attributor: SpeakerAttributor,
        library: PeopleLibrary,
        embedder_for: Callable[[Path], Embedder],
    ) -> None:
        self.media = media
        self.transcriber = transcriber
        self.detector = detector
        self.attributor = attributor
        self.library = library
        self.embedder_for = embedder_for

    def process(self, video: Path, glossary: str, manual: dict[int, str], learn: bool = False) -> CallResult:
        if not video.suffix:
            raise ValueError(f"{video.name}: a recording needs a file extension (.mov, .mp4)")
        out = video.with_suffix("")
        work = out / "_work"
        work.mkdir(parents=True, exist_ok=True)

        audio = work / "audio.wav"
        if not audio.exists():
            log.info("extracting audio")
            self._atomic(audio, lambda tmp: self.media.extract_audio(video, tmp))

        words_json = work / "words.json"
        if not words_json.exists():
            log.info("transcribing")
            done = self.transcriber.transcribe(audio, work / "partial-words", glossary, work / "whisper.log")
            done.replace(words_json)

        speakers_json = work / "speakers.json"
        if not speakers_json.exists():
            log.info("finding speakers on video")
            detection = self.detector.detect(video)
            self._atomic(speakers_json, lambda tmp: _write_json(tmp, detection.to_dict()))
        detection = Detection.from_dict(json.loads(speakers_json.read_text(encoding="utf-8")))

        words = read_words(words_json)
        result = self.attributor.attribute(
            words,
            detection.groups,
            self.embedder_for(audio),
            self.library.avatars,
            self.library.voiceprints(),
            manual,
        )
        transcript = out / TRANSCRIPT
        self._atomic(transcript, lambda tmp: _write_text(tmp, transcript_markdown(result.turns())))
        self._save_unknown_faces(detection, result, out / UNKNOWN_FACES)
        if learn:
            self._learn(detection, result)
        return CallResult(transcript, result, report(result, len(words)))

    def _save_unknown_faces(self, detection: Detection, result: Attribution, folder: Path) -> None:
        """Faces nobody could name, so a person can name them with --names."""
        video = Path(detection.video)
        for g in result.groups:
            name = result.names[g.id]
            if name.by != NamedBy.UNKNOWN:
                continue
            folder.mkdir(exist_ok=True)
            self._save_face(video, g.sample_t, g.crop, _face_file(folder, name.name.rstrip("?")))

    def _learn(self, detection: Detection, result: Attribution) -> None:
        video = Path(detection.video)
        self.library.faces.mkdir(parents=True, exist_ok=True)
        for g in result.groups:
            name = result.names[g.id]
            face = _face_file(self.library.faces, name.name)
            if name.by in (NamedBy.MANUAL, NamedBy.AVATAR) and not face.exists():
                self._save_face(video, g.sample_t, g.crop, face)
        self.library.learn(self.attributor.sightings(result))
        self.library.save()

    def _save_face(self, video: Path, t: float, crop: Crop, path: Path) -> None:
        try:
            self.media.save_crop(video, t, crop, path)
        except MediaError as e:  # a missing face never costs the transcript
            log.warning("no face for %s: %s", path.stem, e)

    @staticmethod
    def _atomic(path: Path, make: Callable[[Path], None]) -> None:
        """A step's file appears only when complete, so a crash is redone on the next run."""
        tmp = path.with_name(f"partial-{path.name}")
        make(tmp)
        tmp.replace(path)


def _write_json(path: Path, data: object) -> None:
    _write_text(path, json.dumps(data, ensure_ascii=False))


def _write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _face_file(folder: Path, name: str) -> Path:
    """A person's picture: the name is typed by people, so it never becomes a path."""
    safe = re.sub(r"[^\w -]", "_", name).strip(" _") or "unnamed"
    return folder / f"{safe}.jpg"
