"""HTTP-level tests for the admin books API (web/api/books.py routes).

Exercises the real FastAPI app (create_app with injected fake config/logger
on a tmp SQLite DB) through httpx.ASGITransport — see tests/api_client.py.
Admin routes authenticate via the real /api/auth/login flow (session cookie).
"""
import pytest


def _create_book(admin_client, title="HTTP Test Book", **extra):
    resp = admin_client.post("/api/books", json={"title": title, "author": "Author A", **extra})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["title"] == title
    return body["id"]


class TestAuth:
    def test_admin_routes_require_session(self, api_client):
        resp = api_client.get("/api/books")
        assert resp.status_code == 401

    def test_wrong_password_rejected(self, api_client):
        resp = api_client.post("/api/auth/login", json={"password": "nope"})
        assert resp.status_code == 403


class TestBooksCrud:
    def test_create_update_list(self, admin_client):
        book_id = _create_book(admin_client)

        # Update
        resp = admin_client.put(
            f"/api/books/{book_id}",
            json={"author": "Author B", "status": "completed"})
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"status": "ok"}

        # Detail reflects the update
        resp = admin_client.get(f"/api/books/{book_id}")
        assert resp.status_code == 200
        book = resp.json()
        assert book["author"] == "Author B"
        assert book["status"] == "completed"

        # List includes it
        resp = admin_client.get("/api/books")
        assert resp.status_code == 200
        books = resp.json()["books"]
        assert any(b["id"] == book_id and b["title"] == "HTTP Test Book"
                   for b in books)

    def test_update_invalid_status_rejected(self, admin_client):
        book_id = _create_book(admin_client, title="Status Book")
        resp = admin_client.put(f"/api/books/{book_id}", json={"status": "bogus"})
        assert resp.status_code == 400

    def test_missing_book_chapters_404(self, admin_client):
        resp = admin_client.get("/api/books/999999/chapters")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Book not found."


class TestExport:
    @pytest.fixture
    def book_with_chapter(self, admin_client, db):
        """Book created over HTTP; chapter saved through the db fixture
        (same tmp SQLite file as the app's DatabaseManager)."""
        book_id = _create_book(admin_client, title="Export Book")
        db.save_chapter(
            book_id=book_id,
            chapter_number=1,
            untranslated_content=["原文第一行"],
            translated_content=["Chapter 1", "The first exported line.",
                                "The second exported line."],
            title="The Beginning",
        )
        return book_id

    def test_export_text_returns_content(self, admin_client, book_with_chapter):
        resp = admin_client.get(f"/api/books/{book_with_chapter}/export",
                                params={"format": "text"})
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"].startswith("text/plain")
        assert "attachment" in resp.headers["content-disposition"]
        assert "The first exported line." in resp.text
        assert "The second exported line." in resp.text

    def test_export_html_has_toc_anchor(self, admin_client, book_with_chapter):
        resp = admin_client.get(f"/api/books/{book_with_chapter}/export",
                                params={"format": "html"})
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"].startswith("text/html")
        html = resp.text
        # TOC link and matching chapter anchor
        assert '<a href="#chapter-1">' in html
        assert '<h2 id="chapter-1">' in html
        assert "The first exported line." in html

    def test_export_no_chapters_404(self, admin_client):
        book_id = _create_book(admin_client, title="Empty Export Book")
        resp = admin_client.get(f"/api/books/{book_id}/export",
                                params={"format": "text"})
        assert resp.status_code == 404
