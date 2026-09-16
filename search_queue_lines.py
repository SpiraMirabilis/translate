#!/usr/bin/env python3
"""Search a book's queue text for lines matching a glob pattern, and show them.

Glob matching is per-line via fnmatch — the pattern is tested against the whole
line, so use leading/trailing ``*`` to match a substring. Useful for stripping
advertising lines out of raw novel sources.

By default the script only displays matches (no changes). Pass --delete to
remove matching lines; --delete is itself a dry run until you add --apply.

Usage:
    python search_queue_lines.py <book_id> --pattern "*关注公众号*"           # show matches
    python search_queue_lines.py <book_id> --pattern "*广告*" --delete         # preview deletion
    python search_queue_lines.py <book_id> --pattern "*广告*" --delete --apply  # write changes
"""

import fnmatch
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db_backend import create_backend


def split_content(content):
    """Return (lines, shape) for a queue row's content.

    shape is one of: 'json' (JSON-serialized list), 'list' (raw list),
    'str' (newline-joined string). It tells us how to put the content back.
    """
    if isinstance(content, str):
        try:
            decoded = json.loads(content)
            if isinstance(decoded, list):
                return [x for x in decoded], "json"
        except (json.JSONDecodeError, TypeError):
            pass
        return content.split("\n"), "str"

    if isinstance(content, list):
        return list(content), "list"

    return None, None


def join_content(lines, shape):
    """Inverse of split_content — rebuild the stored value from kept lines."""
    if shape == "json":
        return json.dumps(lines, ensure_ascii=False)
    if shape == "list":
        return lines
    if shape == "str":
        return "\n".join(lines)
    return lines


def parse_args(argv):
    pattern = None
    delete = False
    apply_changes = False
    positional = []

    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--delete":
            delete = True
        elif a in ("--apply", "--commit"):
            apply_changes = True
        elif a == "--pattern":
            if i + 1 >= len(argv):
                print("Error: --pattern requires a value")
                sys.exit(1)
            pattern = argv[i + 1]
            i += 1
        elif a.startswith("--pattern="):
            pattern = a[len("--pattern="):]
        elif a.startswith("--"):
            print(f"Error: unknown option {a!r}")
            sys.exit(1)
        else:
            positional.append(a)
        i += 1

    return pattern, delete, apply_changes, positional


def main():
    pattern, delete, apply_changes, positional = parse_args(sys.argv[1:])

    if len(positional) != 1 or not pattern:
        print("Usage: python search_queue_lines.py <book_id> --pattern <glob> "
              "[--delete] [--apply]")
        sys.exit(1)

    try:
        book_id = int(positional[0])
    except ValueError:
        print(f"Error: book_id must be an integer (got {positional[0]!r})")
        sys.exit(1)

    dry_run = not apply_changes

    backend = create_backend()
    conn = backend.get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT id, chapter_number, title, content FROM queue "
        "WHERE book_id = ? ORDER BY position ASC",
        (book_id,),
    )
    rows = cursor.fetchall()

    if not rows:
        print(f"No queue items found for book {book_id}.")
        conn.close()
        return

    mode = "DELETE" if delete else "SEARCH"
    print(f"Scanning {len(rows)} queue item(s) for book {book_id}  [{mode}]"
          f"{'  [DRY RUN]' if delete and dry_run else ''}")
    print(f"Pattern: {pattern!r}\n")

    total_matched = 0
    items_modified = 0

    for queue_id, ch_num, title, content in rows:
        lines, shape = split_content(content)
        if lines is None:
            continue

        kept = []
        matched = []
        for ln in lines:
            if isinstance(ln, str) and fnmatch.fnmatch(ln, pattern):
                matched.append(ln)
            else:
                kept.append(ln)

        if not matched:
            continue

        items_modified += 1
        total_matched += len(matched)
        ch_label = f"ch {ch_num}" if ch_num is not None else "no chapter#"
        print(f"  Queue {queue_id} ({ch_label}, {title!r}): "
              f"{len(matched)} match(es)")
        for ln in matched:
            print(f"      | {ln}")

        if delete and not dry_run:
            cursor.execute(
                "UPDATE queue SET content = ? WHERE id = ?",
                (join_content(kept, shape), queue_id),
            )

    if delete and not dry_run:
        conn.commit()
    conn.close()

    print(f"\nDone.")
    print(f"  Queue items scanned: {len(rows)}")
    print(f"  Queue items matched: {items_modified}")
    print(f"  Lines matched:       {total_matched}")
    if delete:
        if dry_run:
            print("  (dry run — no lines deleted; pass --apply to commit)")
        else:
            print("  (matching lines deleted)")
    else:
        print("  (search only — pass --delete to remove these lines)")


if __name__ == "__main__":
    main()
