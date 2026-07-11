#!/usr/bin/env python3
"""
Backfill entities' origin_chapter by scanning chapter source text.

Entities added via the CLI / bulk_add_entity.py don't get origin_chapter set.
This walks a book's chapters in ascending order — saved (translated) chapters
plus, optionally, queued (not-yet-translated) chapters — and sets each entity's
origin_chapter to the lowest chapter_number whose untranslated source contains
the entity's untranslated string.

By default only entities with a NULL origin_chapter are touched; pass --all to
re-derive every entity (use with care — it will overwrite existing values with
the first textual appearance, which is usually but not always what you want).

--recompute is the safe form of --all, and the one to reach for on an existing
book. origin_chapter records when extraction *ran*, not when the entity first
appears, so entities routinely sit hundreds of chapters late (25% of book 8's,
worst case 219 chapters) and silently vanish from --origin-chapter range pulls.
--recompute only ever moves an origin EARLIER, never later: an entity cannot
originate after its first textual appearance, but it can legitimately predate
one (a renamed entity, or source since edited), so raising is never safe.
Single-character entities are skipped — a 1-char substring matches coincidentally
almost immediately and would be dragged to a bogus early chapter (--include-short
to override).

Matching mirrors the translation engine: NFC-normalized substring on the
untranslated source. Queued chapters are included by default because for books
mid-translation most entities first appear in still-queued chapters.

Usage:
    python3 backfill_origin_chapter.py --book-id 42 --dry-run
    python3 backfill_origin_chapter.py --book-id 42
    python3 backfill_origin_chapter.py --book-id 42 --recompute --dry-run
    python3 backfill_origin_chapter.py --book-id 42 --category incantations
    python3 backfill_origin_chapter.py --book-id 42 --no-queue
"""

import argparse
import json
import unicodedata

from config import TranslationConfig
from database import DatabaseManager
from logger import Logger


def _norm(text):
    return unicodedata.normalize('NFC', text)


def _content_to_text(raw):
    """Source content is stored as a JSON list of lines or a plain string."""
    if raw is None:
        return ""
    if isinstance(raw, list):
        return "\n".join(raw)
    try:
        loaded = json.loads(raw)
        if isinstance(loaded, list):
            return "\n".join(loaded)
        return str(loaded)
    except (ValueError, TypeError):
        return raw


def load_sources(db, book_id, include_queue):
    """Return {chapter_number: normalized_source_text}, lowest source wins on collisions."""
    conn = db.backend.get_connection()
    cursor = conn.cursor()
    sources = {}

    cursor.execute(
        'SELECT chapter_number, untranslated_content FROM chapters WHERE book_id = ?',
        (book_id,),
    )
    for row in cursor.fetchall():
        cn = row['chapter_number'] if isinstance(row, dict) else row[0]
        raw = row['untranslated_content'] if isinstance(row, dict) else row[1]
        if cn is None:
            continue
        sources[int(cn)] = _norm(_content_to_text(raw))

    if include_queue:
        cursor.execute(
            'SELECT chapter_number, content FROM queue WHERE book_id = ?',
            (book_id,),
        )
        for row in cursor.fetchall():
            cn = row['chapter_number'] if isinstance(row, dict) else row[0]
            raw = row['content'] if isinstance(row, dict) else row[1]
            if cn is None:
                continue
            cn = int(cn)
            # A saved chapter beats a queued one for the same number.
            if cn not in sources:
                sources[cn] = _norm(_content_to_text(raw))

    conn.close()
    return sources


def load_entities(db, book_id, category, only_missing):
    conn = db.backend.get_connection()
    cursor = conn.cursor()
    sql = ('SELECT id, untranslated, translation, category, origin_chapter '
           'FROM entities WHERE book_id = ?')
    params = [book_id]
    if category:
        sql += ' AND category = ?'
        params.append(category)
    if only_missing:
        sql += ' AND origin_chapter IS NULL'
    cursor.execute(sql, tuple(params))
    rows = cursor.fetchall()
    conn.close()
    out = []
    for r in rows:
        if isinstance(r, dict):
            out.append((r['id'], r['untranslated'], r['translation'],
                        r['category'], r['origin_chapter']))
        else:
            out.append((r[0], r[1], r[2], r[3], r[4]))
    return out


def set_origin_chapter(db, entity_id, chapter_number):
    conn = db.backend.get_connection()
    cursor = conn.cursor()
    cursor.execute(
        'UPDATE entities SET origin_chapter = ? WHERE id = ?',
        (chapter_number, entity_id),
    )
    conn.commit()
    conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Backfill entity origin_chapter by scanning chapter source text.")
    parser.add_argument("--book-id", type=int, required=True)
    parser.add_argument("--category", help="Only process entities in this category.")
    parser.add_argument("--all", action="store_true",
                        help="Re-derive every entity, not just those missing origin_chapter.")
    parser.add_argument("--recompute", action="store_true",
                        help="Re-derive every entity but only move an origin EARLIER, never "
                             "later (fixes entities filed late by extraction). Skips "
                             "single-character entities unless --include-short.")
    parser.add_argument("--include-short", action="store_true",
                        help="With --recompute, also recompute single-character entities "
                             "(risky: a 1-char substring matches coincidentally very early).")
    parser.add_argument("--no-queue", action="store_true",
                        help="Scan only saved/translated chapters, not the queue.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change without writing to the database.")
    args = parser.parse_args()

    config = TranslationConfig()
    logger = Logger(config)
    db = DatabaseManager(config, logger)

    include_queue = not args.no_queue
    sources = load_sources(db, args.book_id, include_queue)
    chapter_numbers = sorted(sources.keys())
    recompute = args.recompute
    entities = load_entities(db, args.book_id, args.category,
                             only_missing=not (args.all or recompute))

    skipped_short = []
    if recompute and not args.include_short:
        keep = [e for e in entities if len(e[1] or "") > 1]
        skipped_short = [e for e in entities if len(e[1] or "") <= 1]
        entities = keep

    print(f"Book ID:    {args.book_id}")
    print(f"Chapters:   {len(chapter_numbers)} scanned"
          f" ({chapter_numbers[0]}–{chapter_numbers[-1]})" if chapter_numbers else "Chapters:   none")
    print(f"Queue:      {'included' if include_queue else 'excluded'}")
    if recompute:
        scope = "recompute (only moves origins earlier)"
    elif args.all:
        scope = "all"
    else:
        scope = "missing origin_chapter only"
    print(f"Entities:   {len(entities)} ({scope}"
          f"{', category=' + args.category if args.category else ''})")
    if skipped_short:
        print(f"Skipped:    {len(skipped_short)} single-character entities "
              f"(--include-short to recompute them)")
    print(f"Mode:       {'DRY RUN' if args.dry_run else 'APPLY'}")
    print("=" * 70)

    # Walk chapters ascending; assign each pending entity the first chapter it
    # appears in, then drop it from the pending set. Stop early once all matched.
    pending = {eid: (unicodedata.normalize('NFC', untr), untr, trans, old)
               for (eid, untr, trans, cat, old) in entities}
    resolved = {}  # eid -> chapter_number

    for cn in chapter_numbers:
        if not pending:
            break
        text = sources[cn]
        found = [eid for eid, (key, _, _, _) in pending.items() if key in text]
        for eid in found:
            resolved[eid] = cn
            del pending[eid]

    n_set = 0
    n_changed = 0
    n_unmatched = 0
    n_kept_later = 0
    for (eid, untr, trans, cat, old) in entities:
        if eid in resolved:
            cn = resolved[eid]
            if old == cn:
                continue
            # --recompute never raises an origin: an entity cannot originate after
            # its first textual appearance, but it may legitimately predate one.
            if recompute and old is not None and cn >= old:
                n_kept_later += 1
                continue
            tag = "" if old is None else f" (was {old})"
            action = "WOULD SET" if args.dry_run else "SET"
            print(f"  ✅ {action} [{cat}] {untr!r} ({trans!r}) → ch{cn}{tag}")
            if not args.dry_run:
                set_origin_chapter(db, eid, cn)
            n_set += 1
            if old is not None:
                n_changed += 1
        elif not recompute:
            # Under --recompute most entities already have a sane origin; a miss just
            # means the string no longer appears verbatim, which is not news.
            print(f"  ⚠️  NO MATCH [{cat}] {untr!r} ({trans!r}) — not found in any scanned chapter")
            n_unmatched += 1
        else:
            n_unmatched += 1

    print("=" * 70)
    print(f"Set:         {n_set}" + (f" (of which {n_changed} overwrote an existing value)" if n_changed else ""))
    if recompute:
        print(f"Left alone:  {n_kept_later} (derived origin was not earlier than the recorded one)")
    print(f"Unmatched:   {n_unmatched}")


if __name__ == "__main__":
    main()
