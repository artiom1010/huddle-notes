import numpy as np
from conftest import FakeEmbedder, group, unit, words

from huddle_notes.attribution import SpeakerAttributor
from huddle_notes.domain import Basis, NamedBy, Turn

AVATARS = {"Аня": [[0.0] * 192], "Борис": [[100.0] * 192]}
E1, E2 = unit(1, 0, 0), unit(0, 1, 0)
# what the call's embeddings become once the voice all of them share is subtracted
ANNA_VOICE, BORIS_VOICE = unit(1, -1, 0), unit(-1, 1, 0)


def silent(_t: float) -> None:
    return None


def test_names_speakers_by_their_lit_tile_and_avatar():
    anna, boris = group(1, [(0, 4)], fingerprint=0.0), group(2, [(6, 10)], fingerprint=100.0)
    result = SpeakerAttributor().attribute(
        words(0, 3.5) + words(6, 9.5), [anna, boris], FakeEmbedder(silent), AVATARS, {}, {}
    )
    assert [t.who for t in result.turns()] == ["Аня", "Борис"]
    assert result.names[1].by == NamedBy.AVATAR
    assert result.share(Basis.TILE) == 1.0


def test_a_short_unlit_pause_stays_with_the_speaker():
    anna = group(1, [(0, 2), (4.5, 7)], fingerprint=0.0)
    result = SpeakerAttributor().attribute(words(0, 6.5), [anna], FakeEmbedder(silent), AVATARS, {}, {})
    assert result.turns() == [Turn(0, "Аня", " ".join(["слово"] * 13))]


def test_names_given_by_hand_win_and_strangers_are_marked():
    known, stranger = group(1, [(0, 4)], fingerprint=0.0), group(2, [(6, 10)], fingerprint=50.0)
    result = SpeakerAttributor().attribute(
        words(0, 3.5) + words(6, 9.5), [known, stranger], FakeEmbedder(silent), AVATARS, {}, {1: "Анна"}
    )
    assert result.names[1].by == NamedBy.MANUAL
    assert result.names[2].by == NamedBy.UNKNOWN
    assert [t.who for t in result.turns()] == ["Анна", "Участник 2?"]


def test_voice_names_speech_where_no_tile_was_lit():
    # Аня under her tile, then a stretch with no lit tile in Борис's voice, then Борис's tile
    anna, boris = group(1, [(0, 6.5)], fingerprint=0.0), group(2, [(20, 26.5)], fingerprint=100.0)
    embedder = FakeEmbedder(lambda t: E1 if t < 9 else E2)
    result = SpeakerAttributor().attribute(
        words(0, 6) + words(10, 17) + words(20, 26),
        [anna, boris],
        embedder,
        AVATARS,
        {"Аня": ANNA_VOICE, "Борис": BORIS_VOICE},
        {},
    )
    assert [(t.start, t.who) for t in result.turns()] == [(0, "Аня"), (10, "Борис")]
    assert result.share(Basis.VOICE) > 0


def test_a_voice_that_clearly_contradicts_the_lit_tile_is_flagged():
    anna, boris = group(1, [(0, 6.5), (30, 36.5)], fingerprint=0.0), group(2, [(20, 26.5)], fingerprint=100.0)
    # at 30-36 Аня's tile is lit, but the voice is Борис's
    embedder = FakeEmbedder(lambda t: E1 if t < 9 else E2)
    result = SpeakerAttributor().attribute(
        words(0, 6) + words(20, 26) + words(30, 36),
        [anna, boris],
        embedder,
        AVATARS,
        {"Аня": ANNA_VOICE, "Борис": BORIS_VOICE},
        {},
    )
    assert [t.who for t in result.turns()] == ["Аня", "Борис", "? (Аня или Борис)"]


def test_ties_between_neighbouring_voices_never_depend_on_chance():
    smooth = SpeakerAttributor()._smooth
    assert smooth(["A", "B"]) == ["A", "B"]  # a tie goes to the word's own voice
    assert smooth([None, "B", "A"]) == ["B", "B", "A"]  # ...else to the nearest neighbour's
    assert smooth(["A", "A", "B", None]) == ["A", "A", "A", "A"]
    assert smooth([None, None]) == [None, None]


def test_learns_only_people_named_with_confidence():
    known, stranger = group(1, [(0, 4)], fingerprint=0.0), group(2, [(6, 10)], fingerprint=50.0)
    attributor = SpeakerAttributor()
    result = attributor.attribute(
        words(0, 3.5) + words(6, 9.5), [known, stranger], FakeEmbedder(lambda t: E1 if t < 5 else E2), AVATARS, {}, {}
    )
    sightings = attributor.sightings(result)
    assert [s.name for s in sightings] == ["Аня"]
    assert len(sightings[0].voice_stretches) == 1
    assert np.allclose(sightings[0].voice_stretches[0], ANNA_VOICE)


def test_a_call_with_one_voice_gives_no_voiceprint_rather_than_nan():
    anna = group(1, [(0, 4)], fingerprint=0.0)
    attributor = SpeakerAttributor()
    result = attributor.attribute(words(0, 3.5), [anna], FakeEmbedder(lambda t: E1), AVATARS, {}, {})
    assert all(c.emb is None for c in result.chunks)
    assert attributor.sightings(result)[0].voice_stretches == []
    assert [t.who for t in result.turns()] == ["Аня"]
