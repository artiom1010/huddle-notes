import numpy as np
from conftest import frame

from huddle_notes.speakers.tiles import THUMB, Frame, fingerprint, fingerprint_distance, run_length, speaking_tiles


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


def test_a_white_photo_above_does_not_make_the_tile_taller():
    # the neighbour above has a white background and the speaker's glow bridges the gap:
    # the left border seems to run on through it, but the tile ends at its own top border
    fr = frame(tile=(100, 200, 250, 350))
    fr[40:197, 100:251] = 255
    assert speaking_tiles(fr) == [(100, 194, 257, 350)]


def tooltip_and_white_photo_above() -> Frame:
    fr = frame(tile=(100, 200, 250, 350))
    fr[40:197, 100:251] = 255  # the neighbour's white photo, as above
    fr[40:60, 170:251] = (90, 60, 120)  # something dark at its top right
    fr[335:360, 140:300] = (40, 40, 40)  # "Unmute mic" over the bottom border, right of the pill
    return fr


def test_a_tooltip_over_the_border_does_not_make_the_tile_narrower():
    # both borders the width was measured on are cut; the tile's own top border is whole
    assert speaking_tiles(tooltip_and_white_photo_above()) == [(100, 194, 257, 350)]


def test_a_wider_or_shifted_white_photo_above_is_not_mistaken_for_the_tile():
    fr = frame(tile=(100, 200, 250, 350))
    fr[40:197, 50:330] = 255
    assert speaking_tiles(fr) == [(100, 194, 257, 350)]


def test_a_tile_taller_than_wide_is_kept_when_its_own_border_says_so():
    assert speaking_tiles(frame(tile=(100, 100, 220, 300))) == [(100, 94, 227, 300)]
