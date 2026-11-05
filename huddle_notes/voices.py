"""Voice fingerprints: a 256-number embedding per stretch of speech.

Two stretches of the same voice point the same way (cosine close to 1), different
voices point elsewhere. A person's voiceprint is the average of many stretches.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import numpy as np
import sherpa_onnx
import soundfile

from huddle_notes.domain import Vector

RATE = 16000


class VoiceEmbedder:
    """Embeddings of stretches of one recording."""

    def __init__(self, wav: Path, model: Path, threads: int = 4) -> None:
        self.audio, rate = soundfile.read(str(wav), dtype="float32")
        if rate != RATE:
            raise ValueError(f"{wav}: expected {RATE} Hz mono, got {rate} Hz")
        cfg = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(model), num_threads=threads)
        self.extractor = sherpa_onnx.SpeakerEmbeddingExtractor(cfg)

    def embed(self, start: float, end: float) -> Vector | None:
        """Unit-length embedding of audio between start and end (seconds), None if too short."""
        stream = self.extractor.create_stream()
        stream.accept_waveform(RATE, self.audio[int(start * RATE) : int(end * RATE)])
        stream.input_finished()
        if not self.extractor.is_ready(stream):
            return None
        v = np.array(self.extractor.compute(stream), np.float32)
        return v / np.linalg.norm(v)


def voiceprint(embeddings: Sequence[Vector]) -> Vector:
    v = np.mean(embeddings, axis=0)
    return v / np.linalg.norm(v)


def best_match(v: Vector, prints: dict[str, Vector]) -> tuple[str | None, float, float]:
    """(name, score, runner-up score) for embedding v against {name: voiceprint}."""
    scores = sorted(((float(v @ p), name) for name, p in prints.items()), reverse=True)
    if not scores:
        return None, 0.0, 0.0
    return scores[0][1], scores[0][0], scores[1][0] if len(scores) > 1 else 0.0
