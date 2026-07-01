"""Tests for DatabaseManager strict_writes opt-in error propagation (step A4).

Default behavior (strict_writes=False) must keep the legacy swallow-and-return
contract that ~40 root scripts rely on. strict_writes=True (used by the web
layer) must re-raise real write failures instead of masking them as falsy.
Also covers list_books resilience to per-row corrupt JSON columns.
"""
import pytest

from conftest import FakeConfig, FakeLogger


class BoomError(RuntimeError):
    pass


def _break_connection(db):
    """Make every new DB connection blow up, simulating a hard write failure."""
    def boom():
        raise BoomError("simulated connection failure")
    db.backend.get_connection = boom


@pytest.fixture
def strict_db(tmp_path):
    from database import DatabaseManager

    return DatabaseManager(FakeConfig(tmp_path), FakeLogger(), strict_writes=True)


# ---------------------------------------------------------------------------
# Default (lenient) mode: failures return falsy, never raise
# ---------------------------------------------------------------------------

def test_default_write_failure_returns_falsy(db):
    _break_connection(db)

    assert db.create_book("T") is None
    assert db.save_chapter(1, 1, "t", ["src"], ["dst"]) is None
    assert db.add_entity("characters", "张三", "Zhang San", book_id=1) is False
    assert db.update_book(1, title="x") is False
    assert db.delete_book(1) is False
    assert db.delete_entity("characters", "张三") is False
    assert db.add_to_queue(1, "content") is None
    assert db.remove_from_queue(1) is False
    result = db.replace_in_chapters(1, "a", "b")
    assert result == {'affected_chapters': 0, 'total_replacements': 0, 'can_undo': False}


def test_default_flag_defaults_false(db):
    assert db.strict_writes is False


# ---------------------------------------------------------------------------
# Strict mode: the same failures raise
# ---------------------------------------------------------------------------

def test_strict_write_failure_raises(strict_db):
    assert strict_db.strict_writes is True
    _break_connection(strict_db)

    with pytest.raises(BoomError):
        strict_db.create_book("T")
    # save_chapter first does a get_book() READ (which still swallows and
    # returns None → "book not found" path); stub it so the WRITE runs.
    strict_db.get_book = lambda **kw: {"id": 1, "title": "T"}
    with pytest.raises(BoomError):
        strict_db.save_chapter(1, 1, "t", ["src"], ["dst"])
    with pytest.raises(BoomError):
        strict_db.add_entity("characters", "张三", "Zhang San", book_id=1)
    with pytest.raises(BoomError):
        strict_db.update_book(1, title="x")
    with pytest.raises(BoomError):
        strict_db.delete_book(1)
    with pytest.raises(BoomError):
        strict_db.add_to_queue(1, "content")
    with pytest.raises(BoomError):
        strict_db.replace_in_chapters(1, "a", "b")


def test_strict_save_entities_still_writes_backup_then_raises(strict_db, tmp_path):
    strict_db.entities = {"characters": {"张三": {"translation": "Zhang San"}}}
    _break_connection(strict_db)

    with pytest.raises(BoomError):
        strict_db.save_entities()

    # The entities_backup.json fallback must have run before the re-raise.
    backup = tmp_path / "entities_backup.json"
    assert backup.exists()


def test_strict_normal_falsy_paths_do_not_raise(strict_db):
    """Business-logic falsy returns (not-found etc.) must not become exceptions."""
    assert strict_db.delete_book(99999) is False           # book not found
    assert strict_db.remove_from_queue(99999) is False     # queue item not found
    assert strict_db.delete_chapter(chapter_id=99999) is False


def test_strict_successful_writes_unchanged(strict_db):
    book_id = strict_db.create_book("Strict Book", author="A")
    assert book_id
    assert strict_db.add_entity("characters", "李四", "Li Si", book_id=book_id) is True
    assert strict_db.update_book(book_id, title="Renamed") is True


# ---------------------------------------------------------------------------
# list_books: one corrupt tags/categories row must not hide the library
# ---------------------------------------------------------------------------

def test_list_books_survives_corrupt_json_row(db):
    good_id = db.create_book("Good Book")
    bad_id = db.create_book("Bad Book")
    assert good_id and bad_id

    db.set_book_tags(good_id, ["xianxia"])
    db.set_book_categories(good_id, ["Action"])

    conn = db.backend.get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE books SET tags = ?, categories = ? WHERE id = ?",
        ("not-json", "{broken", bad_id),
    )
    conn.commit()
    conn.close()

    books = db.list_books()
    assert {b["id"] for b in books} == {good_id, bad_id}

    by_id = {b["id"]: b for b in books}
    assert by_id[bad_id]["tags"] == []
    assert by_id[bad_id]["categories"] is None
    assert by_id[good_id]["tags"] == ["xianxia"]
    assert by_id[good_id]["categories"] is not None
