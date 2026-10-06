"""Sitemap generation, publication, and reader canonical tags.

Covers sitemap.py (URL collection, splitting, atomic publication), the
admin-only API (web/api/sitemap.py) and the two things the public process
does with the result: serve /sitemap.xml off disk and advertise it in
robots.txt. The canonical-tag tests belong here too — the tags exist to
agree with the URLs the sitemap lists.
"""
import os

import pytest

import sitemap as sm

BASE = "https://reader.example.com"


def _locs(xml_bytes):
    from xml.etree import ElementTree as ET
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    root = ET.fromstring(xml_bytes)
    return [e.text for e in root.findall(".//s:loc", ns)]


@pytest.fixture
def sitemap_dir(tmp_path, monkeypatch):
    """Where the app under test serves generated files from.

    create_app resolves the directory once, at startup, from
    config.script_dir — the same tmp_path the db/app fixtures share. The
    env override is cleared so a real SITEMAP_DIR in the developer's shell
    can't redirect a test at a live directory.
    """
    monkeypatch.delenv("SITEMAP_DIR", raising=False)
    return os.path.join(str(tmp_path) + "/", "sitemaps")


@pytest.fixture
def seeded_book(db):
    """One public book: chapters 1-3 published, chapter 4 a draft."""
    book_id = db.create_book("Sitemap Book", author="A")
    for n in (1, 2, 3):
        db.save_chapter(book_id=book_id, chapter_number=n,
                        untranslated_content=[f"原文 {n}"],
                        translated_content=[f"Chapter {n}", f"Line {n}"],
                        title=f"Title {n}")
    db.save_chapter(book_id=book_id, chapter_number=4,
                    untranslated_content=["原文 4"],
                    translated_content=["Chapter 4", "Line 4"],
                    title="Draft", publish=False)
    return book_id


class TestCollectUrls:
    def test_library_book_and_published_chapters(self, db, seeded_book):
        locs = [e["loc"] for e in sm.collect_urls(db, BASE)]
        assert f"{BASE}/library" in locs
        assert f"{BASE}/library/book/{seeded_book}" in locs
        for n in (1, 2, 3):
            assert f"{BASE}/library/read/{seeded_book}/{n}" in locs

    def test_draft_chapter_excluded(self, db, seeded_book):
        locs = [e["loc"] for e in sm.collect_urls(db, BASE)]
        assert f"{BASE}/library/read/{seeded_book}/4" not in locs

    def test_private_book_excluded(self, db, seeded_book):
        db.update_book(seeded_book, is_public=False)
        locs = [e["loc"] for e in sm.collect_urls(db, BASE)]
        assert all(f"/book/{seeded_book}" not in loc for loc in locs)
        assert all(f"/read/{seeded_book}/" not in loc for loc in locs)

    def test_trailing_slash_on_base_is_normalised(self, db, seeded_book):
        locs = [e["loc"] for e in sm.collect_urls(db, BASE + "/")]
        assert f"{BASE}/library" in locs

    def test_no_duplicate_locs(self, db, seeded_book):
        locs = [e["loc"] for e in sm.collect_urls(db, BASE)]
        assert len(locs) == len(set(locs))

    def test_include_chapters_false(self, db, seeded_book):
        locs = [e["loc"] for e in sm.collect_urls(db, BASE, include_chapters=False)]
        assert f"{BASE}/library/book/{seeded_book}" in locs
        assert all("/library/read/" not in loc for loc in locs)


class TestBuild:
    def test_single_file_under_limit(self, db, seeded_book):
        result = sm.build(db, BASE)
        assert result["index"] is False
        assert [n for n, _ in result["files"]] == ["sitemap.xml"]
        assert result["url_count"] == len(_locs(result["files"][0][1]))

    def test_splits_into_index_over_limit(self, db, seeded_book, monkeypatch):
        monkeypatch.setattr(sm, "MAX_URLS_PER_FILE", 2)
        result = sm.build(db, BASE)
        assert result["index"] is True
        names = [n for n, _ in result["files"]]
        assert names[0] == "sitemap.xml"
        assert names[1:] == [f"sitemap-{i}.xml" for i in range(1, len(names))]
        # The index names every part at the site root...
        index_locs = _locs(result["files"][0][1])
        assert index_locs == [f"{BASE}/{n}" for n in names[1:]]
        # ...and the parts together hold every URL, exactly once.
        part_locs = [loc for _, data in result["files"][1:] for loc in _locs(data)]
        assert len(part_locs) == result["url_count"] == len(set(part_locs))
        assert all(len(_locs(d)) <= 2 for _, d in result["files"][1:])

    def test_lastmod_is_w3c_and_present(self, db, seeded_book):
        from datetime import datetime
        xml = sm.build(db, BASE)["files"][0][1].decode()
        assert "<lastmod>" in xml
        for line in xml.splitlines():
            if "<lastmod>" in line:
                value = line.strip()[len("<lastmod>"):-len("</lastmod>")]
                dt = datetime.fromisoformat(value)   # raises if malformed
                assert dt.tzinfo is not None, "lastmod must carry an offset"


class TestW3CDatetime:
    def test_naive_gets_local_offset(self):
        out = sm.w3c_datetime("2026-04-21T15:25:23.605674")
        assert out.startswith("2026-04-21T15:25:23")
        assert out[19] in "+-"          # offset appended, seconds precision

    def test_aware_preserved(self):
        assert sm.w3c_datetime("2026-04-21T15:25:23+02:00") == "2026-04-21T15:25:23+02:00"

    def test_unparseable_is_none(self):
        assert sm.w3c_datetime("not a date") is None
        assert sm.w3c_datetime(None) is None
        assert sm.w3c_datetime("") is None


class TestSitemapApi:
    def test_requires_session(self, api_client):
        assert api_client.get("/api/sitemap.xml").status_code == 401
        assert api_client.get("/api/sitemap/status").status_code == 401
        assert api_client.get("/api/sitemap.zip").status_code == 401

    def test_absent_from_public_process(self, public_web_app):
        from tests.api_client import SyncASGIClient
        client = SyncASGIClient(public_web_app, headers={"Origin": "http://testserver"})
        # The public process 404s it — the router is never registered there.
        assert client.get("/api/sitemap.xml").status_code == 404

    def test_status_and_download(self, admin_client, seeded_book, monkeypatch):
        monkeypatch.setenv("SITE_BASE_URL", BASE)
        resp = admin_client.get("/api/sitemap/status?refresh=true")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["base_url"] == BASE
        assert body["index"] is False
        assert body["url_count"] >= 5     # library + book + 3 chapters
        assert body["files"] == [{"name": "sitemap.xml", "bytes": body["total_bytes"]}]

        resp = admin_client.get("/api/sitemap.xml")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/xml")
        assert "sitemap.xml" in resp.headers["content-disposition"]
        assert f"{BASE}/library/read/{seeded_book}/1" in _locs(resp.content)

    def test_base_query_override(self, admin_client, seeded_book):
        resp = admin_client.get("/api/sitemap.xml?base=https://other.example.com")
        assert resp.status_code == 200
        assert all(loc.startswith("https://other.example.com/") for loc in _locs(resp.content))

    def test_missing_base_url_is_400(self, admin_client, monkeypatch):
        monkeypatch.delenv("SITE_BASE_URL", raising=False)
        monkeypatch.delenv("READER_BASE_URL", raising=False)
        from web.api import sitemap as api_sitemap
        monkeypatch.setattr(api_sitemap, "_config", None)
        assert admin_client.get("/api/sitemap.xml").status_code == 400

    def test_scheme_required(self, admin_client):
        resp = admin_client.get("/api/sitemap.xml?base=reader.example.com")
        assert resp.status_code == 400

    def test_zip_contains_every_file(self, admin_client, seeded_book, monkeypatch):
        monkeypatch.setattr(sm, "MAX_URLS_PER_FILE", 2)
        resp = admin_client.get(f"/api/sitemap.zip?base={BASE}&refresh=true")
        assert resp.status_code == 200
        import io, zipfile
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            names = zf.namelist()
            assert names[0] == "sitemap.xml"
            assert len(names) > 1
            assert all(zf.read(n).startswith(b"<?xml") for n in names)


class TestWriteFiles:
    def test_writes_and_prunes_stale_parts(self, tmp_path):
        out = str(tmp_path / "sitemaps")
        sm.write_files([("sitemap.xml", b"<a/>"), ("sitemap-1.xml", b"<b/>"),
                        ("sitemap-2.xml", b"<c/>")], out)
        assert sorted(os.listdir(out)) == ["sitemap-1.xml", "sitemap-2.xml", "sitemap.xml"]

        # A catalog that shrinks back to one file must not leave the old
        # parts being served (and crawled) forever.
        sm.write_files([("sitemap.xml", b"<z/>")], out)
        assert os.listdir(out) == ["sitemap.xml"]
        assert (tmp_path / "sitemaps" / "sitemap.xml").read_bytes() == b"<z/>"

    def test_leaves_no_partial_files(self, tmp_path):
        out = str(tmp_path / "sitemaps")
        sm.write_files([("sitemap.xml", b"<a/>")], out)
        assert not [n for n in os.listdir(out) if n.endswith(".partial")]

    def test_unrelated_files_untouched(self, tmp_path):
        out = tmp_path / "sitemaps"
        out.mkdir()
        (out / "notes.txt").write_text("keep me")
        sm.write_files([("sitemap.xml", b"<a/>")], str(out))
        assert (out / "notes.txt").read_text() == "keep me"


class TestOutputDir:
    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("SITEMAP_DIR", "/var/www/maps/")
        assert sm.resolve_output_dir() == "/var/www/maps"

    def test_defaults_under_script_dir(self, monkeypatch, tmp_path):
        monkeypatch.delenv("SITEMAP_DIR", raising=False)

        class Cfg:
            script_dir = str(tmp_path) + "/"
        assert sm.resolve_output_dir(Cfg()) == os.path.join(str(tmp_path) + "/", "sitemaps")


class TestPublishApi:
    def test_publish_writes_files_and_reports_them(self, admin_client, seeded_book,
                                                   monkeypatch, tmp_path):
        out = str(tmp_path / "published")
        monkeypatch.setenv("SITEMAP_DIR", out)
        monkeypatch.setenv("SITE_BASE_URL", BASE)

        resp = admin_client.post("/api/sitemap/publish")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "ok"
        assert body["published"]["exists"] is True
        assert body["published"]["url"] == f"{BASE}/sitemap.xml"
        assert os.path.isfile(os.path.join(out, "sitemap.xml"))
        assert f"{BASE}/library/read/{seeded_book}/1" in _locs(
            open(os.path.join(out, "sitemap.xml"), "rb").read())

    def test_status_reports_missing_publication(self, admin_client, seeded_book,
                                                monkeypatch, tmp_path):
        monkeypatch.setenv("SITEMAP_DIR", str(tmp_path / "empty"))
        monkeypatch.setenv("SITE_BASE_URL", BASE)
        body = admin_client.get("/api/sitemap/status?refresh=true").json()
        assert body["published"]["exists"] is False
        assert body["published"]["files"] == []


class TestPublicSitemapFile:
    """The public process serves the generated file and nothing else."""

    @pytest.fixture
    def public_client(self, public_web_app):
        from tests.api_client import SyncASGIClient
        return SyncASGIClient(public_web_app, headers={"Origin": "http://testserver"})

    def test_404_before_anything_is_published(self, sitemap_dir, public_client):
        assert public_client.get("/sitemap.xml").status_code == 404

    def test_serves_published_file(self, sitemap_dir, public_client):
        sm.write_files([("sitemap.xml", b"<urlset/>")], sitemap_dir)
        resp = public_client.get("/sitemap.xml")
        assert resp.status_code == 200
        assert resp.content == b"<urlset/>"
        assert resp.headers["content-type"].startswith("application/xml")

    def test_serves_index_parts(self, sitemap_dir, public_client):
        sm.write_files([("sitemap.xml", b"<sitemapindex/>"), ("sitemap-1.xml", b"<urlset/>")],
                       sitemap_dir)
        assert public_client.get("/sitemap-1.xml").content == b"<urlset/>"
        assert public_client.get("/sitemap-9.xml").status_code == 404

    def test_no_path_traversal(self, sitemap_dir, public_client):
        resp = public_client.get("/sitemap-..%2F..%2Fetc%2Fpasswd.xml")
        assert resp.status_code == 404

    def test_robots_advertises_only_a_published_sitemap(self, sitemap_dir, public_client,
                                                        monkeypatch):
        monkeypatch.setenv("SITE_BASE_URL", BASE)
        assert "Sitemap:" not in public_client.get("/robots.txt").text
        sm.write_files([("sitemap.xml", b"<urlset/>")], sitemap_dir)
        assert f"Sitemap: {BASE}/sitemap.xml" in public_client.get("/robots.txt").text


class TestCanonicalTags:
    """Reader pages name one canonical URL — the /library form the sitemap
    lists — so /read/{id}/{n} and /library/read/{id}/{n} don't compete."""

    @pytest.fixture
    def public_client(self, public_web_app, monkeypatch):
        from tests.api_client import SyncASGIClient
        return SyncASGIClient(public_web_app, headers={"Origin": "http://testserver"})

    @pytest.fixture(autouse=True)
    def base_url(self, monkeypatch):
        monkeypatch.setenv("SITE_BASE_URL", BASE)

    def _canonical(self, html):
        import re
        m = re.search(r'<link rel="canonical" href="([^"]+)"', html)
        return m.group(1) if m else None

    def test_chapter_route(self, public_client, seeded_book):
        html = public_client.get(f"/library/read/{seeded_book}/2").text
        assert self._canonical(html) == f"{BASE}/library/read/{seeded_book}/2"

    def test_legacy_read_route_points_at_the_library_form(self, public_client, seeded_book):
        html = public_client.get(f"/read/{seeded_book}/2").text
        assert self._canonical(html) == f"{BASE}/library/read/{seeded_book}/2"

    def test_book_route(self, public_client, seeded_book):
        html = public_client.get(f"/library/book/{seeded_book}").text
        assert self._canonical(html) == f"{BASE}/library/book/{seeded_book}"

    def test_chapterless_reader_points_at_the_book(self, public_client, seeded_book):
        html = public_client.get(f"/read/{seeded_book}").text
        assert self._canonical(html) == f"{BASE}/library/book/{seeded_book}"

    def test_library_route(self, public_client):
        assert self._canonical(public_client.get("/library").text) == f"{BASE}/library"

    def test_root_canonicalises_to_the_library(self, public_client):
        # The reader root serves the Library in place, so it needs the
        # canonical tag or "/" and "/library" compete as two URLs for one page.
        assert self._canonical(public_client.get("/").text) == f"{BASE}/library"

    def test_rss_autodiscovery_still_spliced(self, public_client, seeded_book):
        html = public_client.get(f"/library/read/{seeded_book}/2").text
        assert f'href="/api/public/books/{seeded_book}/feed.rss?around=2"' in html

    def test_no_base_url_no_tag(self, public_client, seeded_book, monkeypatch):
        monkeypatch.delenv("SITE_BASE_URL", raising=False)
        monkeypatch.delenv("READER_BASE_URL", raising=False)
        html = public_client.get(f"/library/read/{seeded_book}/2").text
        assert self._canonical(html) is None
