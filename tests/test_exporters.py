"""Tests for web/services/exporters.py — text/markdown/html export of a
2-chapter book against a tmp SQLite DB (conftest `db` fixture). EPUB is
skipped (heavy ebooklib path; covered manually)."""
import pytest

from conftest import FakeLogger

from web.services.exporters import export_book, ExportError, ExportResult


@pytest.fixture
def book(db):
    """A 2-chapter book; chapter 2 contains HTML-escapable characters."""
    book_id = db.create_book(title="My Test Book", author="Some Author")
    assert book_id
    assert db.save_chapter(
        book_id=book_id, chapter_number=1, title="The Beginning",
        untranslated_content=["第一章"],
        translated_content=["First line.", "Second line."],
    )
    assert db.save_chapter(
        book_id=book_id, chapter_number=2, title="Fangs & Claws",
        untranslated_content=["第二章"],
        translated_content=["He said <hello> & waved.", "", "Last line."],
    )
    return db.get_book(book_id=book_id)


class TestTextExport:
    def test_text_join_and_filename(self, db, book):
        result = export_book(db, db.config, FakeLogger(), book, "text")
        assert isinstance(result, ExportResult)
        assert result.filename == "My_Test_Book.txt"
        assert result.media_type == "text/plain; charset=utf-8"
        assert not result.is_path

        text = result.content.decode("utf-8")
        # chapters joined in order with a blank line after each chapter
        assert text == (
            "First line.\nSecond line.\n\n"
            "He said <hello> & waved.\n\nLast line.\n"
        )

    def test_markdown_extension(self, db, book):
        result = export_book(db, db.config, FakeLogger(), book, "markdown")
        assert result.filename == "My_Test_Book.md"
        assert result.media_type == "text/plain; charset=utf-8"


class TestHtmlExport:
    def test_document_structure(self, db, book):
        result = export_book(db, db.config, FakeLogger(), book, "html")
        assert result.filename == "My_Test_Book.html"
        assert result.media_type == "text/html; charset=utf-8"
        html = result.content.decode("utf-8")

        # Title page
        assert "<h1>My Test Book</h1>" in html
        assert '<p class="author">Some Author</p>' in html

        # TOC anchors link to per-chapter headings
        assert '<li><a href="#chapter-1">The Beginning</a></li>' in html
        assert '<li><a href="#chapter-2">Fangs & Claws</a></li>' in html
        assert '<h2 id="chapter-1">The Beginning</h2>' in html
        assert '<h2 id="chapter-2">Fangs & Claws</h2>' in html

        # Body renders through the shared markdown pipeline: adjacent lines
        # group into one <p> with <br> (nl2br), & is escaped, and raw
        # HTML-ish tags like <hello> are stripped by bleach (parity with the
        # EPUB path, which has always rendered this way).
        assert "First line.<br" in html
        assert "Second line.</p>" in html
        assert "He said  &amp; waved." in html
        assert "<p></p>" not in html

        # Chapter order: ch1 heading precedes ch2 heading
        assert html.index('id="chapter-1"') < html.index('id="chapter-2"')

    def test_untitled_chapter_falls_back_to_number(self, db):
        book_id = db.create_book(title="NoTitles")
        db.save_chapter(book_id=book_id, chapter_number=7, title="",
                        untranslated_content=["七"], translated_content=["x"])
        book = db.get_book(book_id=book_id)
        html = export_book(db, db.config, FakeLogger(), book, "html").content.decode("utf-8")
        assert '<h2 id="chapter-7">Chapter 7</h2>' in html


class TestExportErrors:
    def test_no_chapters_raises_404(self, db):
        book_id = db.create_book(title="Empty Book")
        book = db.get_book(book_id=book_id)
        with pytest.raises(ExportError) as exc:
            export_book(db, db.config, FakeLogger(), book, "text")
        assert exc.value.status_code == 404
