"""Smoke tests for DatabaseManager against a tmp SQLite database.

Uses plain-English content so no trad->simp conversion or source-language
module transform kicks in; source lines are pre-double-spaced so the
default-on chapter_spacing module's ingest transform is a no-op.
"""
import os
import sqlite3


def _table_names(db):
    conn = sqlite3.connect(db.db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    finally:
        conn.close()
    return {r[0] for r in rows}


def test_tables_exist_after_init(db):
    tables = _table_names(db)
    for expected in ("books", "chapters", "entities", "queue", "footnotes",
                     "illustrations", "comments", "activity_log",
                     "wp_publish_state", "token_ratios"):
        assert expected in tables, f"missing table {expected}"


def test_media_dirs_created_next_to_db(db):
    base = os.path.dirname(db.db_path)
    assert os.path.isdir(os.path.join(base, "covers"))
    assert os.path.isdir(os.path.join(base, "illustrations"))


def test_create_book_get_book_round_trip(db):
    book_id = db.create_book("Test Book", author="A. Author",
                             description="A test description")
    assert isinstance(book_id, int)

    book = db.get_book(book_id=book_id)
    assert book["id"] == book_id
    assert book["title"] == "Test Book"
    assert book["author"] == "A. Author"
    assert book["description"] == "A test description"
    assert book["source_language"] == "zh"
    assert book["target_language"] == "en"
    assert book["is_public"] is True
    assert book["view_count"] == 0
    assert book["tags"] == []

    # Lookup by title works too.
    assert db.get_book(title="Test Book")["id"] == book_id


def test_create_book_duplicate_title_returns_existing_id(db):
    first = db.create_book("Same Title")
    second = db.create_book("Same Title")
    assert first == second


def test_get_book_missing_returns_none(db):
    assert db.get_book(book_id=99999) is None


def test_save_chapter_get_chapter_round_trip(db):
    book_id = db.create_book("Chapter Book")
    source = ["hello source", "", "second source line"]
    translated = ["hello translated", "", "second translated line"]

    chapter_id = db.save_chapter(book_id, 1, "Chapter One", source,
                                 translated, summary="a summary")
    assert isinstance(chapter_id, int)

    ch = db.get_chapter(book_id=book_id, chapter_number=1)
    assert ch["id"] == chapter_id
    assert ch["book_id"] == book_id
    # get_chapter returns key "chapter" for the number and "content" for the
    # translated lines ("untranslated" for source).
    assert ch["chapter"] == 1
    assert ch["title"] == "Chapter One"
    assert ch["content"] == translated
    assert ch["untranslated"] == source
    assert ch["summary"] == "a summary"
    assert ch["book_title"] == "Chapter Book"
    # Model defaults to config.translation_model when not passed.
    assert ch["model"] == "test:model"

    # Lookup by chapter_id also works.
    assert db.get_chapter(chapter_id=chapter_id)["id"] == chapter_id


def test_save_chapter_update_existing(db):
    book_id = db.create_book("Update Book")
    first_id = db.save_chapter(book_id, 1, "Old Title", ["src"], ["old text"])
    second_id = db.save_chapter(book_id, 1, "New Title", ["src"], ["new text"])
    assert first_id == second_id

    ch = db.get_chapter(book_id=book_id, chapter_number=1)
    assert ch["title"] == "New Title"
    assert ch["content"] == ["new text"]


def test_save_chapter_unknown_book_returns_none(db):
    assert db.save_chapter(99999, 1, "T", ["s"], ["t"]) is None


def test_list_chapters_uses_chapter_key(db):
    book_id = db.create_book("List Book")
    db.save_chapter(book_id, 1, "One", ["a"], ["A"])
    db.save_chapter(book_id, 3, "Three", ["c"], ["C"])

    chapters = db.list_chapters(book_id)
    assert [c["chapter"] for c in chapters] == [1, 3]
    assert [c["title"] for c in chapters] == ["One", "Three"]
    # list_chapters rows carry metadata only, no content keys.
    assert "content" not in chapters[0]
    assert "untranslated" not in chapters[0]


def test_list_books_includes_created_book(db):
    book_id = db.create_book("Listed Book")
    db.save_chapter(book_id, 1, "One", ["a"], ["A"])

    books = db.list_books()
    match = [b for b in books if b["id"] == book_id]
    assert len(match) == 1
    assert match[0]["title"] == "Listed Book"
    assert match[0]["chapter_count"] == 1
