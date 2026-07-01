"""Behavior lock-in tests for database.normalize_categories."""
from database import DEFAULT_CATEGORIES, normalize_categories


def test_none_yields_defaults_with_characters_gendered():
    out = normalize_categories(None)
    assert [c["name"] for c in out] == DEFAULT_CATEGORIES
    by_name = {c["name"]: c["attributes"] for c in out}
    assert by_name["characters"] == ["gender"]
    for name in DEFAULT_CATEGORIES:
        if name != "characters":
            assert by_name[name] == []


def test_legacy_string_list():
    out = normalize_categories(["characters", "places"])
    assert out == [
        {"name": "characters", "attributes": ["gender"]},
        {"name": "places", "attributes": []},
    ]


def test_string_list_non_default_names_get_no_gender():
    out = normalize_categories(["spells", "chatgroup usernames"])
    assert out == [
        {"name": "spells", "attributes": []},
        {"name": "chatgroup usernames", "attributes": []},
    ]


def test_dict_list_with_attributes():
    out = normalize_categories([
        {"name": "monsters", "attributes": ["gender"]},
        {"name": "places", "attributes": []},
    ])
    assert out == [
        {"name": "monsters", "attributes": ["gender"]},
        {"name": "places", "attributes": []},
    ]


def test_dict_attributes_string_is_wrapped():
    out = normalize_categories([{"name": "beasts", "attributes": "gender"}])
    assert out == [{"name": "beasts", "attributes": ["gender"]}]


def test_unknown_attributes_are_filtered():
    out = normalize_categories([
        {"name": "beasts", "attributes": ["gender", "colour", "gender"]},
    ])
    # Unknown "colour" dropped, duplicate "gender" deduped.
    assert out == [{"name": "beasts", "attributes": ["gender"]}]


def test_deduplicates_names_first_wins():
    out = normalize_categories([
        "places",
        {"name": "places", "attributes": ["gender"]},
    ])
    # First occurrence wins; the later dict form is ignored entirely.
    assert out == [{"name": "places", "attributes": []}]


def test_blank_and_non_string_items_skipped():
    out = normalize_categories(["  ", {"name": ""}, 42, None, "valid"])
    assert out == [{"name": "valid", "attributes": []}]


def test_names_are_stripped():
    out = normalize_categories(["  places  "])
    assert out == [{"name": "places", "attributes": []}]


def test_mixed_string_and_dict_input():
    out = normalize_categories([
        "characters",
        {"name": "gods", "attributes": ["gender"]},
    ])
    assert out == [
        {"name": "characters", "attributes": ["gender"]},
        {"name": "gods", "attributes": ["gender"]},
    ]
