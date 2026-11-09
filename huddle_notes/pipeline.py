"""One recording in, one document out: the call's notes and its named transcript.

Every step keeps its result in the recording's work folder, so a failed or repeated run
picks up where it stopped instead of redoing the slow parts (whisper, video, Claude).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path

from huddle_notes.attribution import Attribution, Embedder, SpeakerAttributor
from huddle_notes.domain import Crop, Detection, NamedBy
from huddle_notes.media import Media, MediaError
from huddle_notes.notes import NotesError, NotesWriter
from huddle_notes.people import PeopleLibrary
from huddle_notes.render import CallInfo, call_document, participants, report, transcript_markdown
from huddle_notes.speakers.detector import SpeakerDetector
from huddle_notes.transcribe import WhisperTranscriber, read_words

log = logging.getLogger(__name__)

UNKNOWN_FACES = "Не опознаны"


@dataclass(frozen=True)
class CallResult:
    document: Path
    info: CallInfo
    attribution: Attribution
    report: str
    notes: str | None = None
    notes_error: str | None = None  # why there are no notes, when there are none
    unknown_faces: list[Path] = field(default_factory=list)


class CallProcessor:
    def __init__(
        self,
        media: Media,
        transcriber: WhisperTranscriber,
        detector: SpeakerDetector,
        attributor: SpeakerAttributor,
        library: PeopleLibrary,
        embedder_for: Callable[[Path], Embedder],
        work_root: Path,
        results_dir: Path,
        notes: NotesWriter | None = None,
        timezone: tzinfo = UTC,
    ) -> None:
        self.media = media
        self.transcriber = transcriber
        self.detector = detector
        self.attributor = attributor
        self.library = library
        self.embedder_for = embedder_for
        self.work_root = work_root
        self.results_dir = results_dir
        self.notes = notes
        self.timezone = timezone

    def process(self, video: Path, glossary: str, manual: dict[int, str], learn: bool = False) -> CallResult:
        work = self.work_root / _safe_name(video.stem)
        work.mkdir(parents=True, exist_ok=True)

        audio = work / "audio.wav"
        if not audio.exists():
            log.info("%s: extracting audio", video.name)
            self._atomic(audio, lambda tmp: self.media.extract_audio(video, tmp))

        words_json = work / "words.json"
        if not words_json.exists():
            log.info("%s: transcribing", video.name)
            done = self.transcriber.transcribe(audio, work / "partial-words", glossary, work / "whisper.log")
            done.replace(words_json)

        speakers_json = work / "speakers.json"
        if not speakers_json.exists():
            log.info("%s: finding speakers on video", video.name)
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
        turns = result.turns()
        info = CallInfo(self._started(video, detection), detection.frames / detection.fps, participants(turns))
        notes, notes_error = self._notes(work, transcript_markdown(turns), glossary)

        self.results_dir.mkdir(parents=True, exist_ok=True)
        document = self._document(work, info.file_name)
        self._atomic(document, lambda tmp: _write_text(tmp, call_document(info, turns, notes)))
        faces = self._save_unknown_faces(detection, result, self.results_dir / UNKNOWN_FACES / document.stem)
        if learn:
            self._learn(detection, result)
        return CallResult(document, info, result, report(result, len(words)), notes, notes_error, faces)

    def _document(self, work: Path, name: str) -> Path:
        """The call's file in the results. A recording keeps the file it got the first time;
        another recording that started in the same minute gets "<name> (2)", not its file."""
        record = work / "document.txt"
        if record.exists():
            return self.results_dir / record.read_text(encoding="utf-8").strip()
        stem, n = Path(name).stem, 1
        while (self.results_dir / name).exists():
            n += 1
            name = f"{stem} ({n}).md"
        self._atomic(record, lambda tmp: _write_text(tmp, name))
        return self.results_dir / name

    def _notes(self, work: Path, transcript: str, glossary: str) -> tuple[str | None, str | None]:
        """Claude's notes, kept per transcript: the same transcript never costs a second request.
        A failure leaves the call without notes rather than without a result."""
        if self.notes is None:
            return None, None
        key = hashlib.sha256(f"{self.notes.model}\n{glossary}\n{transcript}".encode()).hexdigest()[:16]
        cached = work / f"notes-{key}.md"
        if cached.exists():
            return cached.read_text(encoding="utf-8"), None
        log.info("writing notes with %s", self.notes.model)
        try:
            notes = self.notes.write(transcript, glossary)
        except NotesError as e:
            log.warning("no notes: %s", e)
            return None, str(e)
        self._atomic(cached, lambda tmp: _write_text(tmp, notes))
        return notes, None

    def _started(self, video: Path, detection: Detection) -> datetime:
        """When the call started: from the recording's own metadata, else the file's time
        (written when recording stopped) minus its length."""
        try:
            started = self.media.recorded_at(video)
        except MediaError:
            started = None
        if started is None:
            ended = datetime.fromtimestamp(video.stat().st_mtime, UTC)
            started = ended - timedelta(seconds=detection.frames / detection.fps)
        return started.astimezone(self.timezone)

    def _save_unknown_faces(self, detection: Detection, result: Attribution, folder: Path) -> list[Path]:
        """Faces nobody could name, so a person can name them. Faces from an earlier run
        of this call go first: whoever was named since is no longer unknown."""
        video = Path(detection.video)
        for old in folder.glob("*.jpg"):
            old.unlink()
        saved = []
        for g in result.groups:
            name = result.names[g.id]
            if name.by != NamedBy.UNKNOWN:
                continue
            folder.mkdir(parents=True, exist_ok=True)
            face = _face_file(folder, name.name.rstrip("?"))
            if self._save_face(video, g.sample_t, g.crop, face):
                saved.append(face)
        return saved

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

    def _save_face(self, video: Path, t: float, crop: Crop, path: Path) -> bool:
        try:
            self.media.save_crop(video, t, crop, path)
            return True
        except MediaError as e:  # a missing face never costs the transcript
            log.warning("no face for %s: %s", path.stem, e)
            return False

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


def _safe_name(name: str) -> str:
    """Typed or recorded names become file names, never paths."""
    return re.sub(r"[^\w .-]", "_", name).strip(" ._") or "unnamed"


def _face_file(folder: Path, name: str) -> Path:
    """A person's picture: the name is typed by people, so it never becomes a path."""
    safe = re.sub(r"[^\w -]", "_", name).strip(" _") or "unnamed"
    return folder / f"{safe}.jpg"
