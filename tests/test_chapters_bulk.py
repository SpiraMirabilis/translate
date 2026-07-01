"""Tests for get_chapters_bulk, list_chapters pagination and flush_reader_views."""
import json

import pytest


@pytest.fixture
def book_with_chapters(db):
    book_id = db.create_book("Bulk Test Book", author="A")
    for n in (1, 2, 3, 5, 8):
        db.save_chapter(
            book_id=book_id,
            chapter_number=n,
            untranslated_content=[f"src {n}"],
            translated_content=[f"Chapter {n}", f"line one of {n}", f"line two of {n}"],
            title=f"Title {n}",
        )
    return book_id


class TestGetChaptersBulk:
    def test_all_chapters_shape_matches_get_chapter(self, db, book_with_chapters):
        bulk = db.get_chapters_bulk(book_with_chapters)
        assert [c["chapter"] for c in bulk] == [1, 2, 3, 5, 8]
        single = db.get_chapter(book_id=book_with_chapters, chapter_number=3)
        b3 = next(c for c in bulk if c["chapter"] == 3)
        for key in ("id", "book_id", "chapter", "title", "content", "summary",
                    "translation_date", "model", "book_title", "is_proofread"):
            assert b3[key] == single[key], key
        assert "untranslated" not in b3

    def test_subset_and_untranslated(self, db, book_with_chapters):
        bulk = db.get_chapters_bulk(book_with_chapters, [5, 2], include_untranslated=True)
        assert [c["chapter"] for c in bulk] == [2, 5]
        assert bulk[0]["untranslated"] == db.get_chapter(
            book_id=book_with_chapters, chapter_number=2)["untranslated"]

    def test_missing_numbers_skipped(self, db, book_with_chapters):
        bulk = db.get_chapters_bulk(book_with_chapters, [1, 99])
        assert [c["chapter"] for c in bulk] == [1]

    def test_corrupt_row_falls_back_to_newline_split(self, db, book_with_chapters):
        conn = db.backend.get_connection()
        cur = conn.cursor()
        cur.execute(
            "UPDATE chapters SET translated_content = ? WHERE book_id = ? AND chapter_number = ?",
            ("plain\ntext", book_with_chapters, 2))
        conn.commit()
        conn.close()
        bulk = db.get_chapters_bulk(book_with_chapters)
        b2 = next(c for c in bulk if c["chapter"] == 2)
        assert b2["content"] == ["plain", "text"]
        assert len(bulk) == 5

    def test_empty_book(self, db):
        book_id = db.create_book("Empty", author="A")
        assert db.get_chapters_bulk(book_id) == []


class TestListChaptersPagination:
    def test_default_returns_all(self, db, book_with_chapters):
        assert len(db.list_chapters(book_with_chapters)) == 5

    def test_limit_offset(self, db, book_with_chapters):
        page = db.list_chapters(book_with_chapters, limit=2, offset=1)
        assert [c["chapter"] for c in page] == [2, 3]


class TestFlushReaderViews:
    def test_flush_writes_rows_and_bumps(self, db, book_with_chapters):
        views = [(book_with_chapters, 1, "1.1.1.1"),
                 (book_with_chapters, 2, "1.1.1.1"),
                 (book_with_chapters, 1, "2.2.2.2")]
        db.flush_reader_views(views, {book_with_chapters: 4})

        conn = db.backend.get_connection()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM reader_log WHERE book_id = ?", (book_with_chapters,))
        assert cur.fetchone()[0] == 3
        cur.execute("SELECT view_count FROM books WHERE id = ?", (book_with_chapters,))
        assert cur.fetchone()[0] == 4
        conn.close()

    def test_flush_empty_noop(self, db):
        db.flush_reader_views([], {})


class TestViewLogger:
    def test_buffered_flush(self, db, book_with_chapters):
        from web.services.view_logger import ViewLogger
        vl = ViewLogger(db, flush_interval=999)  # manual flush only
        vl.log_view(book_with_chapters, 1, "9.9.9.9")
        vl.log_view(book_with_chapters, 2, "9.9.9.9")
        vl.bump_book(book_with_chapters)
        vl.flush()

        conn = db.backend.get_connection()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM reader_log WHERE ip = '9.9.9.9'")
        assert cur.fetchone()[0] == 2
        cur.execute("SELECT view_count FROM books WHERE id = ?", (book_with_chapters,))
        assert cur.fetchone()[0] == 3  # 2 chapter views + 1 book bump
        conn.close()

        # Second flush is a no-op (buffer drained)
        vl.flush()
        conn = db.backend.get_connection()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM reader_log WHERE ip = '9.9.9.9'")
        assert cur.fetchone()[0] == 2
        conn.close()

    def test_flush_failure_drops_views_without_raising(self, db):
        from web.services.view_logger import ViewLogger
        vl = ViewLogger(db, flush_interval=999)
        vl.log_view(12345, 1, "8.8.8.8")
        original = db.flush_reader_views
        db.flush_reader_views = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
        try:
            vl.flush()  # must not raise
        finally:
            db.flush_reader_views = original
        vl.flush()  # buffer was drained despite failure — no retry storm
