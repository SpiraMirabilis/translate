"""Ranged time quantities must scale BOTH endpoints.

The main pattern binds <num> to the numeral touching the unit, so before this
fix "two or three shichen" converted only the second numeral and emitted
"two or six hours" — the low bound was left in the source unit. Caught in book
69 ch157 (跑了两三个时辰 -> "running for two or six hours").
"""
import pytest

from unit_converter import convert_units


def _one(line):
    return convert_units([line])[0]


@pytest.mark.parametrize("src,expected", [
    # A shichen is two hours; both endpoints double.
    ("He ran for two or three shichen.", "He ran for four or six hours."),
    ("He waited one or two shichen.", "He waited two or four hours."),
    ("He waited four or five shichen.", "He waited eight or ten hours."),
    # "to" is a range separator as well as "or".
    ("He waited two to three shichen.", "He waited four to six hours."),
    # A ke is fifteen minutes.
    ("He rested two or three ke.", "He rested thirty or forty-five minutes."),
])
def test_range_scales_both_endpoints(src, expected):
    assert _one(src) == expected


@pytest.mark.parametrize("src", [
    # En/em dash ranges.
    "He waited two–three shichen.",
    "He waited two—three shichen.",
])
def test_dash_ranges_scale_both_endpoints(src):
    out = _one(src)
    assert "four" in out and "six" in out
    assert "shichen" not in out


def test_bare_hyphen_is_not_a_range_separator():
    """Word-numbers are hyphenated, so a bare hyphen must not split a range."""
    assert _one("He waited twenty-one shichen.") == "He waited forty-two hours."


@pytest.mark.parametrize("src,expected", [
    # Regressions: the single-value paths must be untouched.
    ("He waited three shichen.", "He waited six hours."),
    ("He waited several shichen.", "He waited several hours."),
    ("Half a shichen passed.", "An hour passed."),
    ("He waited two more shichen.", "He waited four more hours."),
    ("He waited 3 shichen.", "He waited six hours."),
])
def test_single_value_paths_unchanged(src, expected):
    assert _one(src) == expected


def test_range_preserves_filler_word():
    assert _one("He waited two or three whole shichen.") == \
        "He waited four or six whole hours."


def test_range_left_alone_when_endpoints_land_in_different_units():
    """A range that would straddle a unit boundary is emitted unchanged rather
    than as a nonsense span."""
    out = _one("He waited a quarter of a ke.")
    # unrelated fraction path still works
    assert "minutes" in out


def test_no_source_unit_survives_a_converted_range():
    for line in ("two or three shichen", "one or two shichen", "two to three ke"):
        assert "shichen" not in _one(f"It took {line}.")
        assert " ke" not in _one(f"It took {line}.")


# ── ASCII-hyphen ranges (digits only) ────────────────────────────────


@pytest.mark.parametrize("src,expected", [
    # Both endpoints scale; before this the low end was orphaned ("3-ten hours").
    ("It took 3-5 shichen to arrive.", "It took six-ten hours to arrive."),
    ("It took 3 - 5 shichen.", "It took six-ten hours."),
])
def test_hyphen_digit_ranges_scale_both_ends(src, expected):
    assert _one(src) == expected


def test_hyphenated_compound_word_number_is_not_a_range():
    """twenty-one is one quantity (21 ke ≈ 5h), never the range 20–1."""
    assert _one("It took twenty-one ke to finish.") == \
        "It took about five hours to finish."


def test_adjacent_ones_words_rejected_not_summed():
    """"two-three shichen" is a 2–3 range the pattern can't safely convert;
    the old parser summed the words (2+3=5 → "ten hours"), silently
    misstating the magnitude. It must now pass through unchanged."""
    assert _one("It took two-three shichen.") == "It took two-three shichen."


def test_half_compounds_still_parse():
    assert _one("It took one and a half shichen.") == "It took three hours."
