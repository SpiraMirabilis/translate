#!/usr/bin/env python3
"""
Measure how much of a book's glossary carries a note where it matters — the
Phase 0 tool of the entity-note backfill (EntityNoteBackfill.md).

"Has a note" is the wrong question, because notes are point-in-time: a note is
resolved through notes_as_of, and a note stamped at ch700 does not exist for
any chapter before it. So each entity is judged at its TARGET chapter:

  convention  (every non-gendered category — terms, places, titles, sects,
               techniques …) → its FIRST indexed appearance. The note is
               timeless guidance and must be in force from the start.
  stateful    (the book's gendered categories — characters) → its LAST
               indexed appearance. Current state, stamped late so it can't
               spoil earlier chapters.

and gets one status:

  ok       a note is in force at the target chapter
  todo     no note there, and one can be stamped there
  blocked  no note there, but a revision already exists at a LATER chapter —
           a stamp at the target would sit behind it (notes_as_of rewinds to
           that revision's previous_note, so the stamp would not show and the
           live note would regress). Needs a different route.

Flags on a worklist row:
  quiet     last seen more than --quiet-window chapters before the head. The
            note_updates channel only reaches entities in chapters being
            translated now, so these will never heal on their own — do them first.
  1ch       single-character source form; the index is substring matching, so
            its chapter spread is inflated (楚 files itself under 清楚).
  origin>1st  origin_chapter is later than the first indexed appearance, and
            notes_as_of floors on origin_chapter — a note stamped at the first
            appearance stays invisible until origin_chapter is fixed.

Spread (distinct chapters in chapter_entities) is the importance measure. The
index is only as current as the glossary was when each chapter was saved, so
reindex first: python3 backfill_chapter_entities.py -b N

Read-only. Safe to run while the book translates.

Usage:
    python3 entity_note_coverage.py -b 14                      # summary + top of worklist
    python3 entity_note_coverage.py -b 14 --min-chapters 20 --limit 0
    python3 entity_note_coverage.py -b 14 --kind convention --quiet-only
    python3 entity_note_coverage.py -b 14 --status blocked
    python3 entity_note_coverage.py -b 14 --no-note --last-seen-before 600
    python3 entity_note_coverage.py -b 14 --format json > /tmp/b14_worklist.json
"""

import argparse
import json
import os
import sys
import warnings

warnings.filterwarnings("ignore", category=FutureWarning)
os.environ.pop("DEBUG", None)

BANDS = [(100, None), (50, 99), (20, 49), (10, 19), (5, 9), (2, 4), (1, 1), (0, 0)]
STATUSES = ("ok", "todo", "blocked")


def band_label(lo, hi):
    if hi is None:
        return f"{lo}+"
    if lo == hi:
        return "unseen" if lo == 0 else str(lo)
    return f"{lo}-{hi}"


def in_band(spread, lo, hi):
    return spread >= lo and (hi is None or spread <= hi)


def note_in_force(note, origin_chapter, revisions, chapter):
    """notes_as_of for one entity, from preloaded data.

    `revisions` is that entity's [(chapter_number, previous_note)] in id order.
    Must stay in step with db/entities_repo.py::notes_as_of."""
    value = note
    for rev_chapter, previous in revisions:
        if rev_chapter is not None and rev_chapter > chapter:
            value = previous
            break
    if value and origin_chapter is not None and origin_chapter > chapter:
        value = None
    return value or None


def compute_coverage(db, book_id, stateful_categories=None, quiet_window=50):
    """Per-entity coverage rows for one book, plus book-level facts.

    Returns {"head_chapter", "quiet_before", "unindexed_chapters",
             "stateful_categories", "rows": [...]}. Rows include unseen
    entities (spread 0), which have no target chapter and no status."""
    if stateful_categories is None:
        stateful_categories = db.get_book_gendered_categories(book_id)
    stateful = set(stateful_categories)

    with db._conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT MAX(chapter_number) FROM chapters WHERE book_id = ?",
                    (book_id,))
        head = (cur.fetchone() or [None])[0] or 0

        cur.execute(
            "SELECT COUNT(*) FROM chapters c WHERE c.book_id = ? AND NOT EXISTS "
            "(SELECT 1 FROM chapter_entities ce WHERE ce.chapter_id = c.id)",
            (book_id,))
        unindexed = cur.fetchone()[0]

        cur.execute(
            "SELECT ce.entity_id, COUNT(DISTINCT c.chapter_number), "
            "MIN(c.chapter_number), MAX(c.chapter_number), SUM(ce.occurrences) "
            "FROM chapter_entities ce JOIN chapters c ON c.id = ce.chapter_id "
            "WHERE c.book_id = ? GROUP BY ce.entity_id",
            (book_id,))
        spread = {r[0]: (r[1], r[2], r[3], int(r[4] or 0)) for r in cur.fetchall()}

        cur.execute(
            "SELECT id, category, untranslated, translation, note, origin_chapter "
            "FROM entities WHERE book_id = ?",
            (book_id,))
        entities = cur.fetchall()

        cur.execute(
            "SELECT entity_id, chapter_number, previous_note "
            "FROM entity_note_revisions WHERE book_id = ? ORDER BY id",
            (book_id,))
        revisions = {}
        for entity_id, chapter_number, previous in cur.fetchall():
            revisions.setdefault(entity_id, []).append((chapter_number, previous))

    quiet_before = head - quiet_window
    rows = []
    for entity_id, category, untranslated, translation, note, origin in entities:
        n, first, last, occ = spread.get(entity_id, (0, None, None, 0))
        kind = "stateful" if category in stateful else "convention"
        revs = revisions.get(entity_id, [])
        stamped = [c for c, _ in revs if c is not None]
        row = {
            "id": entity_id,
            "category": category,
            "untranslated": untranslated,
            "translation": translation,
            "kind": kind,
            "spread": n,
            "first_seen": first,
            "last_seen": last,
            "occurrences": occ,
            "origin_chapter": origin,
            "note": note or None,
            "revisions": len(revs),
            "latest_revision_chapter": max(stamped) if stamped else None,
            "target_chapter": None,
            "note_at_target": None,
            "status": None,
            "quiet": False,
            "single_char": len(untranslated or "") == 1,
            "origin_after_first": False,
        }
        if n:
            target = first if kind == "convention" else last
            at_target = note_in_force(note, origin, revs, target)
            if at_target:
                status = "ok"
            elif any(c > target for c in stamped):
                status = "blocked"
            else:
                status = "todo"
            row.update(
                target_chapter=target,
                note_at_target=at_target,
                status=status,
                quiet=last <= quiet_before,
                origin_after_first=origin is not None and origin > first,
            )
        rows.append(row)

    return {
        "head_chapter": head,
        "quiet_before": quiet_before,
        "unindexed_chapters": unindexed,
        "stateful_categories": sorted(stateful),
        "rows": rows,
    }


def band_table(rows):
    """The §1 table: per spread band, entity count / has-a-note / status split."""
    out = []
    for lo, hi in BANDS:
        sel = [r for r in rows if in_band(r["spread"], lo, hi)]
        if not sel:
            continue
        entry = {
            "band": band_label(lo, hi),
            "entities": len(sel),
            "with_note": sum(1 for r in sel if r["note"]),
        }
        for s in STATUSES:
            entry[s] = sum(1 for r in sel if r["status"] == s)
        entry["quiet_open"] = sum(1 for r in sel
                                  if r["quiet"] and r["status"] in ("todo", "blocked"))
        out.append(entry)
    return out


def kind_table(rows, min_chapters):
    out = []
    for kind in ("convention", "stateful"):
        sel = [r for r in rows if r["kind"] == kind and r["spread"] >= min_chapters]
        entry = {"kind": kind, "entities": len(sel)}
        for s in STATUSES:
            entry[s] = sum(1 for r in sel if r["status"] == s)
        entry["quiet_open"] = sum(1 for r in sel
                                  if r["quiet"] and r["status"] in ("todo", "blocked"))
        entry["origin_after_first"] = sum(
            1 for r in sel if r["origin_after_first"] and r["status"] != "ok")
        out.append(entry)
    return out


def worklist(rows, *, min_chapters=10, statuses=("todo", "blocked"), kind=None,
             categories=None, no_note=False, last_seen_before=None, quiet_only=False):
    """Filtered and ranked: gone-quiet first (the channel will never reach
    them), then by chapter spread, then occurrences."""
    sel = [r for r in rows
           if r["spread"] >= max(min_chapters, 1)
           and r["status"] in statuses
           and (kind is None or r["kind"] == kind)
           and (not categories or r["category"] in categories)
           and (not no_note or not r["note"])
           and (last_seen_before is None or r["last_seen"] < last_seen_before)
           and (not quiet_only or r["quiet"])]
    sel.sort(key=lambda r: (not r["quiet"], -r["spread"], -r["occurrences"], r["id"]))
    return sel


def _flags(r):
    flags = []
    if r["quiet"]:
        flags.append("quiet")
    if r["single_char"]:
        flags.append("1ch")
    if r["origin_after_first"]:
        flags.append(f"origin>1st(ch{r['origin_chapter']})")
    if r["status"] == "blocked":
        flags.append(f"rev@ch{r['latest_revision_chapter']}")
    return " ".join(flags)


def print_text(book, cov, bands, kinds, work, args, total_work):
    rows = cov["rows"]
    indexed = sum(1 for r in rows if r["spread"])
    print(f"Book {book['id']} — {book.get('title', '')}")
    print(f"  head ch{cov['head_chapter']}, {indexed:,} indexed entities "
          f"({len(rows) - indexed:,} never matched), quiet = last seen ≤ "
          f"ch{cov['quiet_before']} (window {args.quiet_window})")
    print(f"  stateful (stamp at last appearance): "
          f"{', '.join(cov['stateful_categories']) or '—'}; "
          f"everything else is a convention (stamp at first appearance)")
    if cov["unindexed_chapters"]:
        print(f"  ⚠ {cov['unindexed_chapters']} chapter(s) have no index rows — "
              f"run backfill_chapter_entities.py -b {book['id']} first")
    print()

    hdr = f"  {'appears in':>10}  {'entities':>8}  {'noted':>6}  {'ok':>6}  " \
          f"{'todo':>6}  {'blocked':>7}  {'quiet open':>10}"
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for b in bands:
        if b["band"] == "unseen":
            print(f"  {b['band']:>10}  {b['entities']:>8,}  {b['with_note']:>6,}")
            continue
        print(f"  {b['band']:>10}  {b['entities']:>8,}  {b['with_note']:>6,}  "
              f"{b['ok']:>6,}  {b['todo']:>6,}  {b['blocked']:>7,}  {b['quiet_open']:>10,}")
    print()
    print(f"  spread ≥ {args.min_chapters}:")
    for k in kinds:
        extra = (f", {k['origin_after_first']} hidden by origin_chapter"
                 if k["origin_after_first"] else "")
        print(f"    {k['kind']:<10} {k['entities']:>5,} entities — ok {k['ok']:,}, "
              f"todo {k['todo']:,}, blocked {k['blocked']:,} "
              f"({k['quiet_open']:,} of the open ones gone quiet){extra}")

    if args.summary_only:
        return
    print()
    shown = f"top {len(work)} of {total_work}" if len(work) < total_work else f"{total_work}"
    print(f"  Worklist ({shown}; quiet first, then spread):")
    if not work:
        print("    (nothing)")
        return
    for r in work:
        span = f"ch{r['first_seen']}-{r['last_seen']}"
        print(f"    {r['status']:<7} {r['spread']:>4}ch {span:<13} x{r['occurrences']:<5} "
              f"[{r['category']}] {r['untranslated']} → {r['translation']}"
              f"  @ch{r['target_chapter']}  {_flags(r)}".rstrip())
        if args.show_notes and r["note"]:
            print(f"        current note: {r['note']}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-b", "--book", required=True, help="Book id or title")
    ap.add_argument("--min-chapters", type=int, default=10,
                    help="Worklist/kind-table spread floor (default 10)")
    ap.add_argument("--quiet-window", type=int, default=50,
                    help="Chapters behind the head that count as gone quiet (default 50)")
    ap.add_argument("--status", choices=("open", "todo", "blocked", "ok", "all"),
                    default="open", help="Worklist statuses (open = todo + blocked)")
    ap.add_argument("--kind", choices=("convention", "stateful"))
    ap.add_argument("--category", action="append",
                    help="Restrict worklist to a category (repeatable)")
    ap.add_argument("--stateful-category", action="append",
                    help="Treat this category as stateful (repeatable; replaces the "
                         "default of the book's gendered categories)")
    ap.add_argument("--no-note", action="store_true",
                    help="Only entities with no current note at all")
    ap.add_argument("--last-seen-before", type=int, metavar="N",
                    help="Only entities last seen before chapter N")
    ap.add_argument("--quiet-only", action="store_true",
                    help="Only gone-quiet entities")
    ap.add_argument("--limit", type=int, default=40, help="Worklist rows (0 = all)")
    ap.add_argument("--summary-only", action="store_true")
    ap.add_argument("--show-notes", action="store_true",
                    help="Print each worklist entity's current note")
    ap.add_argument("--format", choices=("text", "json"), default="text")
    args = ap.parse_args()

    from config import TranslationConfig
    from database import DatabaseManager
    from logger import Logger
    from get_entities import resolve_book

    config = TranslationConfig()
    db = DatabaseManager(config, Logger(config))
    book = resolve_book(db, args.book)
    if not book:
        sys.exit(f"Book not found: {args.book}")

    cov = compute_coverage(db, book["id"], stateful_categories=args.stateful_category,
                           quiet_window=args.quiet_window)
    statuses = {"open": ("todo", "blocked"), "all": STATUSES}.get(args.status, (args.status,))
    work = worklist(cov["rows"], min_chapters=args.min_chapters, statuses=statuses,
                    kind=args.kind, categories=args.category, no_note=args.no_note,
                    last_seen_before=args.last_seen_before, quiet_only=args.quiet_only)
    total_work = len(work)
    if args.limit:
        work = work[:args.limit]
    bands = band_table(cov["rows"])
    kinds = kind_table(cov["rows"], args.min_chapters)

    if args.format == "json":
        json.dump({
            "book_id": book["id"],
            "title": book.get("title"),
            "head_chapter": cov["head_chapter"],
            "quiet_before": cov["quiet_before"],
            "unindexed_chapters": cov["unindexed_chapters"],
            "stateful_categories": cov["stateful_categories"],
            "bands": bands,
            "kinds": kinds,
            "worklist_total": total_work,
            "worklist": work,
        }, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        print_text(book, cov, bands, kinds, work, args, total_work)


if __name__ == "__main__":
    main()
