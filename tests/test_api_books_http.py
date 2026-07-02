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


class TestWriteEditor:
    """Original-works flow: empty-chapter creation, optimistic lock,
    revision snapshots, restore."""

    @pytest.fixture
    def original_book(self, admin_client):
        return _create_book(admin_client, title="Original Work",
                            is_original=True, genre="chinese_xianxia")

    def test_create_book_original_flag(self, admin_client, original_book):
        book = admin_client.get(f"/api/books/{original_book}").json()
        assert book["is_original"] is True
        # Genre presets don't apply to original works
        assert book["source_language"] == "en"

    def test_create_empty_chapter_defaults_number(self, admin_client, original_book):
        resp = admin_client.post(f"/api/books/{original_book}/chapters", json={})
        assert resp.status_code == 200, resp.text
        assert resp.json()["chapter_number"] == 1

        resp = admin_client.post(f"/api/books/{original_book}/chapters",
                                 json={"title": "The Long Road"})
        assert resp.json()["chapter_number"] == 2

        ch = admin_client.get(f"/api/books/{original_book}/chapters/2").json()
        assert ch["title"] == "The Long Road"
        assert ch["content"] == []
        assert ch["untranslated"] == []

    def test_create_duplicate_chapter_409(self, admin_client, original_book):
        admin_client.post(f"/api/books/{original_book}/chapters",
                          json={"chapter_number": 5})
        resp = admin_client.post(f"/api/books/{original_book}/chapters",
                                 json={"chapter_number": 5})
        assert resp.status_code == 409

    def test_save_snapshot_and_autosave_coalescing(self, admin_client, original_book):
        admin_client.post(f"/api/books/{original_book}/chapters", json={})
        url = f"/api/books/{original_book}/chapters/1"

        # Plain save (ChapterEditor path): no revision recorded
        resp = admin_client.put(url, json={"content": ["Plain save."]})
        assert resp.status_code == 200, resp.text
        assert resp.json()["translation_date"]
        revs = admin_client.get(f"{url}/revisions").json()["revisions"]
        assert revs == []

        # Explicit save: manual revision
        admin_client.put(url, json={"content": ["Draft one."], "snapshot": True})
        # Autosave right after: coalesced away (a revision exists within 10 min)
        admin_client.put(url, json={"content": ["Draft two."], "autosave": True})
        revs = admin_client.get(f"{url}/revisions").json()["revisions"]
        assert [r["kind"] for r in revs] == ["manual"]

        # Another explicit save: always records
        admin_client.put(url, json={"content": ["Draft three."], "snapshot": True})
        revs = admin_client.get(f"{url}/revisions").json()["revisions"]
        assert [r["kind"] for r in revs] == ["manual", "manual"]

    def test_optimistic_lock_409(self, admin_client, original_book):
        admin_client.post(f"/api/books/{original_book}/chapters", json={})
        url = f"/api/books/{original_book}/chapters/1"
        loaded = admin_client.get(url).json()["translation_date"]

        # In-date save succeeds and returns the new date
        resp = admin_client.put(url, json={
            "content": ["Edit A."], "expected_translation_date": loaded})
        assert resp.status_code == 200, resp.text
        new_date = resp.json()["translation_date"]
        assert new_date != loaded

        # Stale save is rejected with the server's current date
        resp = admin_client.put(url, json={
            "content": ["Edit B."], "expected_translation_date": loaded})
        assert resp.status_code == 409
        assert resp.json()["detail"]["translation_date"] == new_date

    def test_revision_get_and_restore(self, admin_client, original_book):
        admin_client.post(f"/api/books/{original_book}/chapters", json={})
        url = f"/api/books/{original_book}/chapters/1"
        admin_client.put(url, json={"content": ["Version one."], "snapshot": True,
                                    "title": "V1"})
        admin_client.put(url, json={"content": ["Version two."], "snapshot": True,
                                    "title": "V2"})

        revs = admin_client.get(f"{url}/revisions").json()["revisions"]
        v1 = next(r for r in revs if r["title"] == "V1")

        full = admin_client.get(f"{url}/revisions/{v1['id']}").json()
        assert full["content"] == ["Version one."]

        resp = admin_client.post(f"{url}/revisions/{v1['id']}/restore")
        assert resp.status_code == 200, resp.text

        ch = admin_client.get(url).json()
        assert ch["content"] == ["Version one."]
        assert ch["title"] == "V1"
        # Restore snapshotted the pre-restore content as an auto revision
        revs = admin_client.get(f"{url}/revisions").json()["revisions"]
        auto = [r for r in revs if r["kind"] == "auto"]
        assert len(auto) == 1
        pre = admin_client.get(f"{url}/revisions/{auto[0]['id']}").json()
        assert pre["content"] == ["Version two."]

    def test_revision_wrong_chapter_404(self, admin_client, original_book):
        admin_client.post(f"/api/books/{original_book}/chapters", json={})
        url = f"/api/books/{original_book}/chapters/1"
        admin_client.put(url, json={"content": ["x"], "snapshot": True})
        rev_id = admin_client.get(f"{url}/revisions").json()["revisions"][0]["id"]
        resp = admin_client.get(
            f"/api/books/{original_book}/chapters/99/revisions/{rev_id}")
        assert resp.status_code == 404
