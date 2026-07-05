"""
Preview / strip "Chapter <num>" prefix from chapter titles.

Usage:
    python strip_chapter_prefix_titles.py            # dry-run preview
    python strip_chapter_prefix_titles.py --apply    # actually update
"""

import re
import sys

from db_backend import create_backend

# Matches: "Chapter 12", "Chapter 12:", "Chapter 12 -", "Chapter 12.", etc.
# Captures the remainder after the prefix and optional separator.
PREFIX_RE = re.compile(
    r'^\s*chapter\s+\d+\s*[:\-\u2013\u2014.\)]?\s*',
    re.IGNORECASE,
)


def strip_chapter_prefix(title):
    """Strip a leading "Chapter N" prefix from a title.

    Returns the stripped title, or the original if no prefix matches or if
    stripping would leave an empty string (e.g. the title is just "Chapter 5").
    """
    if not title:
        return title
    m = PREFIX_RE.match(title)
    if not m:
        return title
    stripped = title[m.end():].strip()
    if not stripped:
        return title
    return stripped


def main():
    apply = '--apply' in sys.argv

    backend = create_backend()
    conn = backend.get_connection()
    cur = conn.cursor()

    cur.execute("SELECT id, book_id, chapter_number, title FROM chapters")
    rows = cur.fetchall()

    changes = []
    empties = []
    for row in rows:
        cid, book_id, chap_num, title = row
        if title is None:
            continue
        m = PREFIX_RE.match(title)
        if not m:
            continue
        new_title = title[m.end():].strip()
        if not new_title:
            empties.append((cid, book_id, chap_num, title))
            continue
        if new_title == title:
            continue
        changes.append((cid, book_id, chap_num, title, new_title))

    print(f"Scanned {len(rows)} chapters")
    print(f"Will update: {len(changes)}")
    print(f"Skipped (would become empty): {len(empties)}")
    print()

    for cid, book_id, chap_num, old, new in changes[:30]:
        print(f"  [book={book_id} ch={chap_num}] {old!r:60} -> {new!r}")
    if len(changes) > 30:
        print(f"  ... and {len(changes) - 30} more")

    if empties:
        print()
        print("Skipped (no remainder after stripping):")
        for cid, book_id, chap_num, title in empties[:10]:
            print(f"  [book={book_id} ch={chap_num}] {title!r}")
        if len(empties) > 10:
            print(f"  ... and {len(empties) - 10} more")

    if not apply:
        print()
        print("Dry-run only. Re-run with --apply to commit.")
        return

    if not changes:
        print("Nothing to do.")
        return

    print()
    print(f"Applying {len(changes)} updates...")
    for cid, _, _, _, new in changes:
        cur.execute("UPDATE chapters SET title = ? WHERE id = ?", (new, cid))
    conn.commit()
    print("Done.")
    conn.close()


if __name__ == '__main__':
    main()
