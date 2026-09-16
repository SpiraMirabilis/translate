#!/usr/bin/env python3
"""
Delete lines from translated chapter content that begin with a given prefix.

Scans the translated text of a specified book (or every book if "all" is given)
and removes any line whose text starts with the supplied prefix.

Safety:
    - The prefix MUST be non-empty (otherwise every line would be deleted).
    - Always performs a dry-run first, prints what would be deleted, then asks
      for explicit yes/no confirmation before writing changes.

Usage:
    python delete_lines_by_prefix.py <book_id> "<prefix>"
    python delete_lines_by_prefix.py all "<prefix>"
"""

import argparse
import json
import sys

from db_backend import create_backend


def find_deletions(lines, prefix):
    """Return (kept_lines, deleted_lines_with_index) for a chapter's lines."""
    kept = []
    deleted = []
    for idx, line in enumerate(lines):
        if isinstance(line, str) and line.startswith(prefix):
            deleted.append((idx, line))
        else:
            kept.append(line)
    return kept, deleted


def scan_book(cursor, book_id, prefix):
    """Return list of (chapter_id, chapter_number, title, kept_lines, deleted_lines)."""
    cursor.execute(
        'SELECT id, chapter_number, title, translated_content FROM chapters '
        'WHERE book_id = ? ORDER BY chapter_number',
        (book_id,)
    )
    rows = cursor.fetchall()

    results = []
    for chapter_id, chapter_number, title, translated_content in rows:
        if not translated_content:
            continue
        try:
            lines = json.loads(translated_content)
            was_json = True
        except (json.JSONDecodeError, TypeError):
            lines = translated_content.split('\n')
            was_json = False

        if not isinstance(lines, list):
            continue

        kept, deleted = find_deletions(lines, prefix)
        if deleted:
            results.append({
                'chapter_id': chapter_id,
                'chapter_number': chapter_number,
                'title': title,
                'kept': kept,
                'deleted': deleted,
                'was_json': was_json,
            })
    return results


def print_dry_run(book_id, results, preview_chars=120):
    if not results:
        print(f"  [book {book_id}] No matching lines.")
        return 0
    total = 0
    for r in results:
        label = r['title'] or f"Chapter {r['chapter_number']}"
        print(f"  [book {book_id}] Ch {r['chapter_number']} ({label}): "
              f"{len(r['deleted'])} line(s) to delete")
        for idx, line in r['deleted']:
            preview = line if len(line) <= preview_chars else line[:preview_chars] + '…'
            print(f"      line {idx}: {preview!r}")
        total += len(r['deleted'])
    return total


def apply_deletions(cursor, results):
    for r in results:
        if r['was_json']:
            new_content = json.dumps(r['kept'], ensure_ascii=False)
        else:
            new_content = '\n'.join(r['kept'])
        cursor.execute(
            'UPDATE chapters SET translated_content = ? WHERE id = ?',
            (new_content, r['chapter_id'])
        )


def confirm(prompt):
    while True:
        ans = input(prompt).strip().lower()
        if ans in ('y', 'yes'):
            return True
        if ans in ('n', 'no', ''):
            return False
        print("Please answer 'yes' or 'no'.")


def main():
    parser = argparse.ArgumentParser(
        description='Delete translated lines that begin with a given prefix.'
    )
    parser.add_argument('book_id', help='Book ID to process, or "all" for every book')
    parser.add_argument('prefix', help='Prefix that lines must start with to be deleted (must be non-empty)')
    args = parser.parse_args()

    if not args.prefix:
        parser.error('prefix must be non-empty — refusing to match every line')

    backend = create_backend()
    conn = backend.get_connection()
    cursor = conn.cursor()

    if args.book_id.lower() == 'all':
        cursor.execute('SELECT DISTINCT book_id FROM chapters ORDER BY book_id')
        book_ids = [row[0] for row in cursor.fetchall()]
        if not book_ids:
            print("No books with chapters found.")
            conn.close()
            return
        print(f"Scanning {len(book_ids)} book(s): {book_ids}")
    else:
        try:
            book_ids = [int(args.book_id)]
        except ValueError:
            parser.error('book_id must be an integer or "all"')

    print(f"\n[DRY RUN] Looking for lines starting with: {args.prefix!r}\n")

    all_results = {}
    grand_total = 0
    for book_id in book_ids:
        results = scan_book(cursor, book_id, args.prefix)
        all_results[book_id] = results
        grand_total += print_dry_run(book_id, results)

    if grand_total == 0:
        print("\nNothing to delete.")
        conn.close()
        return

    chapters_affected = sum(len(r) for r in all_results.values())
    print(f"\n[DRY RUN] Would delete {grand_total} line(s) across "
          f"{chapters_affected} chapter(s).")

    if not sys.stdin.isatty():
        print("Refusing to apply: stdin is not a TTY (cannot confirm interactively).")
        conn.close()
        return

    if not confirm(f"\nApply these deletions? Type 'yes' to confirm: "):
        print("Aborted. No changes made.")
        conn.close()
        return

    for book_id, results in all_results.items():
        apply_deletions(cursor, results)

    conn.commit()
    print(f"\nDone. Deleted {grand_total} line(s) across {chapters_affected} chapter(s).")
    conn.close()


if __name__ == '__main__':
    main()
