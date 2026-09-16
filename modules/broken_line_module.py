"""broken_line module — rejoin translated paragraphs split mid-sentence.

Some raws (and some model outputs) break a single sentence across two
paragraphs. The tell is a paragraph that ends on a **comma**: prose never ends a
paragraph with a comma, so such a line is a continuation that got a stray
paragraph break jammed into it. For example::

    After hesitating and dithering for quite a while,

    Lu Xiu ultimately let out a sigh, lowered her hand, picked up the plate,
    and walked into the kitchen.

should be a single paragraph. This module joins the comma-ended line to the
following paragraph (skipping any blank line between them, so it is safe to run
either side of the double-spacer).

Only the **translated** text is touched — the Chinese source is left alone. The
transform is idempotent: once joined, the merged paragraph no longer ends on a
comma, so a re-run is a no-op. Chained breaks (a continuation that *also* ends
on a comma) collapse in one left-to-right pass.

Forward-going content is fixed via the translated transform hook. Enabling the
module for a book also runs once over every existing chapter's **translated**
text (``event_add_to_book``) — the intended way to sweep an already-translated
book is to toggle the module off and back on. The join is deliberately
**one-way**: there is no meaningful inverse (we would have to guess where to
re-insert paragraph breaks), so ``event_removed_from_book`` is a no-op and
disabling the module simply stops future joins.

On by default for every book; disable per book via the Modules dialog.
"""
import json

from .base import TranslationModule
from .activity import log_module_activity

# Trailing punctuation that marks a paragraph as an unfinished, broken line.
# A comma is the definitive tell (half-width for English prose, full-width in
# case a CJK comma leaks through). Anything else legitimately ends a paragraph.
_BROKEN_ENDINGS = (",", "，")


def _ends_broken(text):
    return isinstance(text, str) and text.rstrip().endswith(_BROKEN_ENDINGS)


def _is_structural(text):
    """Whether a line is a block-level construct we must never merge prose into.

    Headings, tables, blockquotes, list items, code fences and the ⟦…⟧ sentinel
    markers (tables/illustrations/footnotes) are their own blocks; a comma-ended
    sentence must not swallow one, even in the unlikely case one follows.
    """
    if not isinstance(text, str):
        return True
    s = text.lstrip()
    if not s:
        return False
    if s[0] in "#>|⟦`":  # heading / table row / blockquote / sentinel / code fence
        return True
    if len(s) >= 2 and s[0] in "-*+" and s[1] == " ":  # unordered list item
        return True
    return False


def _join_broken_lines(content):
    if not isinstance(content, list):
        return content

    n = len(content)
    result = []
    consumed = set()  # indices already merged into an earlier paragraph
    changed = False
    i = 0
    while i < n:
        if i in consumed:
            i += 1
            continue
        current = content[i]
        # Grow `current` while it ends on a comma and a real continuation
        # paragraph follows. Blank lines between are skipped and consumed. `j`
        # advances past each continuation so the search never re-reads it.
        j = i
        while _ends_broken(current) and not _is_structural(current):
            k = j + 1
            while k < n and isinstance(content[k], str) and content[k].strip() == "":
                k += 1
            if k >= n:
                break
            nxt = content[k]
            if not isinstance(nxt, str) or nxt.strip() == "" or _is_structural(nxt):
                break
            current = current.rstrip() + " " + nxt.strip()
            # Mark the skipped blanks and the continuation as consumed.
            for m in range(j + 1, k + 1):
                consumed.add(m)
            changed = True
            j = k
        result.append(current)
        i += 1

    return result if changed else content


class BrokenLineModule(TranslationModule):
    id = "broken_line"
    name = "Broken Lines"
    description = ("Rejoin translated paragraphs that were split mid-sentence — "
                  "a paragraph ending on a comma is glued to the paragraph that "
                  "follows it. Translated text only; the source is untouched.")
    default_enabled = True

    def transform_translated_lines(self, content, ctx):
        return _join_broken_lines(content)

    def event_add_to_book(self, ctx):
        """Backfill: rejoin broken lines in every existing chapter's translation.

        Translated text only — the Chinese source is never touched. Intended for
        sweeping an already-translated book: toggle the module off then on.
        """
        db = ctx.get("db")
        book = ctx.get("book")
        logger = ctx.get("logger")
        if db is None or not book:
            return
        book_id = book.get("id") if hasattr(book, "get") else None
        if not book_id:
            return

        conn = db.backend.get_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT id, translated_content FROM chapters WHERE book_id = ?",
            (book_id,))
        rows = cur.fetchall()
        fixed = 0
        for cid, translated_raw in rows:
            if not translated_raw:
                continue
            try:
                lines = json.loads(translated_raw)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(lines, list):
                continue
            joined = _join_broken_lines(lines)
            if joined is lines:
                continue  # no broken lines in this chapter
            cur.execute(
                "UPDATE chapters SET translated_content = ? WHERE id = ?",
                (json.dumps(joined, ensure_ascii=False), cid))
            fixed += 1
        conn.commit()
        conn.close()
        if fixed:
            try:
                db.invalidate_epub_cache(book_id)
            except Exception:
                pass
        if logger:
            logger.info(f"broken_line: rejoined broken lines in {fixed} "
                        f"chapter(s) for book {book_id}")
        # One summary line per backfill (this runs as a single background task
        # per toggle), so it surfaces in the visible activity log like the
        # other backfill modules — no debouncing needed.
        log_module_activity(
            db, "info",
            f"{self.name}: rejoined broken lines in {fixed} chapter(s)", book_id)
