"""Emphasis modifiers in front of a duration must survive the unit swap.

"a full half-shichen" replaced only "half-shichen" with "an hour", stranding the
determiner in front of the new article: "a full an hour" (spotted in book 71).
The same collision hit every emphasis word ("an entire an hour", "a good an
hour", "another an hour") and the rounding hedge ("a full about ten minutes").
Two related silent magnitude errors are covered too: an emphasis word standing
in for the count used to be dropped ("the full shichen" -> "the two hours"), and
"an entire shichen" matched nothing at all, so the bare-unit path understated it
as "an entire hour" (one shichen is two hours).
"""
import re

import pytest

from unit_converter import convert_units


def _one(line):
    return convert_units([line])[0]


@pytest.mark.parametrize("src,expected", [
    # A half-shichen is exactly one hour: the article of the emphasis phrase
    # carries the count, so no numeral is emitted at all.
    ("He waited a full half-shichen.", "He waited a full hour."),
    ("He waited a full half a shichen.", "He waited a full hour."),
    ("He waited a full half shichen.", "He waited a full hour."),
    ("He waited an entire half-shichen.", "He waited an entire hour."),
    ("He waited a good half-shichen.", "He waited a good hour."),
    ("He waited a mere half-shichen.", "He waited a mere hour."),
    ("He waited the full half-shichen.", "He waited the full hour."),
    ("He waited another half-shichen.", "He waited another hour."),
])
def test_emphasis_phrase_absorbs_the_article(src, expected):
    assert _one(src) == expected


@pytest.mark.parametrize("src", [
    "He waited a full half-shichen.",
    "He waited an entire half-shichen.",
    "He waited a good half-shichen.",
    "He waited another half-shichen.",
    "He waited a full half-ke.",
])
def test_no_stranded_determiner_survives(src):
    """No "<det> <emphasis>" left sitting in front of an article or a hedge."""
    out = _one(src)
    assert not re.search(
        r"\b(?:a|an|the|another)\s+(?:full|whole|entire|good|solid|mere)?\s*"
        r"(?:a|an|about)\s", out), out


@pytest.mark.parametrize("src,expected", [
    # Lead-position words keep their article in front of a plural count.
    ("He waited a full two shichen.", "He waited a full four hours."),
    ("He waited a solid two shichen.", "He waited a solid four hours."),
    ("He waited a good shichen.", "He waited a good two hours."),
    ("He waited a mere shichen.", "He waited a mere two hours."),
    ("He waited another two shichen.", "He waited another four hours."),
    # "an entire two hours" is not English, so those words go after the count.
    ("He waited a whole shichen.", "He waited two whole hours."),
    ("He waited an entire shichen.", "He waited two entire hours."),
    ("He waited the whole shichen.", "He waited two whole hours."),
])
def test_emphasis_word_placement_by_class(src, expected):
    assert _one(src) == expected


@pytest.mark.parametrize("src,expected", [
    # The emphasis word IS the count ("one shichen"), and must not be dropped.
    ("He waited the full shichen.", "He waited the full two hours."),
    ("He waited a full shichen.", "He waited a full two hours."),
    ("His full shichen of meditation ended.", "His full two hours of meditation ended."),
])
def test_emphasis_word_standing_in_for_the_count(src, expected):
    assert _one(src) == expected


def test_hedged_plural_moves_the_word_after_the_count():
    """"about a full ten minutes" reads wrong; the hedge keeps the front slot."""
    assert _one("He waited a full half-ke.") == "He waited about ten full minutes."


def test_hedge_goes_in_front_of_a_lead_only_word():
    """"another" can't follow a count ("ten another minutes"), so the hedge
    moves ahead of it instead."""
    assert _one("He waited another half-ke.") == "He waited about another ten minutes."


def test_emphasis_phrase_on_a_range():
    assert _one("He waited a full two or three shichen.") == \
        "He waited a full four or six hours."


def test_sentence_initial_casing_follows_the_phrase():
    assert _one("A full half-shichen passed.") == "A full hour passed."
    assert _one("A FULL HALF-SHICHEN passed.") == "A FULL HOUR passed."


def test_annotated_units_are_untouched_by_the_emphasis_capture():
    """Length units annotate rather than replace, so widening the match must not
    change the emitted text."""
    assert _one("It was a full three zhang away.") == \
        "It was a full three zhang (9.99 m) away."


def test_incoherent_emphasis_on_a_vague_count_is_left_alone():
    assert _one("He waited a full several shichen.") == "He waited a full several shichen."
