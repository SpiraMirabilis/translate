#!/usr/bin/env python3
"""Normalize a book's system-notification lines in place: split lines that pack
several notifications, and fold dangling speaker labels into their brackets.

Two transforms, both from the markdown_notifications module (so stored text and
newly translated chapters end up in exactly the same shape):

  **split** — one notification per line. Raws sometimes pack a boot sequence
  two-to-a-paragraph::

      [System status: abnormal startup] [Detecting runtime environment...]

  which the module would otherwise render as a single table cell whose text
  still carries the interior ``] [``. Only lines that are *nothing but* a
  whitespace-separated run of 2+ balanced bracket spans are touched — prose
  mentioning two bracketed terms is safe, as are nested spans
  (``【Li Yu: 【Video】】`` is one) and numeric ``[1]`` footnote markers.

  **fold** — a speaker label belongs inside the box it introduces::

      Molly: 【Because you were tested twice…】   →   【Molly: Because you were tested twice…】
      茉莉：【因為你考了兩次……】                  →   【茉莉：因為你考了兩次……】

  The label must exactly match one of the book's person-like entities (any
  entity carrying a gender, globals included). That gate is what separates a
  label from the tail of a narrative clause: ``看完之後，茉莉說：【去吃飯吧。】``
  and ``Molly popped up again, angling for attention: 【…】`` have the same shape
  but keep their narration outside the box. The colon and the spacing after it
  are preserved, so English ``: `` and Chinese ``：`` each keep their convention.

Both run on the stored source, the translated text, and queued (not-yet-
translated) source. Both preserve trailing Markdown hard breaks and are
idempotent, and neither touches table rows the module already wrote — so this is
safe to run whether or not markdown_notifications is enabled for the book.

Default is a DRY RUN. Pass --apply to write.

Usage:
    python3 normalize_notification_lines.py --book-id 83                    # dry run, everything
    python3 normalize_notification_lines.py --book-id 83 --apply
    python3 normalize_notification_lines.py --book-id 83 -t fold --apply    # one transform
    python3 normalize_notification_lines.py --book-id 83 -f translated --apply
"""

import argparse
import json
import re
import sys

from modules.entity_names import load_person_names
from modules.markdown_notifications_module import (
    _fold_speaker_lines,
    _split_notif_lines,
)

# --- unquote -----------------------------------------------------------------
# Quote styles that can wrap a whole-line notification. The opening quote must
# be followed immediately by an opening bracket -- that adjacency is what
# separates a system notification from ordinary dialogue, which opens with prose.
_QUOTE_PAIRS = (('"', '"'), ("\u201c", "\u201d"))
# A leading rank tag inside a notification: the "\u3010\u94f6\u3011" / "\u3010Silver\u3011" that some raws put
# where others write "\uff08\u94f6\uff09" / "(Silver)". Group 1 is the tag, group 2 the remainder.
_RANK_TAG_RE = re.compile(r"^\u3010([^\u3010\u3011]{1,12})\u3011\s*(\S.*)$", re.S)


def _has_cjk(text):
    return any(ord(ch) >= 0x2E80 for ch in text)


def _balanced(text):
    """True if every bracket style in ``text`` is balanced and never negative."""
    for opener, closer in (("\u3010", "\u3011"), ("[", "]")):
        depth = 0
        for ch in text:
            if ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth < 0:
                    return False
        if depth:
            return False
    return True


def _normalize_rank_tag(inner):
    """Rewrite a leading \u3010tag\u3011 into the parenthesised house style.

    ``\u3010\u94f6\u3011\u94f6\u8272\u6c64\u5319\uff1a\u2026``  -> ``\uff08\u94f6\uff09\u94f6\u8272\u6c64\u5319\uff1a\u2026``   (CJK: full-width parens, no space)
    ``\u3010Silver\u3011Silver Spoon: \u2026`` -> ``(Silver) Silver Spoon: \u2026``  (ASCII parens + one space)

    Without this the unwrapped line reads ``\u3010\u3010Silver\u3011Silver Spoon: \u2026\u3011`` -- correct, but
    visually doubled and out of step with the \u3010\uff08\u94f6\uff09\u2026\u3011 blocks the same book uses
    elsewhere. Anything that is not a short leading tag is left alone.
    """
    m = _RANK_TAG_RE.match(inner)
    if not m:
        return inner
    tag, rest = m.group(1).strip(), m.group(2)
    if not tag or not _balanced(tag):
        return inner
    if _has_cjk(tag):
        return "\uff08" + tag + "\uff09" + rest
    return "(" + tag + ") " + rest


def _unquote_notif_line(line):
    """Turn a quoted whole-line notification into a bracketed one.

    ``\u201c\u3010\u94f6\u3011\u94f6\u8272\u6c64\u5319\uff1a\u7acb\u523b\u83b7\u5f97\u4f53\u4fee\u4e00\u91cd\u4fee\u4e3a\u3002\u201d`` -> ``\u3010\uff08\u94f6\uff09\u94f6\u8272\u6c64\u5319\uff1a\u7acb\u523b\u83b7\u5f97\u4f53\u4fee\u4e00\u91cd\u4fee\u4e3a\u3002\u3011``

    Some raws present a run of system-notification lines with the middle ones
    wrapped in quotation marks, which hides them from the module: a quoted line
    is not a whole-line notification, so it never becomes a table row and the
    run is split in three. Requiring the opening quote to be followed straight
    away by a bracket, and the interior to be bracket-balanced, keeps ordinary
    dialogue ("\u2026he said") and prose that merely mentions a bracketed term out.

    Trailing whitespace is preserved so a Markdown hard break survives.
    Idempotent: the result no longer starts with a quote.
    """
    if not isinstance(line, str):
        return line
    body = line.rstrip()
    tail = line[len(body):]
    stripped = body.lstrip()
    indent = body[:len(body) - len(stripped)]
    for opener, closer in _QUOTE_PAIRS:
        if not stripped.startswith(opener) or len(stripped) < 3:
            continue
        rest = stripped[len(opener):]
        if not rest.startswith(("\u3010", "[")) or not rest.endswith(closer):
            continue
        inner = rest[:-len(closer)].strip()
        if not inner or not _balanced(inner):
            continue
        # A purely numeric ASCII span is a footnote marker, not a notification.
        if inner.startswith("[") and inner[1:-1].strip().isdigit():
            continue
        inner = _normalize_rank_tag(inner)
        # Already one whole bracket span? Then the quotes were the only wrapper.
        if inner.startswith("\u3010") and inner.endswith("\u3011") and _balanced(inner[1:-1]):
            return indent + inner + tail
        return indent + "\u3010" + inner + "\u3011" + tail
    return line


def _unquote_notif_lines(lines):
    """Apply :func:`_unquote_notif_line` to a line array; identity on no-op."""
    if not isinstance(lines, list):
        return lines
    out = [_unquote_notif_line(l) for l in lines]
    return out if out != lines else lines


# label -> (table, id column, content column, order-by column)
TARGETS = {
    "source": ("chapters", "id", "untranslated_content", "chapter_number"),
    "translated": ("chapters", "id", "translated_content", "chapter_number"),
    "queue": ("queue", "id", "content", "chapter_number"),
}

TRANSFORMS = ("split", "fold", "unquote")


def _decode(content):
    """Return (lines_list, was_json_string), or (None, False) if undecodable."""
    if isinstance(content, str):
        try:
            decoded = json.loads(content)
            if isinstance(decoded, list):
                return decoded, True
        except (json.JSONDecodeError, TypeError):
            pass
        return content.split("\n"), False
    if isinstance(content, list):
        return content, False
    return None, False


def _encode(lines, was_json_string):
    if was_json_string:
        return json.dumps(lines, ensure_ascii=False)
    return "\n".join(lines)


def process(label, book_id, transforms, speakers, conn, cursor, dry_run):
    """Run the chosen transforms over one (table, column) target."""
    table, id_col, content_col, order_col = TARGETS[label]
    cursor.execute(
        f"SELECT {id_col}, {order_col}, {content_col} FROM {table} "
        f"WHERE book_id = ? ORDER BY {order_col} ASC",
        (book_id,),
    )
    rows = cursor.fetchall()
    print(f"\n{label}: {len(rows)} row(s) in {table}.{content_col}")
    items = added = 0
    for row_id, chapter_number, content in rows:
        if not content:
            continue
        lines, was_json = _decode(content)
        if lines is None:
            continue
        new_lines = lines
        if "unquote" in transforms:
            new_lines = _unquote_notif_lines(new_lines)
        if "fold" in transforms:
            new_lines = _fold_speaker_lines(new_lines, speakers)
        if "split" in transforms:
            new_lines = _split_notif_lines(new_lines)
        if new_lines == lines:
            continue
        items += 1
        added += len(new_lines) - len(lines)
        for old in lines:
            if old not in new_lines:
                print(f"  ch {chapter_number}: {old.strip()[:120]}")
        if not dry_run:
            cursor.execute(
                f"UPDATE {table} SET {content_col} = ? WHERE {id_col} = ?",
                (_encode(new_lines, was_json), row_id),
            )
    print(f"  → {items} row(s) changed, +{added} line(s)")
    return items, added


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--book-id", "-b", type=int, required=True)
    p.add_argument("--apply", action="store_true",
                   help="Write changes (default is a dry run).")
    p.add_argument("--field", "-f", choices=sorted(TARGETS), action="append",
                   help="Limit to one field (repeatable). Default: all three.")
    p.add_argument("--transform", "-t", choices=TRANSFORMS, action="append",
                   help="Limit to one transform (repeatable). Default: both.")
    args = p.parse_args()
    dry_run = not args.apply
    fields = args.field or ["source", "translated", "queue"]
    transforms = args.transform or list(TRANSFORMS)

    from config import TranslationConfig
    from db import DatabaseManager
    from logger import Logger
    config = TranslationConfig()
    db = DatabaseManager(config, Logger(config))

    conn = db.backend.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT title FROM books WHERE id = ?", (args.book_id,))
    book = cursor.fetchone()
    if not book:
        print(f"Book {args.book_id} not found.", file=sys.stderr)
        return 1

    speakers = None
    if "fold" in transforms:
        speakers = load_person_names(db, args.book_id)
        if not speakers:
            # Without the gate the fold cannot tell a speaker label from the
            # tail of a narrative clause, so it would be unsafe to guess.
            print("No person-like entities for this book — the speaker gate is "
                  "empty, so 'fold' would do nothing. Skipping it.",
                  file=sys.stderr)
            transforms = [t for t in transforms if t != "fold"]
            if not transforms:
                return 1

    print(f"Book: {book[0]} (id={args.book_id})")
    print(f"Mode: {'DRY RUN' if dry_run else 'WRITE'} | fields: {', '.join(fields)}"
          f" | transforms: {', '.join(transforms)}"
          + (f" | {len(speakers)} known speaker(s)" if speakers else ""))

    totals = {}
    for label in fields:
        totals[label] = process(label, args.book_id, transforms, speakers,
                                conn, cursor, dry_run)

    if not dry_run:
        conn.commit()
    conn.close()

    print("\nDone.")
    for label, (items, added) in totals.items():
        print(f"  {label:<11} {items} row(s), +{added} line(s)")
    if dry_run:
        print("  (dry run — no changes written; pass --apply to commit)")
    elif totals.get("translated", (0, 0))[0]:
        # Translated text changed, so the cached EPUB/AZW3 is stale.
        db.invalidate_epub_cache(args.book_id)
        print(f"  EPUB/AZW3 cache invalidated for book {args.book_id}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
