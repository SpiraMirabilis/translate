"""HTTP-level tests for the public-only app (web/public_app.py surface).

The point of the split process is that admin routes DO NOT EXIST here —
these tests lock that in, along with the public gate middleware and the
auth-status stub.
"""
import os

import pytest


@pytest.fixture(autouse=True)
def reset_public_limiter():
    from web.api import public as public_api

    public_api._public_limiter.reset()
    yield
    public_api._public_limiter.reset()


@pytest.fixture
def pub_client(public_web_app):
    from tests.api_client import SyncASGIClient

    return SyncASGIClient(public_web_app, headers={"Origin": "http://testserver"})


@pytest.fixture
def public_book(db):
    book_id = db.create_book("Split Proc Book", author="Author")
    db.save_chapter(
        book_id=book_id,
        chapter_number=1,
        untranslated_content=["第1章", "原文"],
        translated_content=["Chapter 1", "A public line."],
        title="Title 1",
    )
    return book_id


def test_public_books_served(pub_client, public_book):
    resp = pub_client.get("/api/public/books")
    assert resp.status_code == 200, resp.text
    assert any(b["id"] == public_book for b in resp.json()["books"])


def test_health_open(pub_client):
    resp = pub_client.get("/api/health")
    assert resp.status_code == 200
    assert "checks" in resp.json()


def test_admin_routes_do_not_exist(public_web_app):
    paths = {getattr(r, "path", "") for r in public_web_app.routes}
    for admin_path in ("/api/books", "/api/queue", "/api/entities",
                       "/api/settings", "/ws", "/api/auth/login", "/docs"):
        assert not any(p == admin_path or p.startswith(admin_path + "/")
                       for p in paths), f"{admin_path} leaked into public app"


def test_admin_api_requests_rejected(pub_client):
    # Routes are absent, so these fall through to the SPA catch-all (GET)
    # or method matching — never an authenticated handler.
    assert pub_client.get("/api/books").status_code == 404
    assert pub_client.get("/api/queue").status_code == 404
    assert pub_client.post("/api/auth/login",
                           json={"password": "x"}).status_code in (404, 405)


def test_auth_status_stub(pub_client):
    resp = pub_client.get("/api/auth/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["authenticated"] is False
    assert body["auth_required"] is True
    assert body["public_library"] is True


def test_public_gate_closes_everything(pub_client, public_book):
    import settings_store

    settings_store.update({"public_library": False})
    try:
        assert pub_client.get("/api/public/books").status_code == 404
        assert pub_client.get("/simple").status_code == 404
        # Health stays open for the watchdog.
        assert pub_client.get("/api/health").status_code == 200
    finally:
        settings_store.update({"public_library": True})
    assert pub_client.get("/api/public/books").status_code == 200


def test_simple_page_served(pub_client, public_book):
    resp = pub_client.get("/simple")
    assert resp.status_code == 200
    assert "Split Proc Book" in resp.text


_dist = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "web", "frontend", "dist")


@pytest.mark.skipif(not os.path.isdir(_dist), reason="frontend dist not built")
class TestSpaCatchAll:
    def test_library_serves_spa(self, pub_client):
        resp = pub_client.get("/library")
        assert resp.status_code == 200
        assert "<html" in resp.text.lower()

    def test_reader_route_serves_spa(self, pub_client, public_book):
        resp = pub_client.get(f"/read/{public_book}/1")
        assert resp.status_code == 200
        assert "<html" in resp.text.lower()

    def test_admin_spa_routes_404(self, pub_client):
        for path in ("/queue", "/books", "/entities", "/settings"):
            assert pub_client.get(path).status_code == 404, path

    def test_root_redirects_to_library(self, pub_client):
        resp = pub_client.get("/", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/library"
