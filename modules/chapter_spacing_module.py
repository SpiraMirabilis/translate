"""chapter_spacing module — keep chapters uniformly double-spaced.

Wraps the pure helpers from ``fix_chapter_spacing.py`` (``needs_fix`` /
``double_space``): one blank line between every paragraph, for both the source
and translated text. ``double_space`` is idempotent (an already-spaced chapter
is left unchanged).

Forward-going content is fixed via the source/translated transform hooks. When
the module is *enabled* for a book, ``event_add_to_book`` runs once over all of
that book's existing chapters to retroactively fix spacing.

On by default for every book; disable per book via the Modules dialog.
"""
import json

from .base import TranslationModule


def _respace(content):
    if not isinstance(content, list):
        return content
    from fix_chapter_spacing import needs_fix, double_space
    return double_space(content) if needs_fix(content) else content


class ChapterSpacingModule(TranslationModule):
    id = "chapter_spacing"
    name = "Chapter Spacing"
    description = ("Keep chapters double-spaced (one blank line between every "
                   "paragraph), for both source and translated text. Enabling it "
                   "re-spaces all existing chapters of the book once.")
    default_enabled = True

    def transform_source_lines(self, content, ctx):
        return _respace(content)

    def transform_translated_lines(self, content, ctx):
        return _respace(content)

    def event_add_to_book(self, ctx):
        """Backfill: re-space every existing chapter (source + translated) once."""
        db = ctx.get("db")
        book = ctx.get("book")
        logger = ctx.get("logger")
        if db is None or not book:
            return
        book_id = book.get("id") if hasattr(book, "get") else None
        if not book_id:
            return

        from fix_chapter_spacing import needs_fix, double_space
        conn = db.backend.get_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT id, translated_content, untranslated_content "
            "FROM chapters WHERE book_id = ?", (book_id,))
        rows = cur.fetchall()
        fixed = 0
        for cid, translated_raw, source_raw in rows:
            updates = {}
            for col, raw in (("translated_content", translated_raw),
                             ("untranslated_content", source_raw)):
                if not raw:
                    continue
                try:
                    lines = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    continue
                if isinstance(lines, list) and needs_fix(lines):
                    updates[col] = json.dumps(double_space(lines), ensure_ascii=False)
            if updates:
                set_clause = ", ".join(f"{c} = ?" for c in updates)
                cur.execute(f"UPDATE chapters SET {set_clause} WHERE id = ?",
                            (*updates.values(), cid))
                fixed += 1
        conn.commit()
        conn.close()
        if fixed:
            try:
                db.invalidate_epub_cache(book_id)
            except Exception:
                pass
        if logger:
            logger.info(f"chapter_spacing: re-spaced {fixed} chapter(s) for book {book_id}")
