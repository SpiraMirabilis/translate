"""Behavior lock-in tests for unit_converter.py (pure regex/format paths;
no cleaning model is invoked when cleaning_model=None)."""
from unit_converter import (
    _format_number,
    _int_to_words,
    _minutes_phrase,
    _number_to_words,
    _parse_fraction_phrase,
    _scale,
    _word_to_number,
    convert_units,
)


# ── _format_number ─────────────────────────────────────────────────


def test_format_number_integer_gets_commas():
    assert _format_number(1000.0) == "1,000"
    assert _format_number(3.0) == "3"


def test_format_number_decimals_trimmed():
    assert _format_number(3.333) == "3.33"     # <100 -> 2 decimals
    assert _format_number(123.456) == "123.5"  # >=100 -> 1 decimal
    assert _format_number(12.345) == "12.35"   # NOTE: banker's-ish f-string rounding


# ── number words ───────────────────────────────────────────────────


def test_int_to_words():
    assert _int_to_words(0) == "zero"
    assert _int_to_words(24) == "twenty-four"
    assert _int_to_words(105) == "one hundred five"
    assert _int_to_words(1234567) == (
        "one million two hundred thirty-four thousand five hundred sixty-seven"
    )


def test_number_to_words_fractions():
    assert _number_to_words(1.5) == "one and a half"
    assert _number_to_words(0.5) == "half"
    assert _number_to_words(0.25) == "a quarter"
    assert _number_to_words(2.75) == "two and three-quarters"
    # Complex decimals fall back to arabic formatting.
    assert _number_to_words(3.33) == "3.33"


def test_word_to_number():
    assert _word_to_number("three hundred") == 300.0
    assert _word_to_number("ten thousand") == 10000.0
    assert _word_to_number("twenty-four") == 24.0
    assert _word_to_number("half") == 0.5
    assert _word_to_number("a") == 1.0
    assert _word_to_number("42") == 42.0
    assert _word_to_number("several") is None
    assert _word_to_number("a few") is None


def test_parse_fraction_phrase():
    assert _parse_fraction_phrase("half") == 0.5
    assert _parse_fraction_phrase("three-quarters") == 0.75
    assert _parse_fraction_phrase("a third") == 1.0 / 3
    assert _parse_fraction_phrase("gibberish") is None


def test_minutes_phrase():
    assert _minutes_phrase(1) == "one minute"
    assert _minutes_phrase(45) == "forty-five minutes"
    assert _minutes_phrase(60) == "an hour"
    assert _minutes_phrase(105) == "an hour and forty-five minutes"


# ── _scale ─────────────────────────────────────────────────────────


def test_scale_up_and_down():
    assert _scale(3330.0, "m") == (3.33, "km", False)
    assert _scale(0.5, "m") == (50.0, "cm", False)
    assert _scale(90.0, "minute") == (1.5, "hour", False)
    assert _scale(0.5, "hour") == (30.0, "minute", False)


def test_scale_approximate_flag_only_when_rounding_moves_value():
    # 90 min -> 1.5 h, already on a half-hour boundary: not "rounded".
    assert _scale(90.0, "minute", approximate=True) == (1.5, "hour", False)


# ── convert_units end-to-end (annotate action) ─────────────────────


def test_annotate_zhang_numeric():
    assert convert_units(["He flew 1000 zhang before landing."]) == [
        "He flew 1000 zhang (3.33 km) before landing."
    ]


def test_annotate_word_number_li():
    assert convert_units(["The mountain was three hundred li away."]) == [
        "The mountain was three hundred li (150 km) away."
    ]


def test_annotate_jin_mass():
    assert convert_units(["He weighed 100 jin."]) == [
        "He weighed 100 jin (50 kg)."
    ]


def test_annotate_decimal_quantity():
    assert convert_units(["It was 3.5 li away."]) == [
        "It was 3.5 li (1.75 km) away."
    ]


def test_already_annotated_left_alone():
    line = "already annotated 10 zhang (33.3 m) here"
    assert convert_units([line]) == [line]


def test_vague_quantifier_annotate_unit_untouched():
    assert convert_units(["several zhang tall"]) == ["several zhang tall"]


# ── convert_units end-to-end (replace action, minute-based ke) ─────


def test_replace_shichen_word_form():
    assert convert_units(["He waited two shichen."]) == [
        "He waited four hours."
    ]


def test_replace_half_shichen_reads_an_hour():
    assert convert_units(["It took half a shichen."]) == ["It took an hour."]


def test_replace_ke_minutes():
    assert convert_units(["one ke passed"]) == ["fifteen minutes passed"]


def test_replace_ke_preserves_leading_case():
    assert convert_units(["Three ke passed"]) == ["Forty-five minutes passed"]


def test_unusual_fraction_of_ke():
    # 0.25 * 15 min = 3.75 min -> "three and three-quarters minutes"
    assert convert_units(["A quarter of a ke passed."]) == [
        "Three and three-quarters minutes passed."
    ]


def test_hyphenated_fraction_on_unit():
    assert convert_units(["a quarter-shichen later"]) == [
        "thirty minutes later"
    ]


def test_vague_quantifier_replace_hour_unit():
    assert convert_units(["several shichen passed"]) == [
        "several hours passed"
    ]


def test_bare_shichen_is_point_in_time():
    assert convert_units(["the appointed shichen arrived"]) == [
        "the appointed hour arrived"
    ]


def test_point_in_time_earthly_branch():
    assert convert_units(["at the third ke of the wu hour"]) == [
        "at forty-five minutes past the hour of the Horse"
    ]


def test_no_matches_returns_copy():
    lines = ["Nothing to convert here."]
    out = convert_units(lines)
    assert out == lines
    assert out is not lines
