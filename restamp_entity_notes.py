#!/usr/bin/env python3
"""
Move a note's history earlier so it is in force at an entity's first appearance
— the "blocked" case of the entity-note backfill (EntityNoteBackfill.md §8).

A convention note stamped late (e.g. at ch709 by a chapter pass or the
note_updates channel) exists only from that chapter on: notes_as_of rewinds to
the previous_note of the earliest revision after chapter N, and a creation
row's previous_note is NULL. Adding another revision cannot fix that — the late
row stays the earliest one after N. The history itself has to change, which is
what this does, in one transaction per entity:

  move   (plan entry without "early_note")
         The note is already free of later facts. Its creation row is
         re-stamped at `chapter`; text, author, reason and created_at are kept.

  split  (plan entry with "early_note")
         The note carries facts later than `chapter`. A creation row at
         `chapter` gets early_note, and the existing history follows it
         unchanged, except that its first row's previous_note becomes early_note
         instead of NULL. Rows are re-inserted in order, so ids stay ascending
         (note_revisions.py and the Entities panel read by id).

entities.note is never touched: the current note stays what it was, only its
past changes. That is also why this lives outside set_entity_note — every row
it writes is one set_entity_note would have produced had the notes been
written in chapter order.

Guards (the entity is skipped, with the reason):
  * the entity must belong to the book and have a revision history whose
    earliest row is a creation (previous_note NULL) — an older history that
    predates the revision log cannot be moved safely;
  * `chapter` must be earlier than every existing revision's chapter
    (invariant 3: a given entity's stamps stay in ascending chapter order);
  * `chapter` must be >= origin_chapter, or notes_as_of's origin floor would
    hide the stamp anyway — fix origin first (backfill_origin_chapter.py);
  * early_note must be non-empty, <= 500 chars, and differ from the first row.

Invariant 4 (only <=N facts in a note stamped at N) is on the author of the
plan: a move is only right for a note with nothing later in it.

  stamps (plan entry with "stamps": [{"chapter": c, "note": "…"}, …])
         The multi-stamp form of split: several notes at ascending chapters,
         all before the existing history, each one's previous_note the one
         before it, and the existing first row's previous_note the last of
         them. For a main character's state at real turning points.
         "replace_legacy": true also accepts a history whose first row is not
         a creation — a note that predates the revision log. That legacy text
         (the first row's previous_note) is what every earlier chapter sees,
         and the stamps replace it; it is printed so it stays on record.

Plan file: JSON list of
    {"untranslated": "宗主", "chapter": 3, "early_note": "…"}   # split
    {"untranslated": "秀才", "chapter": 24}                       # move
    {"untranslated": "萧墨", "stamps": [{"chapter": 1, "note": "…"},
                                       {"chapter": 120, "note": "…"}]}
    {"untranslated": "萧墨", "insert": [{"chapter": 420, "note": "…"}]}  # fill a gap
("category" optional, to disambiguate.)

  insert (plan entry with "insert": [{"chapter": c, "note": "…"}, …])
         Stamps strictly BETWEEN two existing chapter-stamped revisions, for a
         long stretch where the character keeps appearing under a stale note.
         The neighbours are relinked so the previous_note chain stays intact.

Usage:
    python3 restamp_entity_notes.py -b 14 --plan plan.json            # dry run
    python3 restamp_entity_notes.py -b 14 --plan plan.json --apply
"""

import argparse
import json
import os
import sys
import warnings

warnings.filterwarnings("ignore", category=FutureWarning)
os.environ.pop("DEBUG", None)

MAX_NOTE = 500
REASON_SPLIT = "note backfill: first-appearance stamp"
REASON_INSERT = "note backfill: milestone stamp (gap fill)"

COLS = ("id", "entity_id", "book_id", "previous_note", "new_note", "author",
        "chapter_number", "reason", "shrink", "created_at")


def _load_history(cur, entity_id):
    cur.execute(f"SELECT {', '.join(COLS)} FROM entity_note_revisions "
                "WHERE entity_id = ? ORDER BY id", (entity_id,))
    return [dict(zip(COLS, r)) for r in cur.fetchall()]


def check_entry(db, book_id, entry):
    """Resolve and validate one plan entry. Returns (plan, None) or (None, why)."""
    key = entry.get("untranslated")
    stamps = entry.get("stamps")
    if stamps is not None:
        return _check_stamps(db, book_id, entry)
    if entry.get("insert") is not None:
        return _check_insert(db, book_id, entry)
    chapter = entry.get("chapter")
    early = entry.get("early_note")
    if not key or not isinstance(chapter, int):
        return None, "entry needs 'untranslated' and an integer 'chapter'"
    entity_id = db.get_entity_id(book_id, key, entry.get("category"))
    if not entity_id:
        return None, "not an entity of this book"
    with db._conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT origin_chapter, note FROM entities WHERE id = ?", (entity_id,))
        origin, current = cur.fetchone()
        history = _load_history(cur, entity_id)
    if not history:
        return None, "no revision history (nothing to move)"
    first = history[0]
    if first["previous_note"] is not None:
        return None, "earliest revision is not a creation — history predates the log"
    stamped = [r["chapter_number"] for r in history if r["chapter_number"] is not None]
    if not stamped:
        return None, "no chapter-stamped revision — the note is already in force everywhere"
    if chapter >= min(stamped):
        return None, f"ch{chapter} is not earlier than the first revision (ch{min(stamped)})"
    if origin is not None and chapter < origin:
        return None, f"ch{chapter} is before origin_chapter ch{origin} — the floor would hide it"
    if early is not None:
        early = early.strip()
        if not early:
            return None, "early_note is empty"
        if len(early) > MAX_NOTE:
            return None, f"early_note is {len(early)} chars (cap {MAX_NOTE})"
        if early == (first["new_note"] or "").strip():
            return None, "early_note equals the first revision's text — use a move"
    elif first["chapter_number"] is None:
        return None, "first revision is unstamped; a move would give it a chapter it never had"
    return {"key": key, "entity_id": entity_id, "chapter": chapter, "early_note": early,
            "history": history, "current": current, "origin": origin}, None


def _check_stamps(db, book_id, entry):
    key = entry.get("untranslated")
    stamps = entry.get("stamps") or []
    if not stamps:
        return None, "empty stamps list"
    entity_id = db.get_entity_id(book_id, key, entry.get("category"))
    if not entity_id:
        return None, "not an entity of this book"
    with db._conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT origin_chapter, note FROM entities WHERE id = ?", (entity_id,))
        origin, current = cur.fetchone()
        history = _load_history(cur, entity_id)
    if not history:
        return None, "no revision history to stack the stamps in front of"
    legacy = history[0]["previous_note"]
    if legacy is not None and not entry.get("replace_legacy"):
        return None, ("earliest revision is not a creation — history predates the log "
                      "(set \"replace_legacy\": true to put the stamps in front of it)")
    stamped = [r["chapter_number"] for r in history if r["chapter_number"] is not None]
    if not stamped:
        return None, "no chapter-stamped revision — the note is already in force everywhere"
    chapters = [s.get("chapter") for s in stamps]
    if not all(isinstance(c, int) for c in chapters):
        return None, "every stamp needs an integer chapter"
    if chapters != sorted(set(chapters)):
        return None, f"stamp chapters must be strictly ascending: {chapters}"
    if chapters[-1] >= min(stamped):
        return None, f"last stamp ch{chapters[-1]} is not before the first revision (ch{min(stamped)})"
    if origin is not None and chapters[0] < origin:
        return None, f"first stamp ch{chapters[0]} is before origin_chapter ch{origin}"
    notes = []
    for s in stamps:
        n = (s.get("note") or "").strip()
        if not n or len(n) > MAX_NOTE:
            return None, f"stamp at ch{s['chapter']}: note empty or over {MAX_NOTE} chars"
        notes.append(n)
    if any(a == b for a, b in zip(notes, notes[1:])) or notes[-1] == (history[0]["new_note"] or "").strip():
        return None, "two consecutive stamps carry the same text"
    return {"key": key, "entity_id": entity_id, "chapter": chapters[0],
            "stamps": list(zip(chapters, notes)), "early_note": notes[-1],
            "history": history, "current": current, "origin": origin,
            "discarded_legacy": legacy}, None


def _check_insert(db, book_id, entry):
    """Stamps BETWEEN existing chapter-stamped revisions — filling a gap in a
    history that already has early and late stamps. Each new row takes the note
    of the row before it as previous_note, and the row after it now points back
    at the new one, so the chain stays a chain and notes_as_of rewinds through
    it correctly. Never before the first stamped row (that is "stamps"), never
    after the last (that would change the current note), never across a
    chapter-less row (a hand edit or sweep that belongs to the present)."""
    key = entry.get("untranslated")
    inserts = entry.get("insert") or []
    if not inserts:
        return None, "empty insert list"
    entity_id = db.get_entity_id(book_id, key, entry.get("category"))
    if not entity_id:
        return None, "not an entity of this book"
    with db._conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT origin_chapter, note FROM entities WHERE id = ?", (entity_id,))
        origin, current = cur.fetchone()
        history = _load_history(cur, entity_id)
    stamped = [r["chapter_number"] for r in history if r["chapter_number"] is not None]
    if len(stamped) < 2:
        return None, "needs at least two chapter-stamped revisions to insert between"
    if stamped != sorted(stamped):
        return None, f"existing stamped revisions are not in chapter order: {stamped}"
    rows = [dict(r) for r in history]
    done = []
    for ins in sorted(inserts, key=lambda x: x.get("chapter") or 0):
        c, note = ins.get("chapter"), (ins.get("note") or "").strip()
        if not isinstance(c, int):
            return None, "every insert needs an integer chapter"
        if not note or len(note) > MAX_NOTE:
            return None, f"insert at ch{c}: note empty or over {MAX_NOTE} chars"
        pos = next((i for i in range(1, len(rows))
                    if rows[i - 1]["chapter_number"] is not None
                    and rows[i]["chapter_number"] is not None
                    and rows[i - 1]["chapter_number"] < c < rows[i]["chapter_number"]), None)
        if pos is None:
            return None, (f"ch{c} is not strictly between two consecutive chapter-stamped "
                          f"revisions (have {[r['chapter_number'] for r in rows]})")
        before, after = rows[pos - 1], rows[pos]
        if note in ((before["new_note"] or "").strip(), (after["new_note"] or "").strip()):
            return None, f"insert at ch{c} repeats a neighbouring note"
        new = dict(after, previous_note=before["new_note"], new_note=note, author="script",
                   chapter_number=c, reason=entry.get("reason") or REASON_INSERT, shrink=0)
        rows.insert(pos, new)
        rows[pos + 1] = dict(after, previous_note=note)
        done.append((c, note, before["new_note"]))
    return {"key": key, "entity_id": entity_id, "chapter": done[0][0], "early_note": None,
            "inserts": done, "rows": rows, "history": history, "current": current,
            "origin": origin}, None


def apply_entry(db, p):
    history = p["history"]
    with db._conn() as conn:
        cur = conn.cursor()
        if p.get("rows") is not None:          # insert: rewrite the whole chain in order
            cur.execute("DELETE FROM entity_note_revisions WHERE entity_id = ?",
                        (p["entity_id"],))
            for r in p["rows"]:
                cur.execute(
                    "INSERT INTO entity_note_revisions (entity_id, book_id, previous_note, "
                    "new_note, author, chapter_number, reason, shrink, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    tuple(r[c] for c in COLS[1:]))
            return
        if p["early_note"] is None:
            cur.execute("UPDATE entity_note_revisions SET chapter_number = ? WHERE id = ?",
                        (p["chapter"], history[0]["id"]))
            return
        cur.execute("DELETE FROM entity_note_revisions WHERE entity_id = ?", (p["entity_id"],))
        first = history[0]
        stamps = p.get("stamps") or [(p["chapter"], p["early_note"])]
        rows, prev = [], None
        for ch, note in stamps:
            rows.append(dict(first, previous_note=prev, new_note=note, author="script",
                             chapter_number=ch, reason=p.get("reason") or REASON_SPLIT,
                             shrink=0))
            prev = note
        rows.append(dict(first, previous_note=p["early_note"]))
        rows.extend(history[1:])
        for r in rows:
            cur.execute(
                "INSERT INTO entity_note_revisions (entity_id, book_id, previous_note, "
                "new_note, author, chapter_number, reason, shrink, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(r[c] for c in COLS[1:]))


def _verify_insert(db, book_id, p):
    problems = []
    eid = p["entity_id"]
    for c, note, prior in p["inserts"]:
        got = db.notes_as_of(book_id, c, entity_ids=[eid]).get(eid)
        if got != note:
            problems.append(f"at ch{c}: {got!r:.60}")
        was = db.notes_as_of(book_id, c - 1, entity_ids=[eid]).get(eid)
        if was == note:
            problems.append(f"at ch{c - 1}: the insert already shows")
    with db._conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT note FROM entities WHERE id = ?", (eid,))
        if cur.fetchone()[0] != p["current"]:
            problems.append("current note changed")
        cur.execute("SELECT chapter_number FROM entity_note_revisions WHERE entity_id = ? "
                    "AND chapter_number IS NOT NULL ORDER BY id", (eid,))
        chs = [r[0] for r in cur.fetchall()]
        if chs != sorted(chs):
            problems.append(f"stamped revisions out of order: {chs}")
    return problems


def verify(db, book_id, p):
    if p.get("inserts"):
        return _verify_insert(db, book_id, p)
    """notes_as_of at the stamp, just before the old first revision, and now."""
    old_first = min(r["chapter_number"] for r in p["history"]
                    if r["chapter_number"] is not None)
    expect_early = (p["stamps"][0][1] if p.get("stamps")
                    else p["early_note"] or p["history"][0]["new_note"])
    problems = []
    for ch, note in p.get("stamps") or []:
        got = db.notes_as_of(book_id, ch, entity_ids=[p["entity_id"]]).get(p["entity_id"])
        if got != note:
            problems.append(f"at stamp ch{ch}: {got!r:.60}")
    at = db.notes_as_of(book_id, p["chapter"], entity_ids=[p["entity_id"]]).get(p["entity_id"])
    if at != expect_early:
        problems.append(f"at ch{p['chapter']}: {at!r}")
    before = db.notes_as_of(book_id, old_first - 1, entity_ids=[p["entity_id"]]).get(p["entity_id"])
    expect_before = p["early_note"] if p.get("stamps") else expect_early
    if before != expect_before and len(p["history"]) == 1:
        problems.append(f"at ch{old_first - 1}: {before!r}")
    if p["chapter"] > 1 and (p["origin"] or 0) < p["chapter"]:
        prior = db.notes_as_of(book_id, p["chapter"] - 1,
                               entity_ids=[p["entity_id"]]).get(p["entity_id"])
        if prior is not None:
            problems.append(f"at ch{p['chapter'] - 1} (before the stamp): {prior!r}")
    with db._conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT note FROM entities WHERE id = ?", (p["entity_id"],))
        if cur.fetchone()[0] != p["current"]:
            problems.append("current note changed")
    return problems


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-b", "--book-id", type=int, required=True)
    ap.add_argument("--plan", required=True, help="JSON plan file")
    ap.add_argument("--apply", action="store_true", help="Write (default is a dry run)")
    args = ap.parse_args()

    from config import TranslationConfig
    from database import DatabaseManager
    from logger import Logger

    config = TranslationConfig()
    db = DatabaseManager(config, Logger(config))
    plan = json.load(open(args.plan, encoding="utf-8"))

    ok, skipped, failed = 0, 0, 0
    for entry in plan:
        p, why = check_entry(db, args.book_id, entry)
        label = entry.get("untranslated")
        if not p:
            skipped += 1
            print(f"  SKIP  {label}: {why}")
            continue
        old = [r["chapter_number"] for r in p["history"]]
        mode = ("insert" if p.get("inserts") else "stamps" if p.get("stamps")
                else "split" if p["early_note"] else "move ")
        new = ([c for c, _, _ in p["inserts"]] if p.get("inserts")
               else [c for c, _ in p["stamps"]] if p.get("stamps") else [p["chapter"]])
        print(f"  {mode} {label}: revisions at {old} ← new stamps at {new}")
        if p.get("discarded_legacy"):
            print(f"        replaces the pre-log legacy note: {p['discarded_legacy']!r}")
        if not args.apply:
            ok += 1
            continue
        apply_entry(db, p)
        problems = verify(db, args.book_id, p)
        if problems:
            failed += 1
            print(f"        ⚠ VERIFY FAILED: {'; '.join(problems)}")
        else:
            ok += 1
    verb = "restamped" if args.apply else "would restamp"
    print(f"\n{verb} {ok}, skipped {skipped}" + (f", verify failures {failed}" if failed else ""))
    if not args.apply:
        print("(dry run — pass --apply to write)")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
