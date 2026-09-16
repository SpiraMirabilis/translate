"""Behavior lock-in tests for genres.py."""
import os

from genres import (
    extract_categories_from_prompt,
    extract_categories_meta_from_prompt,
    get_genre,
    load_genres,
    read_genre_prompt,
    genre_categories,

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


def test_every_shipped_genre_declares_categories():
    """Categories live in genres.json, not in the prompt corpus.

    They used to be reverse-engineered from the ++++ response-template block in
    each prompt file. That block is now built by prompt_contract at assembly
    time and no longer sits in the corpus, so a genre that forgets to declare
    its categories would silently create books with none.
    """
    for genre in load_genres(SCRIPT_DIR):
        if not genre.get("prompt_file"):
            continue                      # "custom" configures categories by hand
        cats = genre_categories(genre)
        assert cats, f"{genre['id']} declares no categories"
        by_name = {c["name"]: c["attributes"] for c in cats}
        assert "characters" in by_name
        assert by_name["characters"] == ["gender"]


def test_genre_categories_falls_back_to_prompt_template():
    """A hand-written prompt file that still carries a template block keeps
    seeding categories the old way."""
    cats = genre_categories({"id": "handmade"}, SYNTHETIC_PROMPT)
    assert {c["name"] for c in cats} >= {"characters", "places", "abilities"}


def test_genre_categories_none_when_nothing_declared():
    assert genre_categories({"id": "custom"}, None) is None
    assert genre_categories({"id": "custom"}, "no template markers here") is None
