"""Language-keyed unit tables (units.json: common/zh/ja/ko), strict units, and
the larger-unit scales (t, m³, mL). Pure regex: no cleaning model, Jev off."""
import json

import pytest

import unit_converter
from unit_converter import convert_units, normalize_language, parse_units


def conv(line, lang="zh"):
    return convert_units([line], source_language=lang)[0]


# ── Language selection ─────────────────────────────────────────────


def test_same_name_different_value_by_language():
    # 里: Chinese li 500 m, Japanese ri 3.93 km, Korean ri 393 m.
    assert conv("three ri away", "ja") == "three ri (11.78 km) away"
    assert conv("three ri away", "ko") == "three ri (1.18 km) away"
    assert conv("three li away", "zh") == "three li (1.5 km) away"


def test_korean_murim_li_is_the_chinese_li():
    assert conv("a hundred li away", "ko") == "a hundred li (50 km) away"


def test_language_without_a_table_is_untouched():
    for lang in ("ru", "en"):
        assert conv("walked three li and weighed two jin", lang) == \
            "walked three li and weighed two jin"


def test_missing_language_defaults_to_chinese():
    assert conv("walked three li", None) == "walked three li (1.5 km)"
    assert conv("walked three li", "") == "walked three li (1.5 km)"


def test_language_codes_normalise():
    assert normalize_language("zh-TW") == "zh"
    assert normalize_language("JP") == "ja"
    assert normalize_language("ko_KR") == "ko"


def test_chinese_time_units_stay_chinese():
    assert conv("after two shichen", "ko") == "after two shichen"
    assert conv("after two shichen", "zh") == "after four hours"


def test_common_units_reach_every_language():
    for lang in ("zh", "ja", "ko"):
        assert conv("a 50 tsubo house", lang) == "a 50 tsubo (165.3 m²) house"
        assert conv("a 30 pyeong flat", lang) == "a 30 pyeong (99.18 m²) flat"


def test_other_languages_units_do_not_leak():
    assert conv("two kin of meat", "zh") == "two kin of meat"
    assert conv("two kin of meat", "ja") == "two kin (1.2 kg) of meat"
    assert conv("five geun of pork", "zh") == "five geun of pork"


# ── Strict units (also English words) ──────────────────────────────


@pytest.mark.parametrize("line", [
    "two ping-pong balls",      # hyphenated continuation
    "There were two pings.",    # plural
    "a ping sounded",           # bare article
    "several ping",             # vague
    "a single ping echoed",
])
def test_strict_unit_rejects_the_english_word(line):
    assert conv(line) == line


def test_strict_unit_converts_after_a_real_count():
    assert conv("Territory Area: 6,000 ping") == "Territory Area: 6,000 ping (1.98 ha)"
    assert conv("three sheng of blood") == "three sheng (3 L) of blood"


def test_next_of_kin_is_not_a_weight():
    assert conv("next of kin", "ja") == "next of kin"


# ── New Chinese units and scales ───────────────────────────────────


def test_jun_hyperbole_scales_to_tonnes():
    assert conv("heavy as ten thousand jun") == "heavy as ten thousand jun (150 t)"
    assert conv("a force of a thousand jun") == "a force of a thousand jun (15 t)"


def test_jin_scales_to_tonnes_from_a_thousand_kg():
    assert conv("ten thousand jin of iron") == "ten thousand jin (5 t) of iron"
    assert conv("He weighed 100 jin.") == "He weighed 100 jin (50 kg)."


def test_dan_is_grain_volume():
    assert conv("thirty million dan a year") == "thirty million dan (3,000,000 m³) a year"
    assert conv("a dan of grain") == "a dan of grain"   # strict: 丹 is a pill


def test_dou_litres():
    assert conv("two dou of beast blood") == "two dou (20 L) of beast blood"


def test_small_volumes_scale_to_millilitres():
    assert conv("three gō of sake", "ja") == "three gō (541.2 mL) of sake"


def test_small_mass_scales_to_grams():
    assert conv("ten monme of silver", "ja") == "ten monme (37.5 g) of silver"


# ── units.json parsing and reload ──────────────────────────────────


def test_common_merges_and_language_wins():
    tables = parse_units({
        "common": {"tsubo": {"value": 3.306, "unit": "m²", "type": "area"},
                   "li": {"value": 1.0, "unit": "m", "type": "length"}},
        "zh": {"li": {"value": 500.0, "unit": "m", "type": "length"}},
    })
    assert set(tables) == {"zh"}
    assert tables["zh"]["li"].value == 500.0
    assert tables["zh"]["tsubo"].unit == "m²"


def test_legacy_flat_file_reads_as_chinese():
    tables = parse_units({"li": {"value": 500.0, "unit": "m", "type": "length"}})
    assert list(tables) == ["zh"] and tables["zh"]["li"].strict is False


@pytest.mark.parametrize("entry", [
    {"unit": "m", "type": "length"},                                    # no value
    {"value": -1, "unit": "m", "type": "length"},
    {"value": 1, "unit": "m", "type": "length", "action": "convert"},
    {"value": 1, "unit": "m", "type": "length", "strict": "yes"},
])
def test_malformed_entries_are_refused(entry):
    with pytest.raises(ValueError):
        parse_units({"zh": {"li": entry}})


def test_units_json_reloads_on_change(tmp_path, monkeypatch):
    path = tmp_path / "units.json"
    path.write_text(json.dumps({"zh": {"li": {"value": 500.0, "unit": "m", "type": "length"}}}))
    monkeypatch.setattr(unit_converter, "UNITS_PATH", str(path))
    monkeypatch.setattr(unit_converter, "_tables_state",
                        {"mtime": None, "tables": None, "sets": {}})
    assert conv("three li") == "three li (1.5 km)"

    path.write_text(json.dumps({"zh": {"li": {"value": 400.0, "unit": "m", "type": "length"}}}))
    st = path.stat()
    import os
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    assert conv("three li") == "three li (1.2 km)"

    # A broken edit keeps the previous table instead of failing conversion.
    path.write_text("{not json")
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000))
    assert conv("three li") == "three li (1.2 km)"


def test_shipped_units_json_parses():
    with open(unit_converter.UNITS_PATH, encoding="utf-8") as f:
        tables = parse_units(json.load(f))
    assert {"zh", "ja", "ko"} <= set(tables)
