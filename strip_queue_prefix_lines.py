#!/usr/bin/env python3
"""Delete lines beginning with a given prefix from every queue item of a book.

Defaults to a dry run; pass --apply (or --commit) to actually write changes.

Usage:
    python strip_queue_prefix_lines.py <book_id>                            # dry run, default prefix
    python strip_queue_prefix_lines.py <book_id> --apply                    # write changes
    python strip_queue_prefix_lines.py <book_id> --prefix "Note: "          # custom prefix
    python strip_queue_prefix_lines.py <book_id> --prefix "Note: " --apply
"""

import json
import os
import sys

# Project root (parent of utility_scripts/)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db_backend import create_backend


DEFAULT_PREFIX = "溫馨提示: "


def filter_lines(content, prefix):
    """Return (filtered_content, removed_count).

    Content can be a JSON-serialized list, a raw list, or a string. We always
    return the same shape we received so the queue row stays valid.
    """
    # Try to decode as JSON list first (this is how add_to_queue stores it)
    parsed = None
    was_json_list = False
    if isinstance(content, str):
        try:
            decoded = json.loads(content)
            if isinstance(decoded, list):
                parsed = decoded
                was_json_list = True
        except (json.JSONDecodeError, TypeError):
            pass

    if parsed is not None:
        # JSON list of lines
        kept = [ln for ln in parsed if not (isinstance(ln, str) and ln.startswith(prefix))]
        removed = len(parsed) - len(kept)
        if was_json_list:
            return json.dumps(kept, ensure_ascii=False), removed
        return kept, removed

    if isinstance(content, list):
        kept = [ln for ln in content if not (isinstance(ln, str) and ln.startswith(prefix))]
        return kept, len(content) - len(kept)

    if isinstance(content, str):
        # Plain string with newline-separated lines
        lines = content.split("\n")
        kept = [ln for ln in lines if not ln.startswith(prefix)]
        return "\n".join(kept), len(lines) - len(kept)

    return content, 0


def parse_args(argv):
    apply_changes = False
    prefix = DEFAULT_PREFIX
    positional = []

    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--apply", "--commit"):
            apply_changes = True
        elif a == "--prefix":
            if i + 1 >= len(argv):
                print("Error: --prefix requires a value")
                sys.exit(1)
            prefix = argv[i + 1]
            i += 1
        elif a.startswith("--prefix="):
            prefix = a[len("--prefix="):]
        elif a.startswith("--"):
            print(f"Error: unknown option {a!r}")
            sys.exit(1)
        else:
            positional.append(a)
        i += 1

    return apply_changes, prefix, positional


def main():
    apply_changes, prefix, positional = parse_args(sys.argv[1:])
    dry_run = not apply_changes

    if len(positional) != 1:
        print("Usage: python strip_queue_prefix_lines.py <book_id> [--prefix <text>] [--apply]")
        sys.exit(1)

    if not prefix:
        print("Error: --prefix value cannot be empty")
        sys.exit(1)

    try:
        book_id = int(positional[0])
    except ValueError:
        print(f"Error: book_id must be an integer (got {positional[0]!r})")
        sys.exit(1)

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

    print(f"Scanning {len(rows)} queue item(s) for book {book_id}"
          f"{'  [DRY RUN]' if dry_run else ''}...")
    print(f"Prefix: {prefix!r}\n")

    total_removed = 0
    items_modified = 0

    for queue_id, ch_num, title, content in rows:
        new_content, removed = filter_lines(content, prefix)
        if removed == 0:
            continue

        items_modified += 1
        total_removed += removed
        ch_label = f"ch {ch_num}" if ch_num is not None else "no chapter#"
        print(f"  Queue {queue_id} ({ch_label}, {title!r}): removed {removed} line(s)")

        if not dry_run:
            cursor.execute(
                "UPDATE queue SET content = ? WHERE id = ?",
                (new_content, queue_id),
            )

    if not dry_run:
        conn.commit()
    conn.close()

    print(f"\nDone.")
    print(f"  Queue items scanned:  {len(rows)}")
    print(f"  Queue items modified: {items_modified}")
    print(f"  Lines removed:        {total_removed}")
    if dry_run:
        print("  (dry run — no changes written; pass --apply to commit)")


if __name__ == "__main__":
    main()
