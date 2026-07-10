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
