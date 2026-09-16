"""get_entities.py's point-in-time note view.

Dumping the entities for an early stretch of a book and being handed late-book
notes is misleading — the protagonist's note describes who she is *now*. An
--origin-chapter filter with an upper bound therefore winds the notes back to
that chapter, and --as-of-chapter says so explicitly.
"""
import pytest

from get_entities import (fetch_entities, filter_upper_bound, note_update_chapters,
                          parse_chapter_filter)


@pytest.mark.parametrize("expr,expected", [
    ("1-20", 20),
    ("20-1", 20),      # normalised like parse_chapter_filter does
    ("42", 42),
    ("<=99", 99),
    ("<100", 99),      # exclusive: the latest chapter it can match is 99
    (">15", None),     # open-ended — no point in time to wind back to
    (">=15", None),
    (None, None),
    ("", None),
])
def test_filter_upper_bound(expr, expected):
    assert filter_upper_bound(expr) == expected


@pytest.fixture
def book(db):
    book_id = db.create_book("As-Of Book")
    db.add_entity("characters", "许春娘", "Xu Chunniang", book_id=book_id,
                  last_chapter=1, origin_chapter=1,
                  note="Eight years old, village girl.",
                  note_author='model', note_chapter=1)
    entity_id = db.get_entity_id(book_id, "许春娘")
    db.set_entity_note(entity_id, "Past forty; fourth level of Foundation.",
                       author='model', chapter_number=243)
    db.add_entity("characters", "莫林", "Mo Lin", book_id=book_id,
                  last_chapter=300, origin_chapter=300, note="Late arrival.",
                  note_author='model', note_chapter=300)
    return book_id


def _note(grouped, untranslated):
    for entry in grouped.get("characters", []):
        if entry["untranslated"] == untranslated:
            return entry.get("note")
    return "MISSING"


def test_as_of_chapter_shows_the_note_of_that_era(db, book):
    clause, params = parse_chapter_filter("1-20")
    grouped = fetch_entities(db, book, clause, params, as_of_chapter=20)
    assert _note(grouped, "许春娘") == "Eight years old, village girl."


def test_without_as_of_the_latest_note_is_shown(db, book):
    clause, params = parse_chapter_filter("1-20")
    grouped = fetch_entities(db, book, clause, params)
    assert _note(grouped, "许春娘") == "Past forty; fourth level of Foundation."


def test_entities_from_later_chapters_carry_no_note(db, book):
    grouped = fetch_entities(db, book, None, [], as_of_chapter=20)
    assert _note(grouped, "莫林") is None       # in the dump, but noteless at ch20
    assert _note(grouped, "许春娘") == "Eight years old, village girl."


def test_translation_is_never_wound_back(db, book):
    grouped = fetch_entities(db, book, None, [], as_of_chapter=20)
    entry = next(e for e in grouped["characters"] if e["untranslated"] == "许春娘")
    assert entry["translation"] == "Xu Chunniang"


# ------------------------------------- the chapter filter follows notes, too


def test_note_update_chapters_finds_revisions_in_range(db, book):
    entity_id = db.get_entity_id(book, "许春娘")
    assert note_update_chapters(db, book, "240-260") == {entity_id: [243]}

    # A note's creation is a note event too — it happened at chapter 1.
    assert note_update_chapters(db, book, "1-20") == {entity_id: [1]}
    assert note_update_chapters(db, book, "100-200") == {}
    assert note_update_chapters(db, book, None) == {}


def test_long_lived_entity_shows_up_when_its_note_changed(db, book):
    """许春娘 was introduced in chapter 1, so an origin_chapter filter alone
    misses her — but her note was rewritten at 243, which is exactly what a
    review of chapters 240-260 wants to see."""
    clause, params = parse_chapter_filter("240-260")

    origin_only = fetch_entities(db, book, clause, params)
    assert _note(origin_only, "许春娘") == "MISSING"

    widened = fetch_entities(db, book, clause, params, as_of_chapter=260,
                             note_chapters=note_update_chapters(db, book, "240-260"))
    assert _note(widened, "许春娘") == "Past forty; fourth level of Foundation."


def test_matched_rows_say_why_they_matched(db, book):
    clause, params = parse_chapter_filter("240-260")
    grouped = fetch_entities(db, book, clause, params,
                             note_chapters=note_update_chapters(db, book, "240-260"))
    entry = next(e for e in grouped["characters"] if e["untranslated"] == "许春娘")
    assert entry["note_updated_chapters"] == [243]
