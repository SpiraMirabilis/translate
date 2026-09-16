#!/usr/bin/env python3
"""Split oversized source paragraphs in a book's queued (and optionally stored)
chapters into several readable paragraphs, at sentence boundaries.

Some raws (notably the Russian ones) arrive as enormous single-paragraph walls.
Splitting the *source* before translation makes the model emit normal
paragraphing — and since it runs on the queue, it fixes chapters that haven't
been translated yet without any re-translation.

The transform never adds, drops, or rewords a character: it only inserts
paragraph breaks at sentence boundaries (see paragraph_splitter.py). ⟦IMG⟧
markers, blank separators, and already-reasonable lines are left untouched, so
re-running is a no-op.

Default is a DRY RUN. Pass --apply to write.

Usage:
    python3 split_source_paragraphs.py --book-id 54                     # dry run, queue + chapters
    python3 split_source_paragraphs.py --book-id 54 --apply             # write both
    python3 split_source_paragraphs.py --book-id 54 --smart --apply     # model-chosen seams
    python3 split_source_paragraphs.py --book-id 54 --queue-only --apply
    python3 split_source_paragraphs.py --book-id 54 --chapters-only --apply
    python3 split_source_paragraphs.py --book-id 54 --target 350 --min-split 450

By default both the queue (pre-translation source) and stored chapters'
`untranslated_content` are split. Splitting stored source never touches the
translated text — it only makes a *future* re-translation start from
pre-paragraphed source. Use --queue-only / --chapters-only to scope.
"""

import argparse
import json
import sys

from db_backend import create_backend
from paragraph_splitter import (
    split_content,
    build_smart_messages,
    parse_smart_response,
)


def make_smart_fn(model_spec):
    """Build a smart_fn that asks a model for break points (indices only)."""
    from config import TranslationConfig
    config = TranslationConfig()
    provider, model_name = config.get_client(model_spec)
    print(f"Smart mode: {provider.provider_name}/{model_name}")

    def smart_fn(sentences, target):
        system, user = build_smart_messages(sentences, target)
        resp = provider.chat_completion(
            model=model_name,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.0,
        )
        raw = provider.get_response_content(resp)
        return parse_smart_response(raw, len(sentences))

    return smart_fn


def _decode(content):
    """Return (lines_list, was_json_string)."""
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


def process_rows(rows, table, id_col, content_col, conn, cursor,
                 target, min_split, smart_fn, dry_run):
    """Split content for each (id, label, content) row of one table."""
    items_changed = lines_added = 0
    for row_id, label, content in rows:
        lines, was_json = _decode(content)
        if lines is None:
            continue
        new_lines, split_count = split_content(
            lines, target=target, min_split=min_split, smart_fn=smart_fn,
        )
        if split_count == 0:
            continue
        items_changed += 1
        added = len(new_lines) - len(lines)
        lines_added += added
        print(f"  {table} {row_id} ({label}): {split_count} paragraph(s) "
              f"split, +{added} line(s)")
        if not dry_run:
            cursor.execute(
                f"UPDATE {table} SET {content_col} = ? WHERE {id_col} = ?",
                (_encode(new_lines, was_json), row_id),
            )
    return items_changed, lines_added


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--book-id", type=int, required=True)
    p.add_argument("--apply", action="store_true",
                   help="Write changes (default is a dry run).")
    p.add_argument("--smart", action="store_true",
                   help="Use a model to choose break points for long narration walls.")
    p.add_argument("--model", default="claude:claude-haiku-4-5",
                   help="Model spec for --smart (default: claude:claude-haiku-4-5).")
    p.add_argument("--target", type=int, default=400,
                   help="Approximate target paragraph length in characters (default 400).")
    p.add_argument("--min-split", type=int, default=500,
                   help="Only split lines longer than this (default 500).")
    scope = p.add_mutually_exclusive_group()
    scope.add_argument("--queue-only", action="store_true",
                       help="Only split queued (pre-translation) source.")
    scope.add_argument("--chapters-only", action="store_true",
                       help="Only split stored chapters' source (for future re-translation).")
    args = p.parse_args()
    do_queue = not args.chapters_only
    do_chapters = not args.queue_only
    dry_run = not args.apply

    smart_fn = make_smart_fn(args.model) if args.smart else None

    backend = create_backend()
    conn = backend.get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT title FROM books WHERE id = ?", (args.book_id,))
    book = cursor.fetchone()
    if not book:
        print(f"Book {args.book_id} not found.", file=sys.stderr)
        return 1
    print(f"Book: {book[0]} (id={args.book_id})")
    print(f"Mode: {'DRY RUN' if dry_run else 'WRITE'} | target={args.target} "
          f"min_split={args.min_split} smart={args.smart}\n")

    qi = ql = ci = cl = 0

    # Queue items (pre-translation source).
    if do_queue:
        cursor.execute(
            "SELECT id, chapter_number, content FROM queue "
            "WHERE book_id = ? ORDER BY position ASC",
            (args.book_id,),
        )
        q_rows = [(r[0], f"ch {r[1]}" if r[1] is not None else "no ch#", r[2])
                  for r in cursor.fetchall()]
        print(f"Queue items: {len(q_rows)}")
        qi, ql = process_rows(q_rows, "queue", "id", "content",
                              conn, cursor, args.target, args.min_split, smart_fn, dry_run)

    # Stored chapters' source (takes effect only on re-translation).
    if do_chapters:
        cursor.execute(
            "SELECT id, chapter_number, untranslated_content FROM chapters "
            "WHERE book_id = ? ORDER BY chapter_number ASC",
            (args.book_id,),
        )
        c_rows = [(r[0], f"ch {r[1]}", r[2]) for r in cursor.fetchall()]
        print(f"\nStored chapters: {len(c_rows)}")
        ci, cl = process_rows(c_rows, "chapters", "id", "untranslated_content",
                              conn, cursor, args.target, args.min_split, smart_fn, dry_run)

    if not dry_run:
        conn.commit()
    conn.close()

    print(f"\nDone.")
    if do_queue:
        print(f"  Queue items split:    {qi}  (+{ql} lines)")
    if do_chapters:
        print(f"  Chapters split:       {ci}  (+{cl} lines)")
    if dry_run:
        print("  (dry run — no changes written; pass --apply to commit)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
