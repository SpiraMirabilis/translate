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
    if format == "html":
        return _export_html(db, book, book_info)
    return _export_text(db, book, format)


def _export_epub(db, config, logger, book, book_info) -> ExportResult:
    """Generate (or reuse) the cached EPUB, mirroring it to Spaces/CDN."""
    from output_formatter import OutputFormatter

    book_id = book_info["id"]
    cache_dir = db._epub_cache_dir()
    cached_path = os.path.join(cache_dir, f"{book_id}.epub")
    filename = f"{book['title'].replace(' ', '_')}.epub"

    if not os.path.exists(cached_path):
        all_chapters = [
            {
                "chapter": ch["chapter"],
                "title": ch.get("title") or f"Chapter {ch['chapter']}",
                "content": ch.get("content", []),
            }
            for ch in db.get_chapters_bulk(book_id)
        ]

        formatter = OutputFormatter(config, logger)
        output_path = formatter.save_book_as_epub(all_chapters, book_info)
        if not output_path or not os.path.exists(output_path):
            raise ExportError("Failed to generate EPUB.", status_code=500)

        # Cache the generated EPUB
        os.makedirs(cache_dir, exist_ok=True)
        import shutil
        shutil.copy2(output_path, cached_path)

        # Populate the CDN copy so the public download endpoint can redirect.
        try:
            import spaces
            if spaces.is_enabled(config):
                ver = spaces.epub_version(book_id, book.get("modified_date"))
                key = spaces.epub_key(config, book_id, ver)
                if spaces.upload(config, cached_path, key, "application/epub+zip"):
                    spaces.prune_epub_versions(config, book_id, keep_key=key)
        except Exception:
            pass

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
