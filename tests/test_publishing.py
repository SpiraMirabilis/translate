"""Chapter publishing: published_at semantics (NULL=draft, future=scheduled,
past=live), repo gating, publish endpoints, and public-surface invisibility."""
import datetime

import pytest


def _save(db, book_id, num, lines=None, **kw):
    return db.save_chapter(
        book_id=book_id, chapter_number=num, title=f"Chapter {num}",
        untranslated_content=[], translated_content=lines or [f"content {num}"], **kw)


FUTURE = (datetime.datetime.now() + datetime.timedelta(days=1)).isoformat()
PAST = (datetime.datetime.now() - datetime.timedelta(days=1)).isoformat()


class TestRepoDefaults:
    def test_translation_book_chapters_publish_immediately(self, db):
        book_id = db.create_book(title="Trans Book")
        _save(db, book_id, 1)
        ch = db.get_chapter(book_id=book_id, chapter_number=1)
        assert ch["published_at"] is not None
        assert db.get_chapter(book_id=book_id, chapter_number=1, published_only=True)

    def test_original_book_chapters_default_draft(self, db):
        book_id = db.create_book(title="Orig Book", is_original=True)
        _save(db, book_id, 1)
        ch = db.get_chapter(book_id=book_id, chapter_number=1)
        assert ch["published_at"] is None
        assert db.get_chapter(book_id=book_id, chapter_number=1, published_only=True) is None

    def test_publish_override_wins_both_ways(self, db):
        trans = db.create_book(title="Trans Draft Book")
        _save(db, trans, 1, publish=False)  # translate-to-draft run option
        assert db.get_chapter(book_id=trans, chapter_number=1)["published_at"] is None

        orig = db.create_book(title="Orig Published Book", is_original=True)
        _save(db, orig, 1, publish=True)
        assert db.get_chapter(book_id=orig, chapter_number=1)["published_at"] is not None

    def test_resave_never_touches_publish_state(self, db):
        book_id = db.create_book(title="Resave Book", is_original=True)
        _save(db, book_id, 1)
        db.set_chapter_published(book_id, 1, PAST)
        _save(db, book_id, 1, lines=["edited"], publish=False)  # editor re-save
        assert db.get_chapter(book_id=book_id, chapter_number=1)["published_at"] == PAST


class TestRepoGating:
    @pytest.fixture
    def book(self, db):
        book_id = db.create_book(title="Gated Book", is_original=True)
        _save(db, book_id, 1)  # draft
        _save(db, book_id, 2)
        db.set_chapter_published(book_id, 2, PAST)      # live
        _save(db, book_id, 3)
        db.set_chapter_published(book_id, 3, FUTURE)    # scheduled
        return book_id

    def test_list_chapters_gate(self, db, book):
        assert [c["chapter"] for c in db.list_chapters(book)] == [1, 2, 3]
        assert [c["chapter"] for c in db.list_chapters(book, published_only=True)] == [2]

    def test_get_chapters_bulk_gate(self, db, book):
        assert len(db.get_chapters_bulk(book)) == 3
        pub = db.get_chapters_bulk(book, published_only=True)
        assert [c["chapter"] for c in pub] == [2]
        assert pub[0]["published_at"] == PAST

    def test_search_gate(self, db, book):
        hits = db.search_book_chapters(book, "content", scope="translated",
                                       published_only=True)
        assert [h["chapter_number"] for h in hits] == [2]

    def test_scheduled_becomes_visible_when_time_passes(self, db, book):
        # Re-schedule chapter 3 into the past — visibility is purely query-time
        db.set_chapter_published(book, 3, PAST)
        assert [c["chapter"] for c in db.list_chapters(book, published_only=True)] == [2, 3]

    def test_list_books_published_counts(self, db, book):
        b = {x["id"]: x for x in db.list_books()}[book]
        assert b["chapter_count"] == 3
        assert b["published_chapter_count"] == 1

    def test_latest_published_at_ignores_future(self, db, book):
        assert db.latest_published_at(book) == PAST

    def test_set_chapters_published_stagger(self, db, book):
        schedule = [(1, PAST), (3, None)]
        assert db.set_chapters_published(book, schedule) == 2
        assert db.get_chapter(book_id=book, chapter_number=1)["published_at"] == PAST
        assert db.get_chapter(book_id=book, chapter_number=3)["published_at"] is None

    def test_set_chapter_published_missing_chapter(self, db, book):
        with pytest.raises(LookupError):
            db.set_chapter_published(book, 999, PAST)


class TestPublishApi:
    @pytest.fixture
    def original_book(self, admin_client):
        resp = admin_client.post("/api/books", json={"title": "Publish API Book",
                                                     "is_original": True})
        book_id = resp.json()["id"]
        for _ in range(3):
            admin_client.post(f"/api/books/{book_id}/chapters", json={})
        return book_id

    def test_new_original_chapters_are_drafts_and_publicly_invisible(self, api_client, admin_client, original_book):
        chapters = admin_client.get(f"/api/books/{original_book}/chapters").json()["chapters"]
        assert all(c["published_at"] is None for c in chapters)
        resp = api_client.get(f"/api/public/books/{original_book}/chapters")
        assert resp.json()["chapters"] == []
        resp = api_client.get(f"/api/public/books/{original_book}/chapters/1")
        assert resp.status_code == 404

    def test_publish_now_makes_chapter_public(self, api_client, admin_client, original_book):
        now = datetime.datetime.now().isoformat()
        resp = admin_client.put(f"/api/books/{original_book}/chapters/1/publish",
                                json={"published_at": now})
        assert resp.status_code == 200, resp.text
        assert resp.json()["published_at"] == now

        pub = api_client.get(f"/api/public/books/{original_book}/chapters").json()["chapters"]
        assert [c["chapter"] for c in pub] == [1]
        assert api_client.get(f"/api/public/books/{original_book}/chapters/1").status_code == 200

    def test_scheduled_chapter_stays_hidden(self, api_client, admin_client, original_book):
        admin_client.put(f"/api/books/{original_book}/chapters/1/publish",
                         json={"published_at": FUTURE})
        assert api_client.get(f"/api/public/books/{original_book}/chapters/1").status_code == 404

    def test_unpublish_via_null(self, api_client, admin_client, original_book):
        admin_client.put(f"/api/books/{original_book}/chapters/1/publish",
                         json={"published_at": PAST})
        resp = admin_client.put(f"/api/books/{original_book}/chapters/1/publish", json={})
        assert resp.json()["published_at"] is None
        assert api_client.get(f"/api/public/books/{original_book}/chapters/1").status_code == 404

    def test_invalid_timestamp_400(self, admin_client, original_book):
        resp = admin_client.put(f"/api/books/{original_book}/chapters/1/publish",
                                json={"published_at": "next tuesday"})
        assert resp.status_code == 400

    def test_batch_publish_stagger(self, admin_client, original_book):
        start = "2026-01-01T08:00:00"
        resp = admin_client.post(f"/api/books/{original_book}/chapters/batch-publish",
                                 json={"chapters": [3, 1, 2], "published_at": start,
                                       "interval_hours": 24})
        assert resp.status_code == 200, resp.text
        schedule = resp.json()["schedule"]
        # Ascending chapter order, one day apart
        assert [s["chapter"] for s in schedule] == [1, 2, 3]
        assert schedule[0]["published_at"] == "2026-01-01T08:00:00"
        assert schedule[1]["published_at"] == "2026-01-02T08:00:00"
        assert schedule[2]["published_at"] == "2026-01-03T08:00:00"
        assert resp.json()["updated"] == 3
        # start is in the past → all three are live now
        chapters = admin_client.get(f"/api/books/{original_book}/chapters").json()["chapters"]
        assert all(c["published_at"] for c in chapters)

    def test_batch_unpublish(self, admin_client, original_book):
        admin_client.post(f"/api/books/{original_book}/chapters/batch-publish",
                          json={"chapters": [1, 2, 3]})
        resp = admin_client.post(f"/api/books/{original_book}/chapters/batch-publish",
                                 json={"chapters": [1, 2], "unpublish": True})
        assert resp.json()["updated"] == 2
        chapters = {c["chapter"]: c for c in
                    admin_client.get(f"/api/books/{original_book}/chapters").json()["chapters"]}
        assert chapters[1]["published_at"] is None
        assert chapters[2]["published_at"] is None
        assert chapters[3]["published_at"] is not None


class TestPublicSurfaces:
    @pytest.fixture
    def mixed_book(self, admin_client, db):
        resp = admin_client.post("/api/books", json={"title": "Mixed Public Book"})
        book_id = resp.json()["id"]
        _save(db, book_id, 1, lines=["published words here"])
        _save(db, book_id, 2, lines=["draft words here"], publish=False)
        return book_id

    def test_public_list_books_counts_published_only(self, api_client, mixed_book):
        books = {b["id"]: b for b in api_client.get("/api/public/books").json()["books"]}
        assert books[mixed_book]["chapter_count"] == 1

    def test_public_search_excludes_drafts(self, api_client, mixed_book):
        resp = api_client.post(f"/api/public/books/{mixed_book}/search",
                               json={"query": "words here"})
        assert [r["chapter_number"] for r in resp.json()["results"]] == [1]

    def test_rss_feed_excludes_drafts(self, api_client, mixed_book):
        resp = api_client.get(f"/api/public/books/{mixed_book}/feed.rss")
        assert resp.status_code == 200
        assert "Chapter 1" in resp.text
        assert "Chapter 2" not in resp.text

    def test_comment_on_draft_rejected(self, api_client, mixed_book):
        body = {
            "book_id": mixed_book, "chapter_number": 2,
            "commenter_uuid": "01234567-89ab-4cde-8f01-23456789abcd",
            "email": "a@b.com",
            "display_name": "X", "body": "first!", "turnstile_token": "",
        }
        resp = api_client.post("/api/public/comments", json=body)
        assert resp.status_code == 404
        # Same comment on the published chapter passes the visibility gate
        # (may still fail later on captcha depending on env, so only assert
        # it's not the chapter-gate 404).
        resp = api_client.post("/api/public/comments", json={**body, "chapter_number": 1})
        assert resp.status_code != 404


class TestMigrationBackfill:
    def test_backfill_existing_chapters_stay_visible(self, tmp_path):
        from db_backend import SQLiteBackend
        from db.migrations import run_migrations
        from tests.conftest import FakeLogger

        backend = SQLiteBackend(str(tmp_path / "pub.db"))
        run_migrations(backend, FakeLogger())
        conn = backend.get_connection()
        cur = conn.cursor()
        cur.execute("DROP TABLE schema_migrations")
        cur.execute("ALTER TABLE chapters DROP COLUMN published_at")
        cur.execute("INSERT INTO books (title) VALUES ('Legacy')")
        book_id = cur.lastrowid
        cur.execute(
            "INSERT INTO chapters (book_id, chapter_number, title, untranslated_content, "
            "translated_content, translation_date) VALUES (?, 1, 'C1', '[]', '[]', '2025-01-01T00:00:00')",
            (book_id,))
        conn.commit()
        conn.close()

        run_migrations(backend, FakeLogger())

        conn = backend.get_connection()
        cur = conn.cursor()
        cur.execute("SELECT published_at FROM chapters WHERE book_id = ?", (book_id,))
        assert cur.fetchone()[0] == "2025-01-01T00:00:00"
        conn.close()
