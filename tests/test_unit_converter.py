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


def test_annotate_ping_floor_area():
    assert convert_units(["The flat was only thirty ping."]) == [
        "The flat was only thirty ping (99.18 m²)."
    ]


def test_ping_scales_to_hectares():
    assert convert_units(["a 5,000 ping estate"]) == [
        "a 5,000 ping (1.65 ha) estate"
    ]


def test_ping_needs_an_explicit_count():
    # "ping" is also an English word; a bare article is the sound, not 3.3 m².
    for line in ["There was a ping from the console.",
                 "Another ping sounded.",
                 "a single ping echoed"]:
        assert convert_units([line]) == [line]


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


# ---------------------------------------------------------------------------
# Earthly-branch hours in running prose (book 106 glitches). The branch name is
# capitalised as a proper noun, so its capital must not become "The"; and the
# canonical label's own "the" must not stack on the model's determiner.
# ---------------------------------------------------------------------------
def test_point_mid_sentence_proper_noun_capital_is_not_copied():
    assert convert_units(["Yuzhong, Si hour."]) == [
        "Yuzhong, the hour of the Snake."
    ]


def test_point_sentence_initial_keeps_capital():
    assert convert_units(["He left. Si hour came."]) == [
        "He left. The hour of the Snake came."
    ]
    assert convert_units(['"Si hour," he said.']) == [
        '"The hour of the Snake," he said.'
    ]
    assert convert_units(['Yu Sheng said, "Zi hour is almost here."']) == [
        'Yu Sheng said, "The hour of the Rat is almost here."'
    ]


def test_point_after_the_very_drops_duplicate_article():
    assert convert_units(["at the very start of the Xu hour, which"]) == [
        "at the very start of the hour of the Dog, which"
    ]


def test_point_after_quoted_the_drops_duplicate_article():
    assert convert_units(['Emperor Jing demanded the "Mao hour roll call."']) == [
        'Emperor Jing demanded the "hour of the Rabbit roll call."'
    ]


def test_point_after_indefinite_article_replaces_it():
    assert convert_units(["expelled at a Yin hour on a yin day"]) == [
        "expelled at the hour of the Tiger on a yin day"
    ]
    assert convert_units(["A Yin hour passed."]) == [
        "The hour of the Tiger passed."
    ]


def test_point_after_conjunction_that_keeps_article():
    assert convert_units(["He knew that the Wu hour had come."]) == [
        "He knew that the hour of the Horse had come."
    ]


def test_point_capitalised_hour_of_you():
    assert convert_units(["the end of the hour of You that it grew dark"]) == [
        "the end of the hour of the Rooster that it grew dark"
    ]


def test_point_hour_of_you_pronoun_and_surname_untouched():
    for line in ["an hour of you", "the hour of You-know-what",
                 "spent an hour of You Wei's time"]:
        assert convert_units([line]) == [line]


def test_point_ke_after_the_hour():
    assert convert_units(["Yuzhong, Si hour, third ke."]) == [
        "Yuzhong, forty-five minutes past the hour of the Snake."
    ]


def test_qualified_bare_ke_is_a_quarter_hour():
    cases = {
        "In the first ke of the evaluation, birds came.":
            "In the first quarter hour of the evaluation, birds came.",
        "As the ninth ke arrived, bells rang.":
            "As the ninth quarter hour arrived, bells rang.",
        "Nearly every ke it grew two nodes.":
            "Nearly every quarter hour it grew two nodes.",
        "Over the next ke, he ran.": "Over the next quarter hour, he ran.",
        "Two strokes per ke.": "Two strokes per quarter hour.",
        "After that ke, she would be left weak.":
            "After that quarter hour, she would be left weak.",
        "This ke passed slowly.": "This quarter hour passed slowly.",
    }
    for src, want in cases.items():
        assert convert_units([src]) == [want]


def test_surname_ke_is_not_a_unit():
    for line in ["He met Ke Zhen at noon.", "the first Ke family elder"]:
        assert convert_units([line]) == [line]


def test_joined_romanisation_names_are_not_hours():
    # 无始 Wushi, 海石 Haishi, 裘審勢 Qiu Shenshi, 子实 Zishi were all rewritten
    # into "the hour of the X" by the joined-romanisation form.
    for line in ["followed the Wushi Great Emperor", "Old Ancestor Haishi nodded",
                 "Qiu Shenshi laughed", "Zishi Venerable bowed",
                 "the ritual sword 【Xushi Hour】"]:
        assert convert_units([line]) == [line]


def test_lowercase_yin_hour_is_yin_yang_not_tiger():
    line = "born in a yin year, a yin month, a yin hour"
    assert convert_units([line]) == [line]
    assert convert_units(["at the Yin hour"]) == ["at the hour of the Tiger"]


def test_hyphenated_hour_before_a_noun_is_a_name():
    # 午时草 / 巳时草: plant names in book 106's Twelve Shichen Grass.
    for line in ["kept his eyes on the Wu-hour grass.", "the Si-hour grass had climbed"]:
        assert convert_units([line]) == [line]
    assert convert_units(["It was the Wu-hour."]) == ["It was the hour of the Horse."]


def test_start_of_bare_branch_is_not_an_hour():
    line = "at the start of the Wei dynasty"
    assert convert_units([line]) == [line]


def test_bare_branch_before_a_name_is_not_an_hour():
    line = "ten minutes after Chen Yiran read them"
    assert convert_units([line]) == [line]


def test_no_matches_returns_copy():
    lines = ["Nothing to convert here."]
    out = convert_units(lines)
    assert out == lines
    assert out is not lines

# ---------------------------------------------------------------------------
# Personal names that romanise onto unit words. 李 -> "li", 张 -> "zhang",
# 梁 -> "liang"; with an article or numeral in front, "a Zhang Juzheng" parses
# as a well-formed measurement and was annotated as a distance in book 93.
# ---------------------------------------------------------------------------
def test_surname_before_a_given_name_is_not_a_measurement():
    line = "as for a Zhang Juzheng, there is no need to cut his mourning short."
    assert convert_units([line]) == [line]


def test_numeral_before_a_surname_is_not_a_measurement():
    line = "a scholar below, one Li Sancai, requests an audience."
    assert convert_units([line]) == [line]


def test_another_before_a_surname_is_not_a_measurement():
    line = "He simply feared getting another Li Zaiting."
    assert convert_units([line]) == [line]


def test_a_real_distance_still_converts():
    assert convert_units(["Xuanfu lies no more than four hundred li from the capital."]) == [
        "Xuanfu lies no more than four hundred li (200 km) from the capital."]


def test_a_real_length_with_an_article_still_converts():
    assert convert_units(["a stone stele nearly a zhang high stood before the arch."]) == [
        "a stone stele nearly a zhang (3.33 m) high stood before the arch."]


def test_a_unit_at_a_sentence_end_still_converts():
    # The following capital belongs to the next sentence, so the period keeps
    # the name guard from firing.
    assert convert_units(["they marched thirty li. Beijing was still far off."]) == [
        "they marched thirty li (15 km). Beijing was still far off."]


# ── Book 106 (2026-10-03): lowercase time units before a capitalised word ──
# Only a capitalised unit word can be a romanised surname; a lowercase one is
# the unit even when the sentence's subject follows it.

def test_lowercase_ke_before_a_name_still_converts():
    assert convert_units(["in less than two ke Zhao Xing saw a city ahead."]) == [
        "in less than thirty minutes Zhao Xing saw a city ahead."]


def test_lowercase_shichen_before_a_name_still_converts():
    assert convert_units(["in the two shichen Zhao Xing spent with them"]) == [
        "in the four hours Zhao Xing spent with them"]


def test_capitalised_unit_before_a_name_is_still_a_name():
    for line in ("He met a Zhang Juzheng there.", "Twelve Shichen Grass grows here.",
                 "two Ke Zhen arrived."):
        assert convert_units([line]) == [line]


def test_vague_count_of_ke_becomes_quarter_hours():
    assert convert_units(["how many ke will it take?"]) == ["how many quarter hours will it take?"]
    assert convert_units(["after a few ke he left"]) == ["after a few quarter hours he left"]


def test_final_ke_is_a_quarter_hour():
    assert convert_units(["The final ke."]) == ["The final quarter hour."]


def test_time_units_bypass_the_cleaning_model(monkeypatch):
    # The AI false-positive filter vetoed genuine shichen/ke durations; lowercase
    # time units must never be sent to it.
    import unit_converter
    seen = []
    def fake_filter(context, model, **kw):
        seen.extend(context.values())
        return {int(k) for k in context}   # veto everything it is shown
    monkeypatch.setattr(unit_converter, "_filter_false_positives", fake_filter)
    out = convert_units(["After waiting a ke, he walked two shichen and three li."],
                        cleaning_model="fake:model")
    assert out == ["After waiting fifteen minutes, he walked four hours and three li."]
    assert len(seen) == 1 and seen[0].endswith(">>>three li<<<.")   # only the li went to the model


def test_a_leading_and_is_not_part_of_the_count():
    assert convert_units(["fifty kilometers long, and an entire shichen after entering"]) == [
        "fifty kilometers long, and two entire hours after entering"]
    assert convert_units(["both direction and shichen settled back"]) == [
        "both direction and hour settled back"]


def test_and_inside_a_number_still_counts():
    assert convert_units(["one hundred and twenty li"]) == ["one hundred and twenty li (60 km)"]


# ── gongli (公里): replaced outright, never annotated ──────────────


def _conv(s):
    return convert_units([s], cleaning_model=None, use_jev=False)[0]


def test_gongli_is_replaced_with_kilometers():
    assert _conv("Three gongli further on.") == "Three kilometers further on."
    # A one-to-one unit keeps the count exactly as written.
    assert _conv("It was 15 gongli away.") == "It was 15 kilometers away."
    assert _conv("two or three gongli") == "two or three kilometers"
    assert _conv("three gonglis") == "three kilometers"


def test_gongli_keeps_the_article_and_reads_fractions():
    assert _conv("only a gongli off") == "only a kilometer off"
    assert _conv("Half a gongli later") == "Half a kilometer later"
    assert _conv("a quarter of a gongli") == "a quarter of a kilometer"


def test_gongli_takes_a_vague_count():
    # One-to-one, so a vague count can't misstate the magnitude.
    assert _conv("dozens of gongli") == "dozens of kilometers"
    assert _conv("Gongli Village") == "Gongli Village"


# ── gongjin (公斤): the same treatment — one kilogram, i.e. two jin ──


def test_gongjin_is_replaced_with_kilograms():
    assert _conv("Three gongjin of rice.") == "Three kilograms of rice."
    assert _conv("It weighed 15 gongjin.") == "It weighed 15 kilograms."
    assert _conv("two or three gongjin") == "two or three kilograms"
    assert _conv("only a gongjin left") == "only a kilogram left"
    assert _conv("Half a gongjin of pork") == "Half a kilogram of pork"


def test_gongjin_is_not_read_as_jin():
    assert "jin (" not in _conv("Three gongjin of rice.")
    assert _conv("three jin of rice") == "three jin (1.5 kg) of rice"
    assert _conv("dozens of gongjin") == "dozens of kilograms"
    assert _conv("several hundred thousand gongjin") == "several hundred thousand kilograms"
    assert _conv("a few thousand gongjin more") == "a few thousand kilograms more"


def test_gongjin_keeps_the_count_as_written():
    # Book 139: "6,432 gongjin" became "six thousand four hundred thirty-two
    # kilograms", and "a thousand gongjin" became "one thousand kilograms".
    assert _conv('"6,432 gongjin, 6,247 gongjin..."') == '"6,432 kilograms, 6,247 kilograms..."'
    assert _conv("over a thousand gongjin of force") == "over a thousand kilograms of force"
    assert _conv("two or three thousand gongjin") == "two or three thousand kilograms"
    assert _conv("the 60,000-gongjin limit") == "the 60,000-kilogram limit"
    assert _conv("0.5 gongjin") == "0.5 kilograms"
    assert _conv("1 gongjin") == "1 kilogram"


def test_a_match_never_starts_mid_number():
    # The apostrophe blocked "2", and the match started at "000": "2,zero kilograms".
    assert _conv("its limit was '2,000 gongjin.'") == "its limit was '2,000 kilograms.'"


# ── Hyphenated compound adjectives (2026-10-28) ────────────────────
# "a two-shichen wait" used to come out "four hours wait": the article was
# swallowed with the count and the unit pluralised in front of the noun.

def test_compound_adjective_stays_hyphenated_and_singular():
    assert _conv("a two-shichen wait") == "a four-hour wait"
    assert _conv("a hundred-gongli journey") == "a hundred-kilometer journey"
    assert _conv("a fifty-gongjin sack") == "a fifty-kilogram sack"
    assert _conv("a three-ke walk") == "a forty-five-minute walk"
    assert _conv("a one-jiazi cycle") == "a sixty-year cycle"
    assert _conv("a quarter-shichen walk") == "a thirty-minute walk"
    assert _conv("a half-gongli stretch") == "a half-kilometer stretch"


def test_compound_adjective_keeps_determiners_and_emphasis():
    assert _conv("A two-shichen wait began.") == "A four-hour wait began."
    assert _conv("the two-shichen wait") == "the four-hour wait"
    assert _conv("his two-shichen absence") == "his four-hour absence"
    assert _conv("a full two-shichen wait") == "a full four-hour wait"
    assert _conv("another two-shichen wait") == "another four-hour wait"
    assert _conv("a two-ke-long nap") == "a thirty-minute-long nap"


def test_compound_adjective_fixes_the_article():
    assert _conv("a four-shichen wait") == "an eight-hour wait"
    assert _conv("An eight-ke nap") == "A two-hour nap"
    assert _conv("a 4-shichen wait") == "an eight-hour wait"


def test_compound_adjective_is_exact_never_hedged():
    # "about" can't sit inside an adjective; 75 minutes stays 75 minutes.
    assert _conv("a five-ke break") == "a seventy-five-minute break"


def test_hyphenated_count_without_an_adjective_reading_is_a_count():
    assert _conv("after two-shichen of waiting") == "after four hours of waiting"
    assert _conv("two-shichen passed") == "four hours passed"
    assert _conv("twenty-one shichen later") == "forty-two hours later"


# ── Names built from a real measurement (2026-10-12) ───────────────
# Any capitalised unit word is part of a name, whatever follows it — Jev and
# the cleaning model called "Ten-Thousand-Li Pool" a unit at 0.90-0.94.

def test_capitalised_unit_inside_a_name_is_never_converted():
    for line in ("the Ten-Thousand-Li Pool of Thunder was still.",
                 "II. Orb of Nine Li: by pouring spiritual power into it",
                 'he cast the "Orb of Nine Li" again.',
                 "Twelve Shichen Grass grows here."):
        assert convert_units([line], use_jev=False) == [line]


def test_all_caps_unit_is_shouting_not_a_name():
    assert convert_units(["THREE LI AWAY!"], use_jev=False) == ["THREE LI (1.5 km) AWAY!"]
