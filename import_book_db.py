#!/usr/bin/env python3
"""
Import one book from another t9 install's SQLite database into this one,
under a fresh book id.

Copies the book row, its chapters, entities, footnotes and the per-chapter
entity index (chapter_entities), remapping every chapter/entity id. Columns
are matched by name, so a source a few migrations behind or ahead imports
cleanly; columns only one side has are left at their defaults.

What is deliberately NOT carried over:
    cover_image  — the path names a file on the sender's disk, not in the DB.
                   Pass the image with --cover (posted to the admin API's
                   cover upload, so Spaces and thumbnails happen as usual),
                   or upload it from the Books page afterwards.
    view_count   — their readers, not ours.
    is_public    — the book arrives private unless --public is passed, so it
                   can be looked over before it shows in the Library.
Logs, comments, queue rows, revisions etc. are never copied.

Everything is written in one transaction; a failure leaves nothing behind.

Usage:
    python3 import_book_db.py --file other.db                  # dry run
    python3 import_book_db.py --file other.db --apply
    python3 import_book_db.py --file other.db --source-book 3 --apply --public
    python3 import_book_db.py --file other.db --cover covers/3.png --apply
"""

import argparse
import datetime
import os
import sqlite3
import sys
import warnings

warnings.filterwarnings("ignore", category=FutureWarning)
if "--debug" not in sys.argv:
    os.environ.pop("DEBUG", None)

from config import TranslationConfig
from db import DatabaseManager
from logger import Logger

BATCH = 2000

# Never copied from the source book row (see module docstring).
BOOK_SKIP = {"id", "cover_image", "view_count"}


def _source_columns(src, table):
    return [r[1] for r in src.execute(f"PRAGMA table_info({table})")]


def _shared_columns(db, conn, src, table, skip):
    ours = set(db.backend.get_table_columns(conn, table))
    return [c for c in _source_columns(src, table) if c in ours and c not in skip]


def _datetime_columns(db, conn, table):
    """Columns that are DATETIME here (MySQL) but ISO-8601 TEXT in SQLite —
    chapters.is_proofread, so far. Both sides store UTC; only the literal
    differs ('2026-10-28T14:09:51Z' vs '2026-10-28 14:09:51')."""
    if db.backend.name != "mysql":
        return set()
    cur = conn.cursor()
    cur.execute("SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = ? "
                "AND data_type IN ('datetime', 'timestamp')", (table,))
    return {r[0] for r in cur.fetchall()}


def _to_mysql_datetime(value):
    if not value:
        return None
    dt = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")


def _row_values(row, cols, dt_cols, **overrides):
    out = []
    for c in cols:
        v = overrides[c] if c in overrides else row[c]
        out.append(_to_mysql_datetime(v) if c in dt_cols else v)
    return out


def _collation_merges(conn, entities, index_counts):
    """{loser_id: survivor_id} for source entities that are distinct in SQLite
    but one key under our collation — uq_entity is (book_id, untranslated) in
    utf8mb4_unicode_ci, which folds e.g. 'II俑' and 'Ⅱ俑' (U+2161). MySQL
    itself decides the grouping, so the fold is exactly the key's.

    Only rows that agree on category and translation are merged; anything
    else is a real disagreement and exits for a human to settle."""
    cur = conn.cursor()
    cur.execute("CREATE TEMPORARY TABLE import_entity_keys "
                "(id INT, u VARCHAR(255) COLLATE utf8mb4_unicode_ci)")
    cur.executemany("INSERT INTO import_entity_keys VALUES (?, ?)",
                    [(e["id"], e["untranslated"][:255]) for e in entities])
    cur.execute("SELECT GROUP_CONCAT(id) FROM import_entity_keys "
                "GROUP BY u HAVING COUNT(*) > 1")
    groups = [[int(i) for i in g[0].split(",")] for g in cur.fetchall()]
    cur.execute("DROP TEMPORARY TABLE import_entity_keys")

    by_id = {e["id"]: e for e in entities}
    merges, conflicts = {}, []
    for ids in groups:
        rows = [by_id[i] for i in ids]
        if len({(r["category"], r["translation"]) for r in rows}) > 1:
            conflicts.append(rows)
            continue
        survivor = min(rows, key=lambda r: (-index_counts.get(r["id"], 0),
                                            r["origin_chapter"] or 10**9, r["id"]))
        for r in rows:
            if r["id"] != survivor["id"]:
                merges[r["id"]] = survivor["id"]
                print(f"  merging entity {r['untranslated']!r} into "
                      f"{survivor['untranslated']!r} ({survivor['translation']!r}): "
                      f"one key under utf8mb4_unicode_ci")
    if conflicts:
        for rows in conflicts:
            print("  collation clash, different records:",
                  [(r["untranslated"], r["category"], r["translation"]) for r in rows])
        sys.exit("settle these in the source DB (or by hand) and re-run")
    return merges


def _insert_sql(table, cols):
    return (f"INSERT INTO {table} ({', '.join(cols)}) "
            f"VALUES ({', '.join('?' for _ in cols)})")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", required=True, help="source SQLite database")
    ap.add_argument("--source-book", type=int,
                    help="book id in the source DB (default: its only book)")
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    ap.add_argument("--public", action="store_true",
                    help="keep the source's is_public instead of importing private")
    ap.add_argument("--cover", help="cover image to upload after the import")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    if args.cover and not os.path.isfile(args.cover):
        sys.exit(f"cover not found: {args.cover}")

    src = sqlite3.connect(f"file:{os.path.abspath(args.file)}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row

    books = src.execute("SELECT id, title FROM books").fetchall()
    if args.source_book is None:
        if len(books) != 1:
            sys.exit(f"source has {len(books)} books; pass --source-book "
                     f"({', '.join(f'{b[0]}={b[1]!r}' for b in books)})")
        sb = books[0]["id"]
    else:
        sb = args.source_book
    book = src.execute("SELECT * FROM books WHERE id = ?", (sb,)).fetchone()
    if not book:
        sys.exit(f"no book {sb} in source")

    counts = {t: src.execute(q, (sb,)).fetchone()[0] for t, q in {
        "chapters": "SELECT COUNT(*) FROM chapters WHERE book_id = ?",
        "entities": "SELECT COUNT(*) FROM entities WHERE book_id = ?",
        "footnotes": "SELECT COUNT(*) FROM footnotes WHERE book_id = ?",
        "chapter_entities": "SELECT COUNT(*) FROM chapter_entities ce "
                            "JOIN chapters c ON c.id = ce.chapter_id WHERE c.book_id = ?",
    }.items()}
    print(f"Source book {sb}: {book['title']!r}")
    for t, n in counts.items():
        print(f"  {t:17} {n}")

    config = TranslationConfig()
    db = DatabaseManager(config, Logger(config), strict_writes=True)

    with db._conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM books WHERE title = ?", (book["title"],))
        clash = cur.fetchone()
        if clash:
            sys.exit(f"a book titled {book['title']!r} already exists here (id {clash[0]})")

        entities = src.execute("SELECT * FROM entities WHERE book_id = ? ORDER BY id",
                               (sb,)).fetchall()
        index_counts = dict(src.execute(
            "SELECT entity_id, COUNT(*) FROM chapter_entities GROUP BY entity_id").fetchall())
        merges = _collation_merges(conn, entities, index_counts)

        if not args.apply:
            print("\nDry run — pass --apply to import.")
            return

        # Book
        cols = _shared_columns(db, conn, src, "books", BOOK_SKIP)
        overrides = {} if args.public else {"is_public": 0}
        vals = _row_values(book, cols, _datetime_columns(db, conn, "books"),
                           **{k: v for k, v in overrides.items() if k in cols})
        cur.execute(_insert_sql("books", cols), vals)
        new_book = cur.lastrowid

        # Chapters (one at a time: each new id is needed for the map)
        cols = _shared_columns(db, conn, src, "chapters", {"id"})
        dt_cols = _datetime_columns(db, conn, "chapters")
        sql = _insert_sql("chapters", cols)
        chapter_map = {}
        for r in src.execute("SELECT * FROM chapters WHERE book_id = ? ORDER BY chapter_number",
                             (sb,)):
            cur.execute(sql, _row_values(r, cols, dt_cols, book_id=new_book))
            chapter_map[r["id"]] = cur.lastrowid

        # Entities
        cols = _shared_columns(db, conn, src, "entities", {"id"})
        dt_cols = _datetime_columns(db, conn, "entities")
        sql = _insert_sql("entities", cols)
        entity_map = {}
        for r in entities:
            if r["id"] in merges:
                continue
            cur.execute(sql, _row_values(r, cols, dt_cols, book_id=new_book))
            entity_map[r["id"]] = cur.lastrowid
        for loser, survivor in merges.items():
            entity_map[loser] = entity_map[survivor]

        # Footnotes
        cols = _shared_columns(db, conn, src, "footnotes", {"id"})
        dt_cols = _datetime_columns(db, conn, "footnotes")
        sql = _insert_sql("footnotes", cols)
        rows = []
        for r in src.execute("SELECT * FROM footnotes WHERE book_id = ? ORDER BY id", (sb,)):
            row = dict(r)
            row["book_id"] = new_book
            row["chapter_id"] = chapter_map[row["chapter_id"]]
            rows.append(_row_values(row, cols, dt_cols))
        cur.executemany(sql, rows)

        # Per-chapter entity index. Merged entities share a target id, so
        # their rows for one chapter combine into one (occurrences summed).
        cols = _shared_columns(db, conn, src, "chapter_entities", set())
        sql = _insert_sql("chapter_entities", cols)
        index, skipped = {}, 0
        for r in src.execute("SELECT ce.* FROM chapter_entities ce "
                             "JOIN chapters c ON c.id = ce.chapter_id WHERE c.book_id = ?",
                             (sb,)):
            row = dict(r)
            if row["entity_id"] not in entity_map:
                skipped += 1          # index row for an entity of another book
                continue
            row["chapter_id"] = chapter_map[row["chapter_id"]]
            row["entity_id"] = entity_map[row["entity_id"]]
            key = (row["chapter_id"], row["entity_id"])
            if key in index:
                index[key]["occurrences"] = ((index[key].get("occurrences") or 0)
                                             + (row.get("occurrences") or 0))
            else:
                index[key] = row
        rows_ce = [[row[c] for c in cols] for row in index.values()]
        for i in range(0, len(rows_ce), BATCH):
            cur.executemany(sql, rows_ce[i:i + BATCH])

    print(f"\nImported as book {new_book}"
          f"{'' if args.public else ' (private)'}: {len(chapter_map)} chapters, "
          f"{len(entity_map) - len(merges)} entities, {len(rows)} footnotes"
          + (f" ({len(merges)} merged by collation)" if merges else "")
          + (f"; skipped {skipped} index rows for foreign entities" if skipped else ""))

    if args.cover:
        upload_cover(new_book, args.cover)


def upload_cover(book_id, path):
    """Upload through the admin endpoint, after the import has committed —
    a failed upload leaves the book in place, coverless, to retry by hand."""
    import mimetypes
    from t9_client import client
    ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
    with client(timeout=120) as c, open(path, "rb") as f:
        r = c.post(f"/api/books/{book_id}/cover",
                   files={"file": (os.path.basename(path), f, ctype)})
    if r.status_code == 200:
        print(f"Cover uploaded: {r.json().get('cover_image')}")
    else:
        print(f"Cover upload failed ({r.status_code}): {r.text[:200]} — "
              f"upload it from the Books page", file=sys.stderr)


if __name__ == "__main__":
    main()
