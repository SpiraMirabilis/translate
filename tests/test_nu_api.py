"""HTTP tests for the NovelUpdates admin API (web/api/nu_api.py). The lane
itself is covered by test_nu_sync.py; here only what the endpoints record."""
import pytest

import nu_sync

PAST = "2026-01-01T00:00:00"


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(nu_sync, "HOLD_FILE", str(tmp_path / "nu_hold.json"))
    monkeypatch.setattr(nu_sync, "VOCAB_FILE", str(tmp_path / "nu_vocab.json"))
    monkeypatch.setattr(nu_sync, "site_base", lambda: "https://reader.test")
    monkeypatch.setattr(nu_sync, "cover_url_for", lambda db, book: None)


def _book(admin_client, db, title="NU API Book", source_title="我在仙界有个爹", chapters=(1, 2)):
    resp = admin_client.post("/api/books", json={"title": title, "source_title": source_title})
    assert resp.status_code == 200, resp.text
    book_id = resp.json()["id"]
    for n in chapters:
        db.save_chapter(book_id, n, f"Chapter {n}", ["源"], ["text"])
        db.set_chapter_published(book_id, n, PAST)
    return book_id


def test_book_view_defaults_and_suggested_start(admin_client, db):
    book_id = _book(admin_client, db)
    body = admin_client.get(f"/api/books/{book_id}/nu").json()
    assert body["state"]["state"] == "untracked" and body["state"]["mode"] == "off"
    assert body["suggested_start"] == 3


def test_suggested_start_is_1_only_when_nu_says_not_ours(admin_client, db):
    book_id = _book(admin_client, db)
    url = f"/api/books/{book_id}/nu"
    for state, expected in (("untracked", 3), ("needs_review", 3), ("active", 3),
                            ("linked", 1), ("new_series", 1), ("first_release_sent", 1)):
        db.update_book_nu(book_id, state=state)
        assert admin_client.get(url).json()["suggested_start"] == expected, state


def test_auto_mode_requires_start(admin_client, db):
    book_id = _book(admin_client, db)
    resp = admin_client.put(f"/api/books/{book_id}/nu", json={"mode": "auto"})
    assert resp.status_code == 409
    resp = admin_client.put(f"/api/books/{book_id}/nu", json={"mode": "auto", "start_chapter": 2})
    assert resp.status_code == 200
    st = resp.json()["state"]
    assert st["mode"] == "auto" and st["start_chapter"] == 2


def test_search_queues_and_find_all(admin_client, db):
    book_id = _book(admin_client, db)
    st = admin_client.post(f"/api/books/{book_id}/nu/search").json()["state"]
    assert st["state"] == "searching"
    assert st["search_names"] == ["我在仙界有个爹", "我在仙界有個爹"]

    other = _book(admin_client, db, title="Second", source_title="神农道君")
    out = admin_client.post("/api/nu/find-all").json()
    assert out["searching"] == [other]
    ov = admin_client.get("/api/nu/overview").json()
    assert {t["kind"] for t in ov["queued"]} == {"sync"}  # searches are queued by the planner


def test_pick_and_new_series_draft_gate(admin_client, db):
    book_id = _book(admin_client, db)
    st = admin_client.post(f"/api/books/{book_id}/nu/pick",
                           json={"url": "https://www.novelupdates.com/series/my-dad/",
                                 "title": "My Dad Is an Immortal"}).json()["state"]
    assert st["state"] == "linked" and st["nu_slug"] == "my-dad"
    assert admin_client.get(f"/api/books/{book_id}").json()["nu_title"] == "My Dad Is an Immortal"

    other = _book(admin_client, db, title="Fresh", source_title="新书")
    view = admin_client.post(f"/api/books/{other}/nu/pick", json={"new": True}).json()
    assert view["state"]["state"] == "new_series"
    draft = dict(view["state"]["series_draft"], genres=["Xianxia"])
    assert admin_client.put(f"/api/books/{other}/nu/draft", json=draft).status_code == 200
    # submit without a dry run is refused
    assert admin_client.post(f"/api/books/{other}/nu/draft/submit").status_code == 409
    resp = admin_client.post(f"/api/books/{other}/nu/draft/dry-run")
    assert resp.status_code == 200 and resp.json()["task_id"]


def test_resolve_check_release(admin_client, db):
    book_id = _book(admin_client, db)
    db.update_book_nu(book_id, state="active", nu_slug="s", hold_reason="check_nu")
    db.upsert_nu_release(book_id, 1, status="check", error="may have gone through")
    resp = admin_client.post(f"/api/books/{book_id}/nu/releases/1/resolve", json={"action": "sent"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"]["hold_reason"] is None
    assert body["releases"][0]["status"] == "sent"
    # nothing left to resolve
    assert admin_client.post(f"/api/books/{book_id}/nu/releases/1/resolve",
                             json={"action": "sent"}).status_code == 409


def test_count_badge(admin_client, db):
    book_id = _book(admin_client, db)
    assert admin_client.get("/api/nu/count").json()["count"] == 0
    db.update_book_nu(book_id, state="needs_review")
    assert admin_client.get("/api/nu/count").json()["count"] == 1
    nu_sync.set_hold("login failed")
    assert admin_client.get("/api/nu/count").json()["count"] == 2
    admin_client.post("/api/nu/hold/clear")
    assert admin_client.get("/api/nu/count").json()["count"] == 1


def test_original_books_refused(admin_client, db):
    resp = admin_client.post("/api/books", json={"title": "Mine", "is_original": True})
    book_id = resp.json()["id"]
    assert admin_client.post(f"/api/books/{book_id}/nu/search").status_code == 409


def test_screenshot_name_validated(admin_client):
    assert admin_client.get("/api/nu/screenshots/..passwd").status_code == 400
    assert admin_client.get("/api/nu/screenshots/shot.jpg").status_code == 400


def test_pending_series_endpoints(admin_client, db):
    book_id = _book(admin_client, db)
    base = f"/api/books/{book_id}/nu"
    # only a submitted series is looked up / posted by id
    assert admin_client.post(f"{base}/release-search").status_code == 409
    db.update_book_nu(book_id, state="series_submitted", series_draft={"title": "Pending Title"})
    view = admin_client.post(f"{base}/release-search").json()
    assert view["pending_title"] == "Pending Title"
    assert any(t["kind"] == "release_search" and t["options"]["title"] == "Pending Title"
               for t in view["tasks"])
    # no id yet: "post now" is refused
    assert admin_client.post(f"{base}/first-release/post", json={}).status_code == 409
    assert admin_client.post(f"{base}/pick", json={"id": "abc"}).status_code == 409
    st = admin_client.post(f"{base}/pick", json={"id": "123"}).json()["state"]
    assert st["nu_series_id"] == "123" and st["state"] == "series_submitted"
    resp = admin_client.post(f"{base}/first-release/post", json={"chapter": 1})
    assert resp.status_code == 200 and resp.json()["task_id"]
