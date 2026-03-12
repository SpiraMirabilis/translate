#!/usr/bin/env python3
"""
Deduplicate the entities table so each (book_id, untranslated) pair is unique,
dropping the category dimension from the uniqueness constraint.

Strategy:
- For entries with identical translations across categories: keep one, pick best category
- For entries with conflicting translations: keep the highest-priority category's row
- Print summary and details for conflicts so the user can review

Safety: backs up database.db first, uses transactions throughout.
"""

import sqlite3
import shutil
import os
import sys
from collections import defaultdict

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "database.db")
BACKUP_PATH = DB_PATH + ".pre-dedup"

# Priority order: lower index = higher priority = kept first
CATEGORY_PRIORITY = [
    "characters",
    "places",
    "organizations",
    "titles",
    "abilities",
    "equipment",
    "creatures",
]


def category_rank(cat):
    """Return priority rank for a category. Lower is better. Unknown categories rank last."""
    cat_lower = (cat or "").lower().strip()
    try:
        return CATEGORY_PRIORITY.index(cat_lower)
    except ValueError:
        return len(CATEGORY_PRIORITY)  # unknown categories are lowest priority


def book_id_key(book_id):
    """Normalize book_id for grouping — NULL becomes a sentinel."""
    return book_id if book_id is not None else "__NULL__"


def main():
    if not os.path.exists(DB_PATH):
        print(f"ERROR: Database not found at {DB_PATH}")
        sys.exit(1)

    # --- Step 1: Back up ---
    print(f"Backing up {DB_PATH} -> {BACKUP_PATH}")
    shutil.copy2(DB_PATH, BACKUP_PATH)
    print("Backup complete.\n")

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = OFF")  # we'll be recreating the table
    conn.row_factory = sqlite3.Row

    # --- Step 2: Analyze duplicates ---
    print("Analyzing duplicates...")

    rows = conn.execute(
        "SELECT id, category, untranslated, translation, last_chapter, "
        "incorrect_translation, gender, book_id, origin_chapter, note "
        "FROM entities ORDER BY id"
    ).fetchall()

    # Group by (book_id, untranslated)
    groups = defaultdict(list)
    for row in rows:
        key = (book_id_key(row["book_id"]), row["untranslated"])
        groups[key].append(dict(row))

    # Separate into unique vs duplicated
    duplicated_groups = {k: v for k, v in groups.items() if len(v) > 1}
    unique_groups = {k: v for k, v in groups.items() if len(v) == 1}

    print(f"Total entities: {len(rows)}")
    print(f"Unique (book_id, untranslated) groups: {len(groups)}")
    print(f"Groups with duplicates: {len(duplicated_groups)}")
    print()

    # Classify duplicates: identical translations vs conflicting
    identical_groups = {}
    conflicting_groups = {}

    for key, entries in duplicated_groups.items():
        translations = set(e["translation"] for e in entries)
        if len(translations) == 1:
            identical_groups[key] = entries
        else:
            conflicting_groups[key] = entries

    print(f"Duplicated groups with IDENTICAL translations: {len(identical_groups)}")
    print(f"Duplicated groups with CONFLICTING translations: {len(conflicting_groups)}")
    print()

    # --- Step 3: Decide which rows to keep ---
    keep_ids = set()
    drop_ids = set()

    # For unique groups, keep the single entry
    for key, entries in unique_groups.items():
        keep_ids.add(entries[0]["id"])

    # For identical translation groups, keep the one with best category
    for key, entries in identical_groups.items():
        entries_sorted = sorted(entries, key=lambda e: category_rank(e["category"]))
        keep_ids.add(entries_sorted[0]["id"])
        for e in entries_sorted[1:]:
            drop_ids.add(e["id"])

    # For conflicting translation groups, keep the one with best category
    print("=" * 80)
    print("CONFLICTING TRANSLATIONS (review these)")
    print("=" * 80)

    for key, entries in sorted(conflicting_groups.items(), key=lambda x: x[0]):
        book_id_display = key[0] if key[0] != "__NULL__" else "GLOBAL"
        untranslated = key[1]

        entries_sorted = sorted(entries, key=lambda e: category_rank(e["category"]))
        kept = entries_sorted[0]
        dropped = entries_sorted[1:]

        keep_ids.add(kept["id"])
        for e in dropped:
            drop_ids.add(e["id"])

        print(f"\n  book_id={book_id_display}  untranslated=\"{untranslated}\"")
        print(f"    KEPT:    category={kept['category']:<15} translation=\"{kept['translation']}\"")
        for e in dropped:
            print(f"    DROPPED: category={e['category']:<15} translation=\"{e['translation']}\"")

    if not conflicting_groups:
        print("  (none)")

    print()
    print("=" * 80)
    print()

    # Sanity check
    assert len(keep_ids & drop_ids) == 0, "Bug: overlapping keep/drop sets"
    assert len(keep_ids) + len(drop_ids) == len(rows), (
        f"Bug: keep({len(keep_ids)}) + drop({len(drop_ids)}) != total({len(rows)})"
    )

    print(f"Rows to KEEP:  {len(keep_ids)}")
    print(f"Rows to DROP:  {len(drop_ids)}")
    print()

    # --- Step 4: Recreate table with new constraint ---
    print("Recreating entities table with UNIQUE(book_id, untranslated) constraint...")

    # Use a transaction for the whole operation
    with conn:
        # Create new table with updated constraint
        conn.execute("""
            CREATE TABLE entities_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                untranslated TEXT NOT NULL,
                translation TEXT NOT NULL,
                last_chapter TEXT,
                incorrect_translation TEXT,
                gender TEXT,
                book_id INTEGER,
                origin_chapter INTEGER,
                note TEXT,
                UNIQUE(book_id, untranslated)
            )
        """)

        # Copy only the rows we want to keep, preserving their original IDs
        keep_id_list = sorted(keep_ids)

        # Insert in batches to avoid huge IN clauses
        BATCH = 500
        for i in range(0, len(keep_id_list), BATCH):
            batch = keep_id_list[i : i + BATCH]
            placeholders = ",".join("?" * len(batch))
            conn.execute(
                f"""
                INSERT INTO entities_new (id, category, untranslated, translation,
                    last_chapter, incorrect_translation, gender, book_id,
                    origin_chapter, note)
                SELECT id, category, untranslated, translation,
                    last_chapter, incorrect_translation, gender, book_id,
                    origin_chapter, note
                FROM entities
                WHERE id IN ({placeholders})
                """,
                batch,
            )

        # Drop old table and rename
        conn.execute("DROP TABLE entities")
        conn.execute("ALTER TABLE entities_new RENAME TO entities")

        # Recreate indices
        conn.execute("CREATE INDEX IF NOT EXISTS idx_category ON entities(category)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_untranslated ON entities(untranslated)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_book_id ON entities(book_id)")

    print("Table recreated successfully.\n")

    # --- Step 5: Verify ---
    final_count = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    print(f"Final row count: {final_count} (was {len(rows)}, removed {len(rows) - final_count})")

    # Verify no duplicates remain
    dupes = conn.execute("""
        SELECT book_id, untranslated, COUNT(*) as cnt
        FROM entities
        GROUP BY COALESCE(book_id, -1), untranslated
        HAVING cnt > 1
    """).fetchall()

    if dupes:
        print(f"WARNING: {len(dupes)} duplicate (book_id, untranslated) pairs still exist!")
        for d in dupes:
            print(f"  book_id={d[0]}, untranslated={d[1]}, count={d[2]}")
    else:
        print("Verification passed: no duplicate (book_id, untranslated) pairs.")

    conn.close()
    print("\nDone. Backup is at:", BACKUP_PATH)


if __name__ == "__main__":
    main()
