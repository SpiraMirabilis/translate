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

Longer keys can collide just as badly: a 2-char name that is also half of a
reduplication or a chengyu (花花 in 白花花, 小黑 in 小黑屋, 浩浩 in 浩浩蕩蕩) gets
dragged to the coincidence every run, undoing any manual correction. Record those
per book in backfill_origin_exclusions.json — {"<book_id>": ["花花", "小黑"]} — and
--recompute will leave them alone. --skip-key adds one for a single run.

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
import os
import unicodedata
from dataclasses import dataclass, field

from config import TranslationConfig
from database import DatabaseManager
from logger import Logger

EXCLUSIONS_FILE = "backfill_origin_exclusions.json"


def load_exclusions(book_id, extra=(), warnings=None):
    """Entity keys --recompute must not re-derive for this book.

    A key here matches coincidentally inside a longer word, so recomputing drags
    it to a bogus early chapter and silently reverts any hand-set origin.

    An unreadable exclusions file is not fatal: the problem is appended to
    `warnings` (a list) when one is given, printed otherwise.
    """
    keys = set(extra)
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), EXCLUSIONS_FILE)
        with open(path, encoding="utf-8") as fh:
            keys |= set(json.load(fh).get(str(book_id), []))
    except FileNotFoundError:
        pass
    except (ValueError, OSError) as exc:
        msg = f"WARNING: could not read {EXCLUSIONS_FILE}: {exc}"
        if warnings is None:
            print(msg)
        else:
            warnings.append(msg)
    return {unicodedata.normalize("NFC", k) for k in keys}


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


def _entity_row(entity):
    eid, untr, trans, cat, old = entity
    return {"entity_id": eid, "untranslated": untr, "translation": trans,
            "category": cat, "old": old}


@dataclass
class BackfillPlan:
    """What a backfill run would do. Built by compute_origin_backfill; nothing
    is written until apply_origin_backfill(db, plan).

    changes           [{entity_id, untranslated, translation, category, old, new}]
                      in scan order; old is None for a first-time set.
    unmatched         [{entity_id, untranslated, translation, category, old}] —
                      key not found in any scanned chapter.
    kept_later        (recompute only) [{..., old, derived}] where the derived
                      origin was not earlier than the recorded one, so left alone.
    skipped_short     (recompute without include_short) single-character
                      entities not considered, [{..., old}].
    skipped_excluded  (recompute only) entities whose key is in the exclusions
                      file or skip_keys, [{..., old}].
    entities_considered  how many entities were matched against the text.
    chapters_scanned, first_chapter, last_chapter  the source chapters walked.
    scope             "recompute" | "all" | "missing".
    warnings          non-fatal problems (an unreadable exclusions file).
    """
    book_id: int
    category: object = None
    scope: str = "missing"
    include_queue: bool = True
    chapters_scanned: int = 0
    first_chapter: object = None
    last_chapter: object = None
    entities_considered: int = 0
    changes: list = field(default_factory=list)
    unmatched: list = field(default_factory=list)
    kept_later: list = field(default_factory=list)
    skipped_short: list = field(default_factory=list)
    skipped_excluded: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    # changes and unmatched interleaved in scan order, as ("set"|"unmatched", row);
    # lets main() print them in the order it always has.
    ordered: list = field(default_factory=list, repr=False)

    @property
    def overwrites(self):
        """How many changes replace an existing (non-NULL) origin_chapter."""
        return sum(1 for c in self.changes if c["old"] is not None)


def compute_origin_backfill(db, book_id, *, category=None, recompute=False,
                            all_=False, include_short=False, skip_keys=None,
                            include_queue=True):
    """Work out each entity's first textual appearance; write nothing.

    Mirrors the CLI flags:
      category       only entities in this category (--category)
      recompute      consider every entity, but only ever move an origin
                     EARLIER (--recompute); skips single-character keys unless
                     include_short, and keys excluded for the book
      all_           consider every entity and overwrite in either direction
                     (--all); without all_/recompute only NULL origins are set
      include_short  with recompute, also consider 1-char keys (--include-short)
      skip_keys      extra keys to exclude, IN ADDITION to the book's entries in
                     backfill_origin_exclusions.json (--skip-key). Like the file,
                     they only apply under recompute.
      include_queue  also scan queued (untranslated) chapters; the CLI default
                     (--no-queue turns it off)
    Only book-scoped entities (book_id = N) are considered, never globals.
    """
    plan = BackfillPlan(book_id=book_id, category=category,
                        include_queue=include_queue,
                        scope="recompute" if recompute else ("all" if all_ else "missing"))

    sources = load_sources(db, book_id, include_queue)
    chapter_numbers = sorted(sources.keys())
    plan.chapters_scanned = len(chapter_numbers)
    if chapter_numbers:
        plan.first_chapter, plan.last_chapter = chapter_numbers[0], chapter_numbers[-1]

    entities = load_entities(db, book_id, category,
                             only_missing=not (all_ or recompute))

    if recompute and not include_short:
        keep = [e for e in entities if len(e[1] or "") > 1]
        plan.skipped_short = [_entity_row(e) for e in entities if len(e[1] or "") <= 1]
        entities = keep
    if recompute:
        excluded = load_exclusions(book_id, skip_keys or (), warnings=plan.warnings)
        if excluded:
            keep = [e for e in entities
                    if unicodedata.normalize("NFC", e[1] or "") not in excluded]
            plan.skipped_excluded = [_entity_row(e) for e in entities
                                     if unicodedata.normalize("NFC", e[1] or "") in excluded]
            entities = keep
    plan.entities_considered = len(entities)

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

    for entity in entities:
        eid, untr, trans, cat, old = entity
        if eid in resolved:
            cn = resolved[eid]
            if old == cn:
                continue
            # recompute never raises an origin: an entity cannot originate after
            # its first textual appearance, but it may legitimately predate one.
            if recompute and old is not None and cn >= old:
                plan.kept_later.append(dict(_entity_row(entity), derived=cn))
                continue
            row = dict(_entity_row(entity), new=cn)
            plan.changes.append(row)
            plan.ordered.append(("set", row))
        else:
            row = _entity_row(entity)
            plan.unmatched.append(row)
            plan.ordered.append(("unmatched", row))

    return plan


def apply_origin_backfill(db, plan):
    """Write every change in `plan`; returns how many origins were set."""
    n = 0
    for change in plan.changes:
        set_origin_chapter(db, change["entity_id"], change["new"])
        n += 1
    return n


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
    parser.add_argument("--skip-key", action="append", default=[], metavar="KEY",
                        help="Entity key --recompute must not re-derive (repeatable); "
                             f"permanent entries live in {EXCLUSIONS_FILE}.")
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

    recompute = args.recompute
    plan = compute_origin_backfill(
        db, args.book_id, category=args.category, recompute=recompute,
        all_=args.all, include_short=args.include_short,
        skip_keys=args.skip_key, include_queue=not args.no_queue)
    for msg in plan.warnings:
        print(msg)

    print(f"Book ID:    {args.book_id}")
    print(f"Chapters:   {plan.chapters_scanned} scanned"
          f" ({plan.first_chapter}–{plan.last_chapter})" if plan.chapters_scanned
          else "Chapters:   none")
    print(f"Queue:      {'included' if plan.include_queue else 'excluded'}")
    if recompute:
        scope = "recompute (only moves origins earlier)"
    elif args.all:
        scope = "all"
    else:
        scope = "missing origin_chapter only"
    print(f"Entities:   {plan.entities_considered} ({scope}"
          f"{', category=' + args.category if args.category else ''})")
    if plan.skipped_short:
        print(f"Skipped:    {len(plan.skipped_short)} single-character entities "
              f"(--include-short to recompute them)")
    if plan.skipped_excluded:
        names = ", ".join(e["untranslated"] for e in plan.skipped_excluded[:8])
        print(f"Excluded:   {len(plan.skipped_excluded)} known coincidental keys ({names}"
              f"{'…' if len(plan.skipped_excluded) > 8 else ''})")
    print(f"Mode:       {'DRY RUN' if args.dry_run else 'APPLY'}")
    print("=" * 70)

    action = "WOULD SET" if args.dry_run else "SET"
    for kind, row in plan.ordered:
        untr, trans, cat = row["untranslated"], row["translation"], row["category"]
        if kind == "set":
            old = row["old"]
            tag = "" if old is None else f" (was {old})"
            print(f"  ✅ {action} [{cat}] {untr!r} ({trans!r}) → ch{row['new']}{tag}")
        elif not recompute:
            # Under --recompute most entities already have a sane origin; a miss just
            # means the string no longer appears verbatim, which is not news.
            print(f"  ⚠️  NO MATCH [{cat}] {untr!r} ({trans!r}) — not found in any scanned chapter")

    if not args.dry_run:
        apply_origin_backfill(db, plan)

    n_changed = plan.overwrites
    print("=" * 70)
    print(f"Set:         {len(plan.changes)}" + (f" (of which {n_changed} overwrote an existing value)" if n_changed else ""))
    if recompute:
        print(f"Left alone:  {len(plan.kept_later)} (derived origin was not earlier than the recorded one)")
    print(f"Unmatched:   {len(plan.unmatched)}")


if __name__ == "__main__":
    main()
