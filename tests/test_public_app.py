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
    public_api._trending_memo.update(at=0.0, views=None)
    yield
    public_api._public_limiter.reset()
    public_api._trending_memo.update(at=0.0, views=None)


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


def test_public_books_carry_source_language(pub_client, db):
    book_id = db.create_book("Korean Book", author="A", source_language="ko")
    db.save_chapter(book_id=book_id, chapter_number=1,
                    untranslated_content=["x"], translated_content=["Chapter 1", "y"], title="T")
    books = {b["id"]: b for b in pub_client.get("/api/public/books").json()["books"]}
    assert books[book_id]["source_language"] == "ko"


def _two_books(db):
    older = db.create_book("Older Book", author="A")
    newer = db.create_book("Newer Book", author="A")
    for bid in (older, newer):
        db.save_chapter(book_id=bid, chapter_number=1,
                        untranslated_content=["x"], translated_content=["Chapter 1", "y"], title="T")
    return older, newer


def test_trending_sort_follows_recent_views(pub_client, db):
    older, newer = _two_books(db)
    for _ in range(3):
        db.log_reader_view(older, 1, "10.0.0.1")
    db.log_reader_view(newer, 1, "10.0.0.2")
    books = pub_client.get("/api/public/books?sort=trending").json()["books"]
    ids = [b["id"] for b in books]
    assert ids.index(older) < ids.index(newer)
    by_id = {b["id"]: b for b in books}
    assert by_id[older]["recent_views"] == 3
    assert by_id[newer]["recent_views"] == 1


def test_updated_sort_uses_published_chapter_not_modified_date(pub_client, db):
    older, newer = _two_books(db)
    # A new chapter on `older` makes it the most recently updated book...
    db.save_chapter(book_id=older, chapter_number=2,
                    untranslated_content=["x"], translated_content=["Chapter 2", "z"], title="T2")
    # ...while an edit that bumps only modified_date must not promote `newer`.
    with db._conn() as conn:
        conn.cursor().execute("UPDATE books SET modified_date = '2999-01-01T00:00:00' WHERE id = ?", (newer,))
    ids = [b["id"] for b in pub_client.get("/api/public/books?sort=updated").json()["books"]]
    assert ids.index(older) < ids.index(newer)


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

    def test_root_serves_the_library_without_redirecting(self, pub_client):
        # "/" must answer with the page, not a hop to /library: a redirecting
        # root is the weaker signal for crawlers and an extra round trip for
        # every reader who types the bare domain.
        resp = pub_client.get("/", follow_redirects=False)
        assert resp.status_code == 200
        assert "<html" in resp.text.lower()

    @staticmethod
    def _inline_library_data(html):
        import json
        import re
        m = re.search(r'<script type="application/json" id="library-data">(.*?)</script>',
                      html, re.S)
        return json.loads(m.group(1)) if m else None

    def test_library_shell_inlines_the_api_payload(self, pub_client, public_book):
        # The SPA seeds its book query from this, so it must be exactly the
        # API's payload for the default sort — no second request on first paint.
        data = self._inline_library_data(pub_client.get("/library").text)
        assert data is not None
        assert data["sort"] == "trending"
        api = pub_client.get("/api/public/books?sort=trending").json()
        assert data["books"] == api["books"]
        assert any(b["title"] == "Split Proc Book" for b in data["books"])

    def test_inline_payload_cannot_close_its_script(self, pub_client, db):
        db.create_book("Evil </script><script>alert(1)</script> & <!-- Book", author="A")
        html = pub_client.get("/library").text
        assert "</script><script>alert(1)" not in html
        data = self._inline_library_data(html)
        assert any(b["title"].startswith("Evil </script>") for b in data["books"])

    def test_library_shell_carries_no_catalog_when_gated(self, pub_client, public_book):
        import settings_store

        settings_store.update({"public_library": False})
        try:
            html = pub_client.get("/library").text
        finally:
            settings_store.update({"public_library": True})
        assert self._inline_library_data(html) is None
        assert "Split Proc Book" not in html


def test_trending_memo_refreshes_in_the_background(monkeypatch):
    """An expired memo is served stale and refreshed off-request."""
    import threading
    from web.api import public as public_api

    release = threading.Event()
    calls = []

    class SlowDb:
        def recent_book_views(self, days):
            calls.append(days)
            release.wait(5)
            return {1: 99}

    monkeypatch.setattr(public_api, "_db", SlowDb())
    public_api._trending_memo.update(at=0.0, views={1: 5})
    try:
        assert public_api._recent_views() == {1: 5}   # stale, not blocked
        assert public_api._recent_views() == {1: 5}   # no second refresh
        release.set()
        for _ in range(100):
            if public_api._trending_memo["views"] == {1: 99}:
                break
            threading.Event().wait(0.02)
        assert public_api._trending_memo["views"] == {1: 99}
        assert len(calls) == 1
    finally:
        release.set()
        public_api._trending_memo.update(at=0.0, views=None)


TEST_GTM_ID = "GTM-TEST123"


@pytest.fixture
def gtm_id(monkeypatch):
    # The container ID lives in .env (GTM_ID); tests pin their own.
    monkeypatch.setenv("GTM_ID", TEST_GTM_ID)
    return TEST_GTM_ID


def test_simple_page_carries_gtm(pub_client, public_book, gtm_id):
    html = pub_client.get("/simple").text
    head, body = html.split("<body>", 1)
    assert f"gtm.js?id='+i+dl" in head and gtm_id in head
    assert f"ns.html?id={gtm_id}" in body


def test_admin_pages_carry_no_gtm(web_app, public_book, gtm_id):
    # The admin host serves the same pages; its traffic stays out of analytics.
    from tests.api_client import SyncASGIClient

    client = SyncASGIClient(web_app)
    resp = client.get("/simple")
    if resp.status_code == 200:
        assert gtm_id not in resp.text


def test_with_gtm_placement_and_idempotence(gtm_id):
    from web.analytics import with_gtm

    html = with_gtm('<html><head lang="x"><title>t</title></head><body class="c"><p>x</p></body></html>')
    assert html.index("<head") < html.index("googletagmanager.com/gtm.js") < html.index("<title>")
    assert html.index("<body") < html.index("ns.html") < html.index("<p>x</p>")
    assert with_gtm(html) == html
    assert html.count(gtm_id) == 2


@pytest.mark.parametrize("value", [None, "", "UA-12345-1", "GTM-ABC');alert(1);//"])
def test_with_gtm_needs_a_valid_configured_id(monkeypatch, value):
    # Unset (a fresh clone) or not a GTM container ID → no snippet at all;
    # the value goes into a <script>, so a malformed one is never spliced.
    from web.analytics import with_gtm

    if value is None:
        monkeypatch.delenv("GTM_ID", raising=False)
    else:
        monkeypatch.setenv("GTM_ID", value)
    page = "<html><head></head><body></body></html>"
    assert with_gtm(page) == page


@pytest.mark.skipif(not os.path.isdir(_dist), reason="frontend dist not built")
def test_spa_routes_and_404_carry_gtm(pub_client, public_book, gtm_id):
    for path in ("/", "/library", f"/library/book/{public_book}",
                 f"/library/read/{public_book}/1", "/library/whatever", "/queue"):
        assert pub_client.get(path).text.count(gtm_id) == 2, path


def test_site_info_carries_nu_forum_username(pub_client):
    import settings_store

    # Set both ways explicitly: a fresh tmp settings.json seeds from os.environ,
    # which earlier imports may have filled from the real settings.json.
    settings_store.update({"nu_forum_username": ""})
    assert pub_client.get("/api/public/site_info").json()["nu_forum_username"] == ""
    settings_store.update({"nu_forum_username": "SomeTranslator"})
    assert pub_client.get("/api/public/site_info").json()["nu_forum_username"] == "SomeTranslator"


def test_geoblock_blocks_listed_countries(pub_client, public_book):
    import settings_store

    settings_store.update({"geoblock_countries": "CN,RU", "geoblock_message": "Not here."})
    try:
        cn = {"CF-IPCountry": "cn"}
        api = pub_client.get("/api/public/books", headers=cn)
        assert api.status_code == 403
        assert api.json() == {"detail": "Not here.", "geoblocked": True}
        assert api.headers["cache-control"] == "no-store"
        page = pub_client.get("/library", headers=cn)
        assert page.status_code == 403 and "Not here." in page.text
        assert pub_client.get("/simple", headers={"CF-IPCountry": "RU"}).status_code == 403
        # Health stays open for the watchdog; unsubscribe must always work.
        assert pub_client.get("/api/health", headers=cn).status_code == 200
        assert pub_client.get("/api/public/comments/unsubscribe", headers=cn).status_code != 403
        # Other countries, and requests with no country header, pass — and the
        # cacheable API varies on the country so Apache's cache can't leak.
        ok = pub_client.get("/api/public/books", headers={"CF-IPCountry": "US"})
        assert ok.status_code == 200
        assert "CF-IPCountry" in ok.headers["vary"]
        assert pub_client.get("/api/public/books").status_code == 200
    finally:
        settings_store.update({"geoblock_countries": "", "geoblock_message": ""})
    off = pub_client.get("/api/public/books", headers={"CF-IPCountry": "CN"})
    assert off.status_code == 200
    assert "CF-IPCountry" not in off.headers.get("vary", "")


def test_geoblock_default_message_and_escaping(pub_client):
    import settings_store
    from web.services import geoblock

    settings_store.update({"geoblock_countries": "KP"})
    try:
        body = pub_client.get("/library", headers={"CF-IPCountry": "KP"}).text
        assert "isn&#x27;t available in your region" in body
        settings_store.update({"geoblock_message": "<b>no</b>"})
        body = pub_client.get("/library", headers={"CF-IPCountry": "KP"}).text
        assert "<b>no</b>" not in body and "&lt;b&gt;no&lt;/b&gt;" in body
    finally:
        settings_store.update({"geoblock_countries": "", "geoblock_message": ""})
    assert geoblock.parse_countries(" ru,cn;  kp ru") == ["CN", "KP", "RU"]
    with pytest.raises(ValueError):
        geoblock.parse_countries("CN, China")


def test_geoblock_settings_round_trip_and_admin_is_never_blocked(
        admin_client, tmp_path, monkeypatch):
    import settings_store
    # web_app doesn't redirect the store; never write the real settings.json.
    monkeypatch.setattr(settings_store, "_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setattr(settings_store, "_data", None)
    monkeypatch.setattr(settings_store, "_mtime_ns", None)
    monkeypatch.setenv("GEOBLOCK_COUNTRIES", "")  # restored after update() syncs env
    monkeypatch.setenv("GEOBLOCK_MESSAGE", "")

    resp = admin_client.put("/api/settings", json={"geoblock_countries": "ru, cn",
                                                   "geoblock_message": "  Sorry.  "})
    assert resp.status_code == 200, resp.text
    got = admin_client.get("/api/settings", headers={"CF-IPCountry": "CN"})
    assert got.status_code == 200
    assert got.json()["geoblock_countries"] == "CN,RU"
    assert got.json()["geoblock_message"] == "Sorry."
    assert admin_client.get("/api/public/books",
                            headers={"CF-IPCountry": "CN"}).status_code == 200
    bad = admin_client.put("/api/settings", json={"geoblock_countries": "China"})
    assert bad.status_code == 400
    assert settings_store.get("geoblock_countries") == "CN,RU"


def test_wordpress_feed_paths_redirect_to_the_feed(pub_client):
    for path in ("/feed", "/feed/", "/feed/rss-chapters"):
        resp = pub_client.get(path, follow_redirects=False)
        assert resp.status_code == 301 and resp.headers["location"] == "/api/public/feed.rss", path
    assert pub_client.get("/feedback", follow_redirects=False).status_code != 301


def test_feed_items_carry_the_nu_title_as_a_category(pub_client, public_book, db):
    xml = pub_client.get("/api/public/feed.rss").text
    assert xml.count("<category>") == 1                      # our title only
    db.set_book_nu_title(public_book, "Split Process Book (NU)")
    xml = pub_client.get(f"/api/public/books/{public_book}/feed.rss").text
    assert "<category>Split Proc Book</category>" in xml
    assert "<category>Split Process Book (NU)</category>" in xml
    db.set_book_nu_title(public_book, "split proc book")      # same name: no duplicate
    assert pub_client.get(f"/api/public/books/{public_book}/feed.rss").text.count("<category>") == 1



def test_book_and_chapter_pages_carry_alternate_names(pub_client, public_book, db):
    import json, re
    def ld(html):
        m = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
        return json.loads(m.group(1)) if m else None
    # same name as ours: no "also known as", no alternateName
    db.set_book_nu_title(public_book, "split proc book")
    page = pub_client.get(f"/library/book/{public_book}").text
    assert "Also known as" not in page and "alternateName" not in page
    assert pub_client.get(f"/api/public/books/{public_book}").json()["nu_title"] == "split proc book"

    db.set_book_nu_title(public_book, "The NU Name")
    db.set_book_source_title(public_book, "原名")
    page = pub_client.get(f"/library/book/{public_book}").text
    assert "Also known as: The NU Name · 原名" in page
    assert ld(page)["alternateName"] == ["The NU Name", "原名"]
    chapter = pub_client.get(f"/library/read/{public_book}/1").text
    data = ld(chapter)
    assert data["@type"] == "Chapter" and data["isPartOf"]["alternateName"] == ["The NU Name", "原名"]
    assert "Also known as" not in chapter                  # hidden on chapter pages
