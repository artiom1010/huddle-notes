import numpy as np
from conftest import frame

from huddle_notes.speakers.tiles import THUMB, fingerprint, fingerprint_distance, run_length, speaking_tiles


def test_finds_the_lit_tile_and_traces_its_border():
    # the corner is the bottom-left border pixel; top and right include Slack's rounded-corner margins
    assert speaking_tiles(frame(tile=(100, 100, 300, 250))) == [(100, 94, 307, 250)]


def test_ignores_a_tile_that_is_not_speaking():
    assert speaking_tiles(frame(border=False, pill=False)) == []


def test_needs_the_level_pill_not_just_a_white_border():
    assert speaking_tiles(frame(pill=False)) == []
    assert speaking_tiles(frame(bars=False)) == []


def test_finds_one_tile_per_speaker_anywhere_on_screen():
    fr = frame(tile=(100, 100, 300, 250))
    fr[:, 480:] = frame(tile=(600, 200, 800, 350))[:, 480:]
    assert speaking_tiles(fr) == [(100, 94, 307, 250), (600, 194, 807, 350)]


def test_run_length_steps_over_short_breaks_but_not_the_gap_between_tiles():
    line = np.zeros(40, bool)
    line[0:10] = line[12:20] = True  # a 2 px break: same border
    line[30:40] = True  # 10 px gap: the next tile
    assert run_length(line) == 20
    assert run_length(np.zeros(10, bool)) == 0


def test_fingerprint_is_the_avatar_colour_and_distance_tells_avatars_apart():
    a = fingerprint(frame(avatar=(90, 60, 120)), (100, 100, 300, 250))
    b = fingerprint(frame(avatar=(200, 150, 40)), (100, 100, 300, 250))
    assert a.shape == (THUMB * THUMB * 3,)
    assert np.allclose(a.reshape(-1, 3), (90, 60, 120))
    assert fingerprint_distance(a, a.tolist()) == 0.0
    assert fingerprint_distance(a, b) > 50
