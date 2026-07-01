"""Behavior lock-in tests for genres.py."""
import os

from genres import (
    extract_categories_from_prompt,
    extract_categories_meta_from_prompt,
    get_genre,
    load_genres,
    read_genre_prompt,
)

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── load_genres / get_genre ────────────────────────────────────────


def test_load_genres_returns_genres_from_json():
    genres = load_genres(SCRIPT_DIR)
    assert isinstance(genres, list)
    ids = [g["id"] for g in genres]
    assert "chinese_xianxia" in ids
    assert "japanese_light_novel" in ids
    assert "korean_web_novel" in ids


def test_load_genres_missing_dir_returns_empty_list(tmp_path):
    assert load_genres(str(tmp_path)) == []


def test_get_genre_known_id():
    genre = get_genre(SCRIPT_DIR, "chinese_xianxia")
    assert genre is not None
    assert genre["id"] == "chinese_xianxia"
    assert genre["source_language"] == "zh"
    assert genre["prompt_file"] == "prompts/chinese_xianxia.txt"


def test_get_genre_unknown_id_returns_none():
    assert get_genre(SCRIPT_DIR, "does_not_exist") is None


def test_read_genre_prompt_missing_file_returns_none(tmp_path):
    assert read_genre_prompt(str(tmp_path), {"prompt_file": "nope.txt"}) is None
    assert read_genre_prompt(str(tmp_path), {}) is None


# ── extract_categories_from_prompt ─────────────────────────────────

SYNTHETIC_PROMPT = """Some instructions above.

++++ Response Template Example
{
  "translation": ["line one"],
  "entities": {
    "characters": {
      "张羽": {"translation": "Zhang Yu", "gender": "male"}
    },
    "places": {
      "青云山": {"translation": "Azure Cloud Mountain"}
    },
    "abilities": {}
  }
}
++++ Response Template End

More instructions below.
"""


def test_extract_categories_from_synthetic_prompt():
    cats = extract_categories_from_prompt(SYNTHETIC_PROMPT)
    assert cats == ["characters", "places", "abilities"]


def test_extract_categories_no_template_block():
    assert extract_categories_from_prompt("no template markers here") is None


def test_extract_categories_malformed_json():
    prompt = ("++++ Response Template Example\n"
              "{ not valid json\n"
              "++++ Response Template End")
    assert extract_categories_from_prompt(prompt) is None


def test_extract_categories_missing_entities_key():
    prompt = ("++++ Response Template Example\n"
              '{"translation": ["x"]}\n'
              "++++ Response Template End")
    assert extract_categories_from_prompt(prompt) is None


def test_extract_categories_meta_marks_gendered():
    meta = extract_categories_meta_from_prompt(SYNTHETIC_PROMPT)
    by_name = {m["name"]: m["attributes"] for m in meta}
    assert by_name["characters"] == ["gender"]
    assert by_name["places"] == []
    assert by_name["abilities"] == []


def test_real_xianxia_prompt_yields_categories():
    genre = get_genre(SCRIPT_DIR, "chinese_xianxia")
    prompt = read_genre_prompt(SCRIPT_DIR, genre)
    assert prompt is not None
    cats = extract_categories_from_prompt(prompt)
    assert cats is not None
    assert "characters" in cats
