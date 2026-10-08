"""The reader routes carry their content in the served HTML (web/seo_html.py).

Google's first-pass indexer reads the raw HTML. Before this, every one of the
~45k reader URLs served the same empty shell titled "Translator", and Search
Console filed 38k of them under "Discovered - currently not indexed". These
tests lock in: real <title> + description + prose on chapter pages, chapter
links on book pages, book links on the library, and real 404s for a private
book / unpublished chapter instead of a 200 shell (a soft 404).
"""
import os

import pytest

from web import seo_html

_dist = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "web", "frontend", "dist")
needs_dist = pytest.mark.skipif(not os.path.isdir(_dist), reason="frontend dist not built")


# ---------------------------------------------------------------- pure functions

BOOK = {"id": 7, "title": "Mirror & Sword", "author": "Zhang <San>",
        "description": "A long tale.\n\nSecond paragraph."}
CHAPTERS = [{"chapter": 1, "title": "Start"}, {"chapter": 2, "title": None}, {"chapter": 3, "title": "End"}]


def test_chapter_page_has_title_description_prose_and_nav():
    ch = {"chapter": 2, "title": None,
          "content": ["Chapter 2", "", "He walked *quickly* into the hall.", "", "Then he stopped."]}
    page = seo_html.chapter_page(BOOK, ch, CHAPTERS, "Boonnovels")
    assert page["title"] == "Chapter 2 — Mirror & Sword — Boonnovels"
    assert page["description"].startswith("He walked quickly into the hall.")
    html = page["html"]
    assert '<div id="ssr">' in html
    assert "<h1>Chapter 2</h1>" in html
    assert "<em>quickly</em>" in html
    assert 'rel="prev" href="/library/read/7/1"' in html
    assert 'rel="next" href="/library/read/7/3"' in html
    assert 'href="/library/book/7"' in html
    assert "Mirror &amp; Sword" in html and "Zhang &lt;San&gt;" in html


def test_chapter_page_edges_have_no_dangling_nav_links():
    first = seo_html.chapter_page(BOOK, {"chapter": 1, "title": "Start", "content": ["x"]}, CHAPTERS, "S")
    assert 'rel="prev"' not in first["html"] and 'rel="next" href="/library/read/7/2"' in first["html"]
    last = seo_html.chapter_page(BOOK, {"chapter": 3, "title": "End", "content": ["x"]}, CHAPTERS, "S")
    assert 'rel="next"' not in last["html"] and 'rel="prev" href="/library/read/7/2"' in last["html"]
    assert last["title"].startswith("Chapter 3: End — ")


def test_description_truncates_on_a_word_boundary():
    words = " ".join(["word"] * 100)
    page = seo_html.chapter_page(BOOK, {"chapter": 1, "content": [words]}, CHAPTERS, "S")
    assert len(page["description"]) <= seo_html.DESCRIPTION_MAX
    assert page["description"].endswith("word…")


def test_illustration_markers_resolve_or_vanish():
    ch = {"chapter": 1, "content": ["⟦IMG:abcd1234⟧", "⟦IMG:ffff0000⟧", "Text."]}
    page = seo_html.chapter_page(BOOK, ch, CHAPTERS, "S", {"abcd1234": "https://cdn/x.jpg"})
    assert '<img src="https://cdn/x.jpg"' in page["html"]
    assert "⟦IMG" not in page["html"]


def test_book_page_lists_every_chapter():
    page = seo_html.book_page(BOOK, CHAPTERS, "Boonnovels")
    assert page["title"] == "Mirror & Sword — Boonnovels"
    assert page["description"] == "A long tale. Second paragraph."
    html = page["html"]
    assert "<p>A long tale.</p><p>Second paragraph.</p>" in html
    for n in (1, 2, 3):
        assert f'href="/library/read/7/{n}"' in html
    assert "Chapter 1: Start" in html and "<li><a href=\"/library/read/7/2\">Chapter 2</a></li>" in html


def test_library_page_links_books():
    page = seo_html.library_page([BOOK | {"published_chapter_count": 3}], "Boonnovels")
    assert page["title"] == "Boonnovels"
    assert 'href="/library/book/7"' in page["html"] and "3 chapters" in page["html"]


def test_splice_replaces_title_and_adds_block_after_root():
    shell = '<html><head><title>Translator</title></head><body><div id="root"></div><script></script></body></html>'
    out = seo_html.splice(shell, {"title": "A <b>", "description": 'say "hi"', "html": '<div id="ssr">X</div>'})
    assert "<title>A &lt;b&gt;</title>" in out and "Translator" not in out
    assert '<meta name="description" content="say &quot;hi&quot;" />' in out
    assert '<div id="root"></div><div id="ssr">X</div>' in out


# ---------------------------------------------------------------- HTTP, public app

@pytest.fixture
def pub_client(public_web_app):
    from tests.api_client import SyncASGIClient
    return SyncASGIClient(public_web_app, headers={"Origin": "http://testserver"})


@pytest.fixture
def seo_book(db):
    book_id = db.create_book("Seo Book", author="Author", description="About the book.")
    for n in (1, 2):
        db.save_chapter(book_id=book_id, chapter_number=n,
                        untranslated_content=["第1章", "原文"],
                        translated_content=[f"Chapter {n}", f"Line of chapter {n}."],
                        title=f"Title {n}")
    db.save_chapter(book_id=book_id, chapter_number=3,
                    untranslated_content=["第3章", "原文"],
                    translated_content=["Chapter 3", "Draft line."], title="Draft",
                    publish=False)
    return book_id


@needs_dist
class TestServedHtml:
    def test_chapter_route_carries_prose(self, pub_client, seo_book):
        for path in (f"/library/read/{seo_book}/2", f"/read/{seo_book}/2"):
            resp = pub_client.get(path)
            assert resp.status_code == 200, path
            assert "<title>Chapter 2: Title 2 — Seo Book — " in resp.text
            assert "Line of chapter 2." in resp.text
            assert f'rel="prev" href="/library/read/{seo_book}/1"' in resp.text
            assert 'rel="next"' not in resp.text  # ch3 is a draft
            assert '<meta name="description"' in resp.text

    def test_book_route_lists_published_chapters_only(self, pub_client, seo_book):
        resp = pub_client.get(f"/library/book/{seo_book}")
        assert resp.status_code == 200
        assert "<title>Seo Book — " in resp.text
        assert f'href="/library/read/{seo_book}/2"' in resp.text
        assert f'href="/library/read/{seo_book}/3"' not in resp.text
        assert "Draft line." not in resp.text

    def test_library_route_links_the_book(self, pub_client, seo_book):
        for path in ("/", "/library"):
            resp = pub_client.get(path)
            assert resp.status_code == 200
            assert f'href="/library/book/{seo_book}"' in resp.text

    def test_missing_book_and_chapter_are_real_404s(self, pub_client, seo_book):
        assert pub_client.get("/library/book/999999").status_code == 404
        assert pub_client.get("/library/read/999999/1").status_code == 404
        assert pub_client.get(f"/library/read/{seo_book}/999").status_code == 404
        # an unpublished chapter is not acknowledged either
        assert pub_client.get(f"/library/read/{seo_book}/3").status_code == 404

    def test_private_book_is_404(self, pub_client, db, seo_book):
        db.update_book(seo_book, is_public=False)
        assert pub_client.get(f"/library/book/{seo_book}").status_code == 404
        assert pub_client.get(f"/library/read/{seo_book}/1").status_code == 404
