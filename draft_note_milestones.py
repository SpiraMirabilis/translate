#!/usr/bin/env python3
"""
Draft earlier note stamps for a book's main characters — the multi-stamp pass
of the entity-note backfill (EntityNoteBackfill.md §2, "one note is the floor,
not the target, for the top ~30 characters").

A character with one note stamped at their last appearance has no note at all
before it: a reader at ch200, or a retranslation of ch200, gets nothing. This
drafts 2-4 stamps at real turning points ahead of the existing history, and
writes them as a restamp_entity_notes.py plan (the "stamps" form), which
rebuilds the history in chapter order. Review the plan, then apply it with
restamp_entity_notes.py.

Two model steps per character, so a stamp cannot carry a later fact
(invariant 4) — the model is never shown one:

  pick   All chapter summaries of the character's appearances BEFORE their
         existing history starts → 2-4 turning-point chapters. The first
         appearance is always one of them.
  draft  One call per stamp, in chapter order, with only the summaries up to
         that stamp (first appearance + the last 12 appearances) and the prose
         at it, plus the previous stamp's note to update rather than restart.

Selection: the book's stateful entities with a note, ranked by chapter spread,
skipping pointer notes ("Short form of…", "Address form…"), single-character
keys, --exclude keys, and characters whose existing history starts too close to
their first appearance to leave room (--min-room chapters).

Usage:
    python3 draft_note_milestones.py -b 14 --top 30 --out plan.json
    python3 draft_note_milestones.py -b 14 --top 30 --out plan.json --exclude 老爷 --exclude 萧公子
    python3 restamp_entity_notes.py -b 14 --plan plan.json            # dry run
    python3 restamp_entity_notes.py -b 14 --plan plan.json --apply
In the plan, delete a stamp or an entry to drop it, or edit a note in place.
"""

import argparse
import json
import os
import re
import sys
import warnings
from concurrent.futures import ThreadPoolExecutor

warnings.filterwarnings("ignore", category=FutureWarning)
os.environ.pop("DEBUG", None)

from draft_entity_notes import (DEFAULT_MODEL, STATE_RADIUS, STATEFUL_PROMPT, _nfc,
                                _windows, call_model)

POINTER_RE = re.compile(r"^(Short form of|Address form|Affectionate address|Title \(|"
                        r"Childhood (name|nickname)|Address \()")
NOTE_CAP = 450
MAX_PICK_SUMMARIES = 400
RECENT = 12
REASON = "note backfill: character milestone stamp"

PICK_PROMPT = """You help maintain a glossary for a Chinese→English web-novel translation.

You get one character and chronological summaries of the chapters they appear in. Choose the chapters at which the character's glossary note should be (re)written — points after which a translator working on later chapters needs to know something new about them.

Pick 2-4 chapters (1-2 if the list spans fewer than ~150 chapters):
- ALWAYS include the first chapter listed — the note that introduces the character.
- Then only real turning points: a new life, timeline or world begins for them; a time skip; a cultivation breakthrough; a change of rank, office, title or allegiance; a marriage; a death; a revealed identity or new name.
- Prefer the chapter where the change happens, not the one after.
- Space them out; do not pick two chapters close together.
- COVER THE WHOLE SPAN. A note stays in force until the next one, so a long gap before the last listed chapter leaves an out-of-date note in force for all of it. When the list spans more than ~300 chapters, pick up to 6 and put at least one in each third of the span.

Choose only from the chapter numbers listed.

Return ONLY JSON: {"stamps": [{"chapter": <number>, "change": "one line: what changes here"}]}"""

MILESTONE_PROMPT = STATEFUL_PROMPT.replace(
    "Each note is stamped at the character's LAST appearance in the book so far and describes them as of then.",
    "Each note is stamped at a turning point in the character's story and describes them AS OF THAT CHAPTER. "
    "You may be given the note from the previous turning point: keep what still holds, update what has changed, "
    "and drop what is no longer true.")
assert MILESTONE_PROMPT != STATEFUL_PROMPT, "STATEFUL_PROMPT wording changed; update MILESTONE_PROMPT"


def select(db, book_id, top, exclude, min_room, only=None, replace_legacy=False):
    from entity_note_coverage import compute_coverage
    cov = compute_coverage(db, book_id)
    rows = [r for r in cov["rows"]
            if r["kind"] == "stateful" and r["spread"] and r["note"]
            and not r["single_char"] and r["untranslated"] not in exclude
            and (not only or r["untranslated"] in only)
            and not POINTER_RE.match(r["note"])]
    rows.sort(key=lambda r: (-r["spread"], r["id"]))
    picked, skipped = [], []
    with db._conn() as conn:
        cur = conn.cursor()
        for r in rows:
            cur.execute("SELECT MIN(chapter_number), COUNT(*), "
                        "SUM(previous_note IS NULL AND id = (SELECT MIN(id) FROM "
                        "entity_note_revisions WHERE entity_id = ?)) "
                        "FROM entity_note_revisions WHERE entity_id = ?", (r["id"], r["id"]))
            earliest, count, creation = cur.fetchone()
            if not count:
                skipped.append((r["untranslated"], "no revision history (legacy note)"))
                continue
            if not creation and not replace_legacy:
                skipped.append((r["untranslated"], "history does not start with a creation "
                                "(--replace-legacy to draft anyway)"))
                continue
            r["replace_legacy"] = not creation
            if earliest is None or earliest - r["first_seen"] < min_room:
                skipped.append((r["untranslated"],
                                f"history starts at ch{earliest}, first seen ch{r['first_seen']}"))
                continue
            r["history_starts"] = earliest
            picked.append(r)
            if len(picked) >= top:
                break
    return picked, skipped


def appearances(db, entity_id, before):
    with db._conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT c.chapter_number, c.summary FROM chapter_entities ce "
                    "JOIN chapters c ON c.id = ce.chapter_id "
                    "WHERE ce.entity_id = ? AND c.chapter_number < ? "
                    "ORDER BY c.chapter_number", (entity_id, before))
        return [(n, (s or "").strip()) for n, s in cur.fetchall()]


def _even_sample(items, cap):
    if len(items) <= cap:
        return items
    step = len(items) / cap
    keep = {int(i * step) for i in range(cap)} | {0, len(items) - 1}
    return [items[i] for i in sorted(keep)]


def pick_chapters(model, r, seen):
    listed = _even_sample([(n, s) for n, s in seen if s], MAX_PICK_SUMMARIES)
    prompt = json.dumps({
        "character": r["untranslated"], "english": r["translation"],
        "chapters": [{"chapter": n, "summary": s} for n, s in listed]},
        ensure_ascii=False, indent=1)
    reply = call_model(model, prompt, system_prompt=PICK_PROMPT)
    valid = {n for n, _ in listed}
    out = {}
    for s in reply.get("stamps") or []:
        try:
            n = int(s.get("chapter"))
        except (TypeError, ValueError):
            continue
        if n in valid:
            out[n] = (s.get("change") or "").strip()
    first = listed[0][0]
    out.setdefault(first, "first appearance")
    return enforce_coverage(out, [n for n, _ in listed])


def enforce_coverage(picks, chapters, long_span=300, limit_long=6, limit=4):
    """The model tends to crowd its picks into the opening chapters. On a long
    span, give every third of it at least one stamp (the median appearance in
    that third), then trim back to the limit by dropping the pick that sits
    closest to the one before it — never the first appearance, never a
    coverage pick."""
    first, last = chapters[0], chapters[-1]
    if last - first <= long_span:
        return sorted(picks.items())[:limit]
    picks, forced = dict(picks), set()
    third = (last - first) / 3
    for i in range(3):
        lo, hi = first + i * third, first + (i + 1) * third
        if any(lo <= n <= hi for n in picks):
            continue
        inside = [n for n in chapters if lo <= n <= hi]
        if inside:
            n = inside[len(inside) // 2]
            picks[n] = "coverage: no turning point picked in this part of the span"
            forced.add(n)
    while len(picks) > limit_long:
        order = sorted(picks)
        candidates = [(order[i] - order[i - 1], order[i]) for i in range(1, len(order))
                      if order[i] not in forced]
        if not candidates:
            break
        del picks[min(candidates)[1]]
    return sorted(picks.items())


def draft_stamp(db, book_id, model, r, chapter, seen, previous_note, gender, cache):
    upto = [(n, s) for n, s in seen if n <= chapter and s]
    context = upto[:1] + [x for x in upto[-RECENT:] if x != upto[0]]
    if chapter not in cache:
        cache[chapter] = db.get_chapter(book_id=book_id, chapter_number=chapter) or {}
    prose = _windows(cache[chapter].get("content") or [], r["translation"] or "",
                     STATE_RADIUS, 2, fold=True)
    payload = {
        "untranslated": r["untranslated"],
        "translation": r["translation"],
        "recorded_gender": gender or "unknown",
        "stamp_chapter": chapter,
        "previous_note": previous_note,
        "chapter_summaries": [{"chapter": n, "summary": s} for n, s in context],
        "prose_excerpts": prose,
    }
    reply = call_model(model, json.dumps(payload, ensure_ascii=False, indent=1),
                       system_prompt=MILESTONE_PROMPT)
    entry = reply.get(r["untranslated"]) or next(iter(reply.values()), {})
    return (entry.get("note") or "").strip(), (entry.get("reason") or "").strip()


def run_one(db, book_id, model, r):
    with db._conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT gender FROM entities WHERE id = ?", (r["id"],))
        gender = (cur.fetchone() or [None])[0]
    seen = appearances(db, r["id"], r["history_starts"])
    if not seen:
        return None, "no indexed appearances before the existing history"
    picks = pick_chapters(model, r, seen)
    stamps, previous, cache, flags = [], None, {}, []
    for chapter, change in picks:
        note, reason = draft_stamp(db, book_id, model, r, chapter, seen, previous, gender, cache)
        if not note:
            flags.append(f"ch{chapter}: empty draft, dropped")
            continue
        if len(note) > NOTE_CAP:
            flags.append(f"ch{chapter}: OVER CAP {len(note)} chars — trim before applying")
        if re.search(r"\b(?:ch\.?\s*\d+|chapter\s+\d+)", note, re.I):
            flags.append(f"ch{chapter}: mentions a chapter number")
        stamps.append({"chapter": chapter, "change": change, "note": note, "reason": reason})
        previous = note
    if not stamps:
        return None, "no stamps drafted"
    return {
        "untranslated": r["untranslated"],
        "translation": r["translation"],
        "spread": r["spread"],
        "first_seen": r["first_seen"],
        "existing_history_starts": r["history_starts"],
        "existing_note": r["note"],
        "flags": flags,
        "reason": REASON,
        "replace_legacy": r.get("replace_legacy", False),
        "stamps": stamps,
    }, None


COMPRESS_PROMPT = """Shorten a glossary note for a character in a translated novel to at most 380 characters.

Keep, in this order of priority: who they are and their key relationships; their current situation (rank, realm, allegiance, alive or dead); how they are addressed. Drop narrative detail, backstory episodes and anything a translator does not need to render the next chapter correctly.

Use only what is in the note. Add nothing. Keep every English name exactly as written.

Return ONLY JSON: {"note": "..."}"""

MIN_GAP = 10


def collapse_close(stamps, min_gap=MIN_GAP):
    """Drop a stamp when the next one is fewer than min_gap chapters later —
    the later one knows more, and the gap it leaves is a few chapters."""
    out = []
    for i, s in enumerate(stamps):
        nxt = stamps[i + 1] if i + 1 < len(stamps) else None
        if nxt and nxt["chapter"] - s["chapter"] < min_gap:
            continue
        out.append(s)
    return out


def compress(model, note):
    reply = call_model(model, note, system_prompt=COMPRESS_PROMPT)
    return (reply.get("note") or "").strip()


def tidy(args):
    """Collapse close stamps and compress over-cap notes in an existing plan."""
    plan = json.load(open(args.out, encoding="utf-8"))
    jobs = []
    for e in plan:
        key = "insert" if "insert" in e else "stamps"
        before = [s["chapter"] for s in e[key]]
        e[key] = collapse_close(e[key])
        after = [s["chapter"] for s in e[key]]
        if after != before:
            print(f"  {e['untranslated']}: {before} → {after}")
        jobs.extend((e, s) for s in e[key] if len(s["note"]) > 400)
    print(f"Compressing {len(jobs)} over-length notes")

    def work(job):
        e, s = job
        try:
            return job, compress(args.model, s["note"])
        except Exception as ex:
            return job, f"ERROR {ex}"

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for (e, s), new in pool.map(work, jobs):
            if new.startswith("ERROR") or not new or len(new) > NOTE_CAP:
                print(f"  ⚠ {e['untranslated']} ch{s['chapter']}: compress failed ({new[:60]!r})")
                continue
            s["long_note"], s["note"] = s["note"], new
    for e in plan:
        e["flags"] = [f"ch{s['chapter']}: still {len(s['note'])} chars"
                      for s in e.get("insert", e.get("stamps", [])) if len(s["note"]) > NOTE_CAP]
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(plan, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    left = sum(len(e["flags"]) for e in plan)
    print(f"{sum(len(e.get('insert', e.get('stamps', []))) for e in plan)} stamps; "
          f"{left} still over cap")


GAP_PICK_PROMPT = PICK_PROMPT.replace(
    "- ALWAYS include the first chapter listed — the note that introduces the character.\n",
    "- The character already has a note in force at the start of this range (given as "
    "note_at_start); pick the points where it goes out of date.\n")
assert GAP_PICK_PROMPT != PICK_PROMPT, "PICK_PROMPT wording changed; update GAP_PICK_PROMPT"
MIN_GAP_APPEARANCES = 15
# A gap must also be long. A character who is in nearly every chapter (the MC)
# clears the appearance bar in any stretch at all, so without a span floor every
# fill creates new "gaps" between the stamps it just added.
MIN_GAP_SPAN = 100


def stamped_history(db, entity_id):
    with db._conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT chapter_number, new_note FROM entity_note_revisions "
                    "WHERE entity_id = ? AND chapter_number IS NOT NULL ORDER BY id",
                    (entity_id,))
        return cur.fetchall()


def find_gaps(db, entity_id, min_appearances=MIN_GAP_APPEARANCES, min_span=MIN_GAP_SPAN):
    """[(start_ch, end_ch, note_at_start, [(ch, summary) inside])] — stretches
    between consecutive stamped revisions where the character keeps appearing."""
    hist = stamped_history(db, entity_id)
    seen = appearances(db, entity_id, 10 ** 6)
    gaps = []
    for (a, note), (b, _) in zip(hist, hist[1:]):
        inside = [(n, sm) for n, sm in seen if a + MIN_GAP <= n <= b - MIN_GAP and sm]
        if b - a >= min_span and len(inside) >= min_appearances:
            gaps.append((a, b, note, inside))
    return gaps, seen


def pick_in_gap(model, r, a, b, note_at_start, inside):
    listed = _even_sample(inside, MAX_PICK_SUMMARIES)
    prompt = json.dumps({
        "character": r["untranslated"], "english": r["translation"],
        "range": f"after chapter {a}, before chapter {b}",
        "note_at_start": note_at_start,
        "chapters": [{"chapter": n, "summary": sm} for n, sm in listed]},
        ensure_ascii=False, indent=1)
    reply = call_model(model, prompt, system_prompt=GAP_PICK_PROMPT)
    valid = {n for n, _ in listed}
    out = {}
    for x in reply.get("stamps") or []:
        try:
            n = int(x.get("chapter"))
        except (TypeError, ValueError):
            continue
        if n in valid:
            out[n] = (x.get("change") or "").strip()
    return enforce_coverage(out, [n for n, _ in listed])


def fill_one(db, book_id, model, r, gender):
    gaps, seen = find_gaps(db, r["id"])
    if not gaps:
        return None, "no gap with enough appearances"
    inserts, flags, cache = [], [], {}
    for a, b, note_at_start, inside in gaps:
        picks = pick_in_gap(model, r, a, b, note_at_start, inside)
        previous = note_at_start
        for chapter, change in picks:
            note, reason = draft_stamp(db, book_id, model, r, chapter, seen, previous,
                                       gender, cache)
            if not note:
                flags.append(f"ch{chapter}: empty draft, dropped")
                continue
            if len(note) > NOTE_CAP:
                flags.append(f"ch{chapter}: OVER CAP {len(note)} chars — trim before applying")
            inserts.append({"chapter": chapter, "change": change, "note": note,
                            "reason": reason, "gap": [a, b]})
            previous = note
    if not inserts:
        return None, "no stamps drafted"
    return {"untranslated": r["untranslated"], "translation": r["translation"],
            "spread": r["spread"], "gaps": [[a, b, len(i)] for a, b, _, i in gaps],
            "flags": flags, "reason": "note backfill: milestone stamp (gap fill)",
            "insert": inserts}, None


def fill_gaps(db, args):
    from entity_note_coverage import compute_coverage
    rows = {r["untranslated"]: r for r in compute_coverage(db, args.book_id)["rows"]}
    keys = args.only or []
    if not keys:
        ranked = []
        for r in rows.values():
            if r["kind"] != "stateful" or not r["spread"] or r["untranslated"] in args.exclude:
                continue
            gaps, _ = find_gaps(db, r["id"])
            if gaps:
                ranked.append((max(len(g[3]) for g in gaps), r["untranslated"]))
        keys = [k for _, k in sorted(ranked, reverse=True)[:args.top]]
    existing = json.load(open(args.out, encoding="utf-8")) if os.path.exists(args.out) else []
    done = {e["untranslated"] for e in existing}
    plan = list(existing)
    for k in keys:
        r = rows.get(k)
        if not r or k in done:
            continue
        gaps, _ = find_gaps(db, r["id"])
        print(f"  {k} → {r['translation']}: gaps "
              f"{[(a, b, len(i)) for a, b, _, i in gaps] or 'none'}")
        if args.list or not gaps:
            continue
        with db._conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT gender FROM entities WHERE id = ?", (r["id"],))
            gender = (cur.fetchone() or [None])[0]
        try:
            entry, err = fill_one(db, args.book_id, args.model, r, gender)
        except Exception as e:
            entry, err = None, f"failed: {str(e)[:150]}"
        if not entry:
            print(f"    FAILED: {err}")
            continue
        plan.append(entry)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(plan, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
        print(f"    drafted inserts at {[x['chapter'] for x in entry['insert']]}"
              + (f"  ⚑ {'; '.join(entry['flags'])}" if entry["flags"] else ""))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-b", "--book-id", type=int, required=True)
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--exclude", action="append", default=[], metavar="KEY")
    ap.add_argument("--min-room", type=int, default=30,
                    help="Chapters needed between first appearance and existing history")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--out", required=True)
    ap.add_argument("--list", action="store_true", help="Show the selection and stop")
    ap.add_argument("--only", action="append", metavar="KEY", help="Draft only these characters")
    ap.add_argument("--fill-gaps", action="store_true",
                    help="Draft stamps INSIDE existing histories: stretches between two stamps "
                         "where the character keeps appearing. Writes an 'insert' plan.")
    ap.add_argument("--replace-legacy", action="store_true",
                    help="Also draft characters whose note predates the revision log; the "
                         "plan marks them replace_legacy, which restamp needs to accept them")
    ap.add_argument("--tidy", action="store_true",
                    help="On an existing plan: drop stamps <10 chapters apart and compress "
                         "over-length notes (the compress call sees only the note)")
    args = ap.parse_args()

    from config import TranslationConfig
    from database import DatabaseManager
    from logger import Logger
    config = TranslationConfig()          # also loads .env — the provider keys live there
    if args.tidy:
        return tidy(args)
    db = DatabaseManager(config, Logger(config))
    if args.fill_gaps:
        return fill_gaps(db, args)

    existing = json.load(open(args.out, encoding="utf-8")) if os.path.exists(args.out) else []
    done = {e["untranslated"] for e in existing}
    picked, skipped = select(db, args.book_id, args.top, set(args.exclude), args.min_room,
                             only=set(args.only or []), replace_legacy=args.replace_legacy)
    print(f"Selected {len(picked)}:")
    for r in picked:
        print(f"  {r['untranslated']} → {r['translation']}  spread {r['spread']}, "
              f"first ch{r['first_seen']}, history from ch{r['history_starts']}"
              + ("  (already drafted)" if r["untranslated"] in done else ""))
    for key, why in skipped:
        print(f"  skipped {key}: {why}")
    if args.list:
        return
    todo = [r for r in picked if r["untranslated"] not in done]

    def work(r):
        try:
            return r, *run_one(db, args.book_id, args.model, r)
        except Exception as e:
            return r, None, f"failed: {str(e)[:150]}"

    plan = list(existing)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for r, entry, err in pool.map(work, todo):
            if entry:
                plan.append(entry)
                with open(args.out, "w", encoding="utf-8") as fh:
                    json.dump(plan, fh, ensure_ascii=False, indent=1)
                    fh.write("\n")
                print(f"  drafted {r['untranslated']}: stamps at "
                      f"{[s['chapter'] for s in entry['stamps']]}"
                      + (f"  ⚑ {'; '.join(entry['flags'])}" if entry["flags"] else ""))
            else:
                print(f"  FAILED {r['untranslated']}: {err}")
    print(f"{len(plan)} entries in {args.out}")


if __name__ == "__main__":
    main()
