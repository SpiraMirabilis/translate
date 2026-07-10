"""Book export business logic, extracted from web/api/books.py (B4).

`export_book` builds a full-book export (epub / html / text / markdown)
and returns an :class:`ExportResult`; the route handler only wraps that
in a StreamingResponse/FileResponse. The public reader EPUB endpoint
(web/api/public.py) has its own flow and does NOT use this module.
"""
import os
from dataclasses import dataclass
from typing import Optional

from output_formatter import render_lines_html, _MD_BLOCK_CSS


@dataclass
class ExportResult:
    """A finished export: either in-memory bytes or a file on disk."""
    filename: str
    media_type: str
    content: Optional[bytes] = None   # set when is_path is False
    path: Optional[str] = None        # set when is_path is True
    is_path: bool = False


class ExportError(Exception):
    """Export failure with an HTTP-ish status code for the route handler."""

    def __init__(self, message, status_code=500):
        super().__init__(message)
        self.status_code = status_code


def export_book(db, config, logger, book, format) -> ExportResult:
    """Export every chapter of ``book`` in the requested format.

    Args:
        db: DatabaseManager
        config: TranslationConfig (script_dir, spaces settings)
        logger: app logger (passed to OutputFormatter for EPUB)
        book: book dict as returned by db.get_book() (must include "id")
        format: "text" | "markdown" | "html" | "epub"

    Raises ExportError (status_code 404 when there is nothing to export,
    500 when generation fails).
    """
    book_id = book["id"]

    chapters = db.list_chapters(book_id)
    if not chapters:
        raise ExportError("No chapters to export.", status_code=404)

    book_info = {
        "id": book_id,
        "title": book.get("title", "Unknown"),
        "author": book.get("author") or "Translator",
        "language": book.get("language") or "en",
    }
    # Include cover image path for EPUB export
    if book.get("cover_image"):
        cover_full = os.path.join(config.script_dir, book["cover_image"])
        if os.path.exists(cover_full):
            book_info["cover_image"] = cover_full

    if format == "epub":
        return _export_epub(db, config, logger, book, book_info)
    if format == "azw3":
        return _export_azw3(db, config, logger, book, book_info)
    if format == "html":
        return _export_html(db, book, book_info)
    return _export_text(db, book, format)


def _export_azw3(db, config, logger, book, book_info) -> ExportResult:
    """Generate (or reuse) the AZW3, converting from the cached EPUB via Calibre."""
    import os
    import azw3

    if not azw3.is_available():
        raise ExportError("AZW3 conversion is not available (ebook-convert not installed).",
                          status_code=503)

    book_id = book_info["id"]
    # Reuse the EPUB export to build/cache the source EPUB.
    epub_result = _export_epub(db, config, logger, book, book_info)
    epub_path = epub_result.path

    import ebook_build
    cache_dir = db._epub_cache_dir()
    # "-full" namespace: this artifact contains ALL chapters (drafts included)
    # and must never share a path — or a CDN key — with the published-only
    # files the public endpoints serve.
    azw3_path = os.path.join(cache_dir, f"{book_id}-full.azw3")

    with ebook_build.book_lock(cache_dir, f"{book_id}-full-azw3"):
        needs_build = (not os.path.exists(azw3_path)
                       or os.path.getmtime(azw3_path) < os.path.getmtime(epub_path))
        if needs_build:
            if not azw3.convert_epub_to_azw3(epub_path, azw3_path, logger):
                raise ExportError("Failed to generate AZW3.", status_code=500)

    filename = f"{book['title'].replace(' ', '_')}.azw3"
    return ExportResult(
        filename=filename,
        media_type="application/x-mobi8-ebook",
        path=azw3_path,
        is_path=True,
    )


def _export_epub(db, config, logger, book, book_info) -> ExportResult:
    """Generate (or reuse) the cached EPUB, mirroring it to Spaces/CDN."""
    from output_formatter import OutputFormatter

    import ebook_build

    book_id = book_info["id"]
    cache_dir = db._epub_cache_dir()
    # "-full" namespace: the admin export includes EVERY chapter, drafts and
    # scheduled ones too. It previously shared epub_cache/{id}.epub with the
    # published-only public endpoint (and even uploaded to the public CDN
    # key), which leaked unpublished content to readers. Keep it local-only
    # under its own name; prewarm owns populating the public CDN.
    cached_path = os.path.join(cache_dir, f"{book_id}-full.epub")
    filename = f"{book['title'].replace(' ', '_')}.epub"
    version_basis = book.get("modified_date")

    with ebook_build.book_lock(cache_dir, f"{book_id}-full"):
        if not ebook_build.is_current(cached_path, version_basis):
            all_chapters = [
                {
                    "chapter": ch["chapter"],
                    "title": ch.get("title") or f"Chapter {ch['chapter']}",
                    "content": ch.get("content", []),
                }
                for ch in db.get_chapters_bulk(book_id)
            ]

            formatter = OutputFormatter(config, logger)
            output_path = formatter.save_book_as_epub(all_chapters, book_info,
                                                      output_path=cached_path)
            if not output_path or not os.path.exists(cached_path):
                raise ExportError("Failed to generate EPUB.", status_code=500)
            ebook_build.write_stamp(cached_path, version_basis)

    return ExportResult(
        filename=filename,
        media_type="application/epub+zip",
        path=cached_path,
        is_path=True,
    )


def _export_html(db, book, book_info) -> ExportResult:
    """Standalone HTML document with a linked table of contents."""
    book_id = book_info["id"]
    ch_list = [
        {
            "number": ch["chapter"],
            "title": ch.get("title") or f"Chapter {ch['chapter']}",
            "content": ch.get("content", []),
        }
        for ch in db.get_chapters_bulk(book_id)
    ]

    html_parts = [
        '<!DOCTYPE html>',
        '<html lang="en">',
        '<head>',
        f'<meta charset="utf-8"><title>{book_info["title"]}</title>',
        '<style>',
        'body { font-family: Georgia, serif; max-width: 42em; margin: 2em auto; padding: 0 1em; line-height: 1.7; color: #222; }',
        'h1 { text-align: center; margin: 1.5em 0 0.5em; }',
        'h2 { margin: 2em 0 0.5em; border-bottom: 1px solid #ccc; padding-bottom: 0.3em; }',
        'p { text-indent: 1.5em; margin: 0.4em 0; }',
        _MD_BLOCK_CSS,
        '.title-page { text-align: center; margin: 4em 0; }',
        '.title-page .author { font-size: 1.1em; color: #555; }',
        'nav { margin: 2em 0; }',
        'nav h2 { border-bottom: none; }',
        'nav ol { padding-left: 1.5em; }',
        'nav li { margin: 0.3em 0; }',
        'nav a { color: #2563eb; text-decoration: none; }',
        'nav a:hover { text-decoration: underline; }',
        '</style>',
        '</head><body>',
        '<div class="title-page">',
        f'<h1>{book_info["title"]}</h1>',
        f'<p class="author">{book_info["author"]}</p>',
        '</div>',
        '<nav><h2>Table of Contents</h2><ol>',
    ]
    for ch_data in ch_list:
        anchor = f"chapter-{ch_data['number']}"
        html_parts.append(f'<li><a href="#{anchor}">{ch_data["title"]}</a></li>')
    html_parts.append('</ol></nav>')

    for ch_data in ch_list:
        anchor = f"chapter-{ch_data['number']}"
        html_parts.append(f'<h2 id="{anchor}">{ch_data["title"]}</h2>')
        html_parts.append(render_lines_html(ch_data["content"]))

    html_parts.append('</body></html>')
    filename = f"{book['title'].replace(' ', '_')}.html"
    return ExportResult(
        filename=filename,
        media_type="text/html; charset=utf-8",
        content="\n".join(html_parts).encode("utf-8"),
    )


def _export_text(db, book, format) -> ExportResult:
    """Plain text / markdown: chapter contents joined with blank lines."""
    book_id = book["id"]
    all_lines = []
    for ch in db.get_chapters_bulk(book_id):
        all_lines.extend(ch.get("content", []))
        all_lines.append("")

    ext_map = {"text": "txt", "markdown": "md"}
    ext = ext_map.get(format, "txt")
    filename = f"{book['title'].replace(' ', '_')}.{ext}"
    return ExportResult(
        filename=filename,
        media_type="text/plain; charset=utf-8",
        content="\n".join(all_lines).encode("utf-8"),
    )
