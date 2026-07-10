"""markdown_notifications module — render 【…】 system/notification blocks as tables.

Many web novels emit RPG-style "system" notifications wrapped in full-width
brackets, one per paragraph, e.g.::

    【Mission Reward: Immaculate Demon Heart】

This module rewrites each such line into a single-column Markdown table cell so
the Reader / EPUB / HTML output draws it as a boxed callout. A *run* of adjacent
notifications (separated only by blank lines) is merged into one multi-row
table — the first bracket becomes the table header, the rest become body rows::

    | Novice Demon Lord Mission has been issued. |
    | --- |
    | Mission: Seize Another's Fortuitous Opportunity (Incomplete) |
    | …please rob others of their fortuitous opportunities… |
    | Mission Progress: 0/10 |
    | Mission Reward: Immaculate Demon Heart |

ASCII-bracket lines (``[ … ]`` alone on their line) are treated identically to
full-width 【…】 — any line whose sole content is one bracketed span counts as a
notification. Reversal always re-emits full-width 【…】 regardless of which
bracket style the source used.

Forward conversion is idempotent: it consumes the bracket markers it matches, so
a second pass finds nothing to do. Disabling the module reverses the operation —
single-column tables (as produced here) are turned back into double-spaced
【…】 paragraphs.

Auto-off: never enabled by Source URL or default; turn it on per book via the
Modules dialog.

Ordering note: this module is registered AFTER ``chapter_spacing`` so that the
double-spacer runs first. ``chapter_spacing`` inserts a blank line between every
adjacent paragraph, which would split a table's contiguous rows; running this
transform last means the table is assembled after spacing has settled.
"""
import json
import re

from .activity import log_module_activity
from .base import TranslationModule

# A whole-line notification: 【 … 】 or [ … ] alone on its line (surrounding
# whitespace ok). Both bracket styles are treated identically.
# Greedy inner (.*) so nested brackets — e.g. 【Li Yu: 【Video】】 — match through the
# LAST closer, not the first. A char class like [^】]* stops at the first inner 】.
_NOTIF_RE = re.compile(r"^\s*(?:【(.*)】|\[(.*)\])\s*$")
# A Markdown table row: | … |
_ROW_RE = re.compile(r"^\s*\|(.*)\|\s*$")
# A table separator cell: optional colons around one-or-more dashes (e.g. ---).
_SEP_RE = re.compile(r"^:?-+:?$")


def _notif(line):
    """Return the inner text of a whole-line 【…】 / [...] notification, else None."""
    if not isinstance(line, str):
        return None
    m = _NOTIF_RE.match(line)
    if not m:
        return None
    # Exactly one of the two alternation groups matched.
    inner = m.group(1) if m.group(1) is not None else m.group(2)
    # A purely numeric [n] is a footnote marker / danmaku count, not a
    # notification — swallowing it into a table breaks the footnote system.
    if m.group(2) is not None and inner.strip().isdigit():
        return None
    return inner


def _esc_cell(text):
    """Escape a notification's text for use inside a Markdown table cell."""
    return text.strip().replace("|", "\\|")


def _make_table(cells):
    """Build a single-column table: cell[0] is the header, the rest are rows."""
    rows = ["| " + _esc_cell(cells[0]) + " |", "| --- |"]
    rows += ["| " + _esc_cell(c) + " |" for c in cells[1:]]
    return rows


def _to_tables(lines):
    """Collapse runs of 【…】 / [...] notification lines into single-column tables.

    Both bracket styles count as notifications. Notifications separated only by
    blank lines are merged into one table. Idempotent: the bracket markers are
    consumed, so re-running is a no-op.
    """
    if not isinstance(lines, list):
        return lines
    n = len(lines)
    out = []
    i = 0
    changed = False
    while i < n:
        cell = _notif(lines[i])
        if cell is None:
            out.append(lines[i])
            i += 1
            continue
        # Start a group; gather following notifications, hopping over blank lines
        # only when another notification follows before any real content.
        cells = [cell]
        last = i
        j = i + 1
        while j < n:
            nt = _notif(lines[j])
            if nt is not None:
                cells.append(nt)
                last = j
                j += 1
            elif isinstance(lines[j], str) and lines[j].strip() == "":
                k = j
                while k < n and isinstance(lines[k], str) and lines[k].strip() == "":
                    k += 1
                if k < n and _notif(lines[k]) is not None:
                    j = k  # blanks bridge two notifications — keep the group going
                else:
                    break  # blanks lead to content/EOF — group ends at `last`
            else:
                break
        out.extend(_make_table(cells))
        changed = True
        # Resume after the last notification; trailing blanks (if any) are
        # re-emitted normally so the table stays separated from what follows.
        i = last + 1
    return out if changed else lines


def _cell_text(inner):
    """Return the single cell of a one-column row body, or None if multi-column.

    ``inner`` is the text between a row's outer pipes. An unescaped interior
    pipe means more than one column, which is not a table this module produced.
    """
    prev = ""
    for ch in inner:
        if ch == "|" and prev != "\\":
            return None
        prev = ch
    return inner.replace("\\|", "|").strip()


def _from_tables(lines):
    """Reverse :func:`_to_tables`: single-column tables → double-spaced 【…】.

    Only tables carrying this module's exact fingerprint are reverted: every
    row in the `| cell |` spacing _make_table emits, and the separator row
    exactly `| --- |`. A hand-authored or chatgroup-style single-column table
    (compact `|:---|`, unpadded pipes) is left untouched — previously ANY
    single-column pipe table was reversed on disable.
    Idempotent: reverted blocks no longer match a table run.
    """
    if not isinstance(lines, list):
        return lines
    n = len(lines)
    out = []
    i = 0
    changed = False
    while i < n:
        if not (isinstance(lines[i], str) and _ROW_RE.match(lines[i])):
            out.append(lines[i])
            i += 1
            continue
        # Collect a contiguous run of table rows (no blank lines between them).
        run = []
        j = i
        while j < n and isinstance(lines[j], str) and _ROW_RE.match(lines[j]):
            run.append(lines[j])
            j += 1
        inners = [_ROW_RE.match(r).group(1) for r in run]
        sep_ok = len(run) >= 2 and run[1].strip() == "| --- |"
        # Fingerprint: _make_table always writes "| cell |" with the padding
        # spaces; anything else was not produced by this module.
        ours = all(r.strip().startswith("| ") and r.strip().endswith(" |")
                   for idx, r in enumerate(run) if idx != 1)
        # Reconstruct cells from every row except the separator (index 1).
        cells = [_cell_text(s) for idx, s in enumerate(inners) if idx != 1]
        if sep_ok and ours and all(c is not None for c in cells):
            for idx, c in enumerate(cells):
                out.append("【" + c + "】")
                if idx != len(cells) - 1:
                    out.append("")
            changed = True
        else:
            out.extend(run)  # not one of ours — leave as-is
        i = j
    return out if changed else lines


class MarkdownNotificationsModule(TranslationModule):
    id = "markdown_notifications"
    name = "Markdown Notifications"
    description = ("Render 【…】 system/notification blocks as boxed Markdown "
                   "tables, merging a run of adjacent notifications into one "
                   "table. Enabling it converts all existing chapters; disabling "
                   "it reverts them back to 【…】 paragraphs.")
    # Auto-off: no default, no URL patterns — manual per book only.

    def transform_translated_lines(self, content, ctx):
        return _to_tables(content)

    def event_add_to_book(self, ctx):
        """Backfill: convert notifications in every existing chapter."""
        self._rewrite_all(ctx, _to_tables, "converted")

    def event_removed_from_book(self, ctx):
        """Reverse: turn this module's tables back into 【…】 paragraphs."""
        self._rewrite_all(ctx, _from_tables, "reverted")

    def _rewrite_all(self, ctx, fn, verb):
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
        changed = 0
        for cid, raw in rows:
            if not raw:
                continue
            try:
                lines = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(lines, list):
                continue
            new = fn(lines)
            if new != lines:
                cur.execute(
                    "UPDATE chapters SET translated_content = ? WHERE id = ?",
                    (json.dumps(new, ensure_ascii=False), cid))
                changed += 1
        conn.commit()
        conn.close()
        if changed:
            try:
                db.invalidate_epub_cache(book_id)
            except Exception:
                pass
        if logger:
            logger.info(
                f"markdown_notifications: {verb} {changed} chapter(s) for book {book_id}")
        log_module_activity(
            db, "info", f"{self.name}: {verb} {changed} chapter(s)", book_id)
