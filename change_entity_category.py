#!/usr/bin/env python3
"""
Change an entity's category in the database for a given book.

Use when an entity is filed under the wrong category (e.g. moving a talent
from `abilities` to `life simulator talents`). Does not touch translations
or chapter content.

Usage:
    python change_entity_category.py --book-id 16 \
        --new-category "life simulator talents" \
        --untranslated 天靈根 --untranslated 修仙四藝
"""

import argparse
import sys

from config import TranslationConfig
from db import DatabaseManager
from genres import extract_categories_from_prompt
from logger import Logger


def find_entities(db_manager, book_id, untranslated):
    with db_manager._conn(dict_rows=True) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, category, translation FROM entities WHERE book_id = ? AND untranslated = ?",
            (book_id, untranslated),
        )
        return [(r["id"], r["category"], r["translation"]) for r in cursor.fetchall()]


def known_categories(db_manager, book_id):
    with db_manager._conn(dict_rows=True) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT category FROM entities WHERE book_id = ?", (book_id,))
        used = [r["category"] for r in cursor.fetchall()]
        cursor.execute("SELECT prompt_template FROM books WHERE id = ?", (book_id,))
        row = cursor.fetchone()
    template = row["prompt_template"] if row else None
    template_cats = extract_categories_from_prompt(template) if template else []
    # books.categories governs a book's category list since the categories left
    # the prompt corpus; the template parse only helps legacy hand-written prompts.
    book_cats = db_manager.get_book_categories(book_id) if row else []
    return sorted(set(c for c in used if c) | set(template_cats or [])
                  | set(c for c in book_cats if c))


def update_category(db_manager, entity_id, new_category):
    """Update an entity's category via the repo method (single transaction)."""
    db_manager.update_entity_by_id(entity_id, category=new_category)


def change_entity_categories(db_manager, book_id, keys, new_category,
                             current_category=None, force=False, dry_run=False) -> dict:
    """Move each entity in `keys` to `new_category`. Never prints or exits.

    Returns a dict:
      ok               False only when `new_category` was refused
      error            why it was refused (else None)
      new_category, dry_run, force
      known_categories the book's known categories (in use, template, books.categories)
      results          one dict per key, in order:
                       {untranslated, status, entity_id, old_category,
                        translation, categories}
                       status: "updated" | "would_update" | "unchanged"
                       (already in new_category) | "not_found" | "ambiguous"
                       (categories lists the candidates; narrow with
                       `current_category`)
      counts           {updated, unchanged, not_found, ambiguous} — "updated"
                       counts would-be updates on a dry run, as the CLI did.

    An unknown `new_category` is refused unless `force` — nothing is looked
    up or written in that case.
    """
    valid = known_categories(db_manager, book_id)
    report = {
        "ok": True, "error": None, "new_category": new_category,
        "dry_run": dry_run, "force": force, "known_categories": valid,
        "results": [],
        "counts": {"updated": 0, "unchanged": 0, "not_found": 0, "ambiguous": 0},
    }
    if new_category not in valid and not force:
        report.update(ok=False,
                      error=f"Category {new_category!r} is not among known categories "
                            f"for book {book_id}; pass force to use it anyway.")
        return report

    counts = report["counts"]
    for u in keys:
        matches = find_entities(db_manager, book_id, u)
        if current_category:
            matches = [m for m in matches if m[1] == current_category]
        entry = {"untranslated": u, "status": None, "entity_id": None,
                 "old_category": None, "translation": None,
                 "categories": [m[1] for m in matches]}
        report["results"].append(entry)

        if not matches:
            entry["status"] = "not_found"
            counts["not_found"] += 1
            continue
        if len(matches) > 1:
            entry["status"] = "ambiguous"
            counts["ambiguous"] += 1
            continue

        eid, old_cat, trans = matches[0]
        entry.update(entity_id=eid, old_category=old_cat, translation=trans)
        if old_cat == new_category:
            entry["status"] = "unchanged"
            counts["unchanged"] += 1
            continue

        if not dry_run:
            update_category(db_manager, eid, new_category)
        entry["status"] = "would_update" if dry_run else "updated"
        counts["updated"] += 1
    return report


def main():
    parser = argparse.ArgumentParser(description="Change an entity's category in the database.")
    parser.add_argument("--book-id", type=int, required=True)
    parser.add_argument("--new-category", required=True)
    parser.add_argument("--untranslated", action="append", required=True,
                        help="Untranslated text (repeat for multiple entities).")
    parser.add_argument("--current-category",
                        help="If untranslated matches multiple categories, restrict to this one.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true",
                        help="Allow setting a category not in the book's template/in-use set.")
    args = parser.parse_args()

    config = TranslationConfig()
    logger = Logger(config)
    # strict_writes: a failed category update raises loudly instead of silently no-op'ing.
    db_manager = DatabaseManager(config, logger, strict_writes=True)

    report = change_entity_categories(
        db_manager, args.book_id, args.untranslated, args.new_category,
        current_category=args.current_category, force=args.force, dry_run=args.dry_run,
    )
    if not report["ok"]:
        print(f"Category {args.new_category!r} is not among known categories for book {args.book_id}:")
        for c in report["known_categories"]:
            print(f"  {c}")
        print("Pass --force to use it anyway.")
        sys.exit(1)

    print(f"Book ID:      {args.book_id}")
    print(f"New category: {args.new_category!r}")
    print(f"Mode:         {'DRY RUN' if args.dry_run else 'APPLY'}")
    print("=" * 70)

    for e in report["results"]:
        u = e["untranslated"]
        if e["status"] == "not_found":
            print(f"  ❌ NOT FOUND: {u!r}")
        elif e["status"] == "ambiguous":
            cats = e["categories"]
            print(f"  ⚠️  AMBIGUOUS ({len(cats)} categories: {cats}): {u!r} — pass --current-category")
        elif e["status"] == "unchanged":
            print(f"  ⏭️  UNCHANGED: {u!r} already in {args.new_category!r}")
        else:
            action = "WOULD UPDATE" if args.dry_run else "UPDATE"
            print(f"  ✅ {action}: {u!r}  [{e['old_category']}] → [{args.new_category}]  "
                  f"(translation: {e['translation']!r})")

    counts = report["counts"]
    print("=" * 70)
    print(f"Updated:    {counts['updated']}")
    print(f"Unchanged:  {counts['unchanged']}")
    print(f"Not found:  {counts['not_found']}")
    print(f"Ambiguous:  {counts['ambiguous']}")


if __name__ == "__main__":
    main()
