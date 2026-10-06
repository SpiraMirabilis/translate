"""Books and chapters (read-only)."""

from typing import Annotated, Literal, Optional

from mcp.server.fastmcp.exceptions import ToolError
from pydantic import Field

from ..deps import db as get_db
from ..formatting import json_out, page_footer, paginate, truncate
from ._common import CHAPTERS_DESC, Format, chapter_predicate, require_book, tool


def book_progress(db, book_id: int) -> dict:
    with db._conn(dict_rows=True) as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS n, MAX(chapter_number) AS hi FROM chapters "
                    "WHERE book_id = ?", (book_id,))
        row = cur.fetchone()
        cur.execute("SELECT COUNT(*) AS n FROM queue WHERE book_id = ?", (book_id,))
        queued = cur.fetchone()["n"]
    return {"translated": row["n"], "highest_chapter": row["hi"], "queued": queued}


def content_lines(value) -> list[str]:
    from footnotes import content_to_list
    return content_to_list(value) if value is not None else []


def register(mcp) -> None:

    @tool(mcp, "t9_list_books", "List books", read_only=True)
    def t9_list_books(
        query: Annotated[Optional[str], Field(description="Case-insensitive substring of the title (or an exact id)")] = None,
        limit: Annotated[int, Field(ge=1, le=200)] = 50,
        offset: Annotated[int, Field(ge=0)] = 0,
        format: Format = "text",
    ) -> str:
        """List books: id, title, chapter counts, source language. Use the id with every other tool."""
        books = get_db().list_books()
        if query:
            q = query.strip().lower()
            books = [b for b in books if q in (b.get("title") or "").lower() or q == str(b["id"])]
        page, meta = paginate(books, limit, offset)
        if format == "json":
            keys = ("id", "title", "author", "chapter_count", "published_chapter_count",
                    "total_source_chapters", "status", "is_original", "is_public",
                    "last_chapter_date")
            return json_out({**meta, "books": [{k: b.get(k) for k in keys} for b in page]})
        if not page:
            return "No books match."
        lines = [f"{b['id']:>4}  {b['title']}  — {b.get('chapter_count') or 0} ch"
                 + (f" / {b['total_source_chapters']} src" if b.get("total_source_chapters") else "")
                 + (" [original]" if b.get("is_original") else "")
                 for b in page]
        return "\n".join(lines + [page_footer(meta)])

    @tool(mcp, "t9_get_book", "Get a book", read_only=True)
    def t9_get_book(book_id: int) -> str:
        """One book's metadata, entity categories (and which are gender-tracked), and
        translation progress (chapters translated, highest chapter, queued)."""
        db = get_db()
        book = require_book(db, book_id)
        out = {k: v for k, v in book.items() if k not in ("prompt_template", "cover_image")}
        out["categories"] = db.get_book_categories(book_id)
        out["gendered_categories"] = db.get_book_gendered_categories(book_id)
        out["progress"] = book_progress(db, book_id)
        return json_out(out)

    @tool(mcp, "t9_get_book_notes", "Get a book's notes", read_only=True)
    def t9_get_book_notes(book_id: int) -> str:
        """The BOOK-SPECIFIC NOTES section of the book's frozen prompt — the
        terminology and register decisions every chapter is translated against.
        Read it before judging a rendering."""
        from footnote_scan_core import book_notes
        db = get_db()
        require_book(db, book_id)
        notes = book_notes(db, book_id)
        return truncate(notes) if notes else f"Book {book_id} has no book-specific notes."

    @tool(mcp, "t9_get_chapter", "Read a chapter", read_only=True)
    def t9_get_chapter(
        book_id: int,
        chapter_number: int,
        side: Annotated[Literal["en", "src", "both"], Field(description="en = translation, src = Chinese source, both = interleaved by line index")] = "en",
        line_start: Annotated[int, Field(ge=0, description="First line index (line 0 is the chapter heading)")] = 0,
        line_count: Annotated[int, Field(ge=1, le=2000)] = 400,
    ) -> str:
        """Read a stored chapter with line indices (`[i] text`). Line 0 is the heading.
        Source and translation are separate line arrays; indices do not align
        one-to-one, so `both` prints each side's block in turn."""
        db = get_db()
        ch = db.get_chapter(book_id=book_id, chapter_number=chapter_number)
        if not ch:
            raise ToolError(f"Book {book_id} has no stored chapter {chapter_number}.")
        parts = [f"Book {book_id} ch{chapter_number}: {ch.get('title') or ''} "
                 f"(model {ch.get('model')}, translated {ch.get('translation_date')})"]
        sides = {"en": [("en", "content")], "src": [("src", "untranslated")],
                 "both": [("src", "untranslated"), ("en", "content")]}[side]
        for label, key in sides:
            lines = content_lines(ch.get(key))
            window = lines[line_start:line_start + line_count]
            parts.append(f"\n--- {label} ({len(lines)} lines) ---")
            parts += [f"[{line_start + i}] {t}" for i, t in enumerate(window)]
            if line_start + line_count < len(lines):
                parts.append(f"… {len(lines) - line_start - line_count} more lines; "
                             f"pass line_start={line_start + line_count}")
        return truncate("\n".join(parts), hint="page with line_start/line_count")

    @tool(mcp, "t9_get_chapter_summaries", "Get chapter summaries", read_only=True)
    def t9_get_chapter_summaries(
        book_id: int,
        chapters: Annotated[Optional[str], Field(description=CHAPTERS_DESC)] = None,
        limit: Annotated[int, Field(ge=1, le=500)] = 40,
        offset: Annotated[int, Field(ge=0)] = 0,
        format: Format = "text",
    ) -> str:
        """The plot summary stored with each translated chapter (written by the
        translation model), in chapter order. The cheap way to learn what happens
        in a stretch of the book — who a character is, when an event occurred —
        without reading whole chapters. Queued chapters have no summary yet."""
        from get_chapter_summaries import fetch_summaries
        db = get_db()
        require_book(db, book_id)
        rows = fetch_summaries(db, book_id, chapter_predicate(chapters))
        page, meta = paginate(rows, limit, offset)
        if format == "json":
            return json_out({**meta, "chapters": page})
        if not rows:
            return f"Book {book_id} has no stored chapters matching {chapters!r}." \
                if chapters else f"Book {book_id} has no stored chapters."
        parts = [f"ch{c['chapter']}: {c['title'] or ''}\n{c['summary'] or '(no summary)'}"
                 for c in page]
        return truncate("\n\n".join(parts + [page_footer(meta)]),
                        hint="narrow chapters or lower limit")
