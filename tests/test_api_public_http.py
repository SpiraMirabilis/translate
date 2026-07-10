"""HTTP-level tests for the public reader API (web/api/public.py routes).

The public library is enabled (T9_PUBLIC_LIBRARY=1 in the web_app fixture)
so these endpoints work without a session cookie. Requests send an Origin
header matching the test host to satisfy public_guard.origin_check.
"""
import pytest


@pytest.fixture(autouse=True)
def reset_public_limiter():
    """The per-IP sliding-window limiter is module-level state shared across
    tests (all ASGI test requests come from the same client IP), so clear it
    around every test."""
    from web.api import public as public_api

    public_api._public_limiter.reset()
    yield
    public_api._public_limiter.reset()


@pytest.fixture
def public_client(web_app):
    from tests.api_client import SyncASGIClient

    return SyncASGIClient(
        web_app, headers={"Origin": "http://testserver"})


@pytest.fixture
def public_book(db):
    """One public book with three translated chapters, seeded through the
    db fixture (same tmp SQLite file as the app)."""
    book_id = db.create_book("Public HTTP Book", author="Pub Author")
    for n in (1, 2, 3):
        db.save_chapter(
            book_id=book_id,
            chapter_number=n,
            untranslated_content=[f"第{n}章", f"原文 {n}"],
            translated_content=[f"Chapter {n}", f"Public line one of {n}",
                                f"Public line two of {n}"],
            title=f"Public Title {n}",
        )
    return book_id


class TestPublicBooks:
    def test_list_books_includes_public_book(self, public_client, public_book):
        resp = public_client.get("/api/public/books")
        assert resp.status_code == 200, resp.text
        books = resp.json()["books"]
        mine = [b for b in books if b["id"] == public_book]
        assert len(mine) == 1
        assert mine[0]["title"] == "Public HTTP Book"
        assert mine[0]["chapter_count"] == 3

    def test_private_book_hidden(self, public_client, public_book, db):
        db.update_book(public_book, is_public=False)
        resp = public_client.get("/api/public/books")
        assert resp.status_code == 200
        assert all(b["id"] != public_book for b in resp.json()["books"])
        resp = public_client.get(f"/api/public/books/{public_book}")
        assert resp.status_code == 404

    def test_unknown_book_404(self, public_client, web_app):
        resp = public_client.get("/api/public/books/999999")
        assert resp.status_code == 404
        resp = public_client.get("/api/public/books/999999/chapters")
        assert resp.status_code == 404


class TestPublicChapters:
    def test_get_chapter_returns_content(self, public_client, public_book):
        resp = public_client.get(f"/api/public/books/{public_book}/chapters/2")
        assert resp.status_code == 200, resp.text
        ch = resp.json()
        assert ch["chapter"] == 2
        assert ch["title"] == "Public Title 2"
        # The leading "Chapter N" heading line is stripped for the reader
        assert ch["content"] == ["Public line one of 2", "Public line two of 2"]

    def test_missing_chapter_404(self, public_client, public_book):
        resp = public_client.get(f"/api/public/books/{public_book}/chapters/99")
        assert resp.status_code == 404

    def test_chapter_list_honors_limit_and_offset(self, public_client, public_book):
        resp = public_client.get(f"/api/public/books/{public_book}/chapters")
        assert resp.status_code == 200
        assert [c["chapter"] for c in resp.json()["chapters"]] == [1, 2, 3]

        resp = public_client.get(
            f"/api/public/books/{public_book}/chapters",
            params={"limit": 1, "offset": 1})
        assert resp.status_code == 200
        chapters = resp.json()["chapters"]
        assert [c["chapter"] for c in chapters] == [2]
        assert chapters[0]["title"] == "Public Title 2"


class TestRateLimit:
    def test_429_after_limit_exceeded(self, public_client, web_app, monkeypatch):
        """Drop the module-level limiter's window to 5 requests and verify the
        6th gets 429. Uses a nonexistent book so each request is cheap (404s
        still consume the limiter — guard runs before the lookup)."""
        from web.api import public as public_api

        limiter = public_api._public_limiter
        limiter.reset()
        monkeypatch.setattr(limiter, "limit", 5)

        for _ in range(5):
            resp = public_client.get("/api/public/books/999999")
            assert resp.status_code == 404

        resp = public_client.get("/api/public/books/999999")
        assert resp.status_code == 429
        assert resp.json()["detail"] == "Too many requests"


class TestChapterViewBeacon:
    """Views are recorded by an explicit beacon, never by fetching content.

    The reader prefetches the next two chapters and serves cache hits without
    touching the origin, so fetch-time counting was wrong in both directions.
    """

    @staticmethod
    def _views(db, book_id):
        from web.api import public as public_api
        public_api._view_logger.flush()   # start() is a no-op under test
        conn = db.backend.get_connection()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM reader_log WHERE book_id = ?", (book_id,))
        n = cur.fetchone()[0]
        conn.close()
        return n

    def test_chapter_get_does_not_log_a_view(self, public_client, public_book, db):
        resp = public_client.get(f"/api/public/books/{public_book}/chapters/1")
        assert resp.status_code == 200, resp.text
        assert self._views(db, public_book) == 0

    def test_batch_prefetch_does_not_log_views(self, public_client, public_book, db):
        resp = public_client.get(
            f"/api/public/books/{public_book}/chapters/batch?nums=2,3")
        assert resp.status_code == 200, resp.text
        assert len(resp.json()["chapters"]) == 2
        assert self._views(db, public_book) == 0

    def test_beacon_logs_a_view(self, public_client, public_book, db):
        resp = public_client.post(f"/api/public/books/{public_book}/chapters/1/view")
        assert resp.status_code == 204, resp.text
        assert self._views(db, public_book) == 1

    def test_beacon_is_deduped_per_chapter(self, public_client, public_book, db):
        for _ in range(3):
            public_client.post(f"/api/public/books/{public_book}/chapters/1/view")
        public_client.post(f"/api/public/books/{public_book}/chapters/2/view")
        assert self._views(db, public_book) == 2

    def test_beacon_bumps_book_view_count(self, public_client, public_book, db):
        public_client.post(f"/api/public/books/{public_book}/chapters/1/view")
        self._views(db, public_book)   # force flush
        conn = db.backend.get_connection()
        cur = conn.cursor()
        cur.execute("SELECT view_count FROM books WHERE id = ?", (public_book,))
        assert cur.fetchone()[0] == 1
        conn.close()

    def test_beacon_on_private_book_404s_and_logs_nothing(self, public_client, public_book, db):
        db.update_book(public_book, is_public=False)
        resp = public_client.post(f"/api/public/books/{public_book}/chapters/1/view")
        assert resp.status_code == 404
        assert self._views(db, public_book) == 0
