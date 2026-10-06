#!/usr/bin/env python3
"""
Draft entity notes for the entity-note backfill (EntityNoteBackfill.md).

  --kind convention (Phase 1, default): terms, places, titles … stamped at the
      entity's FIRST appearance, from first-chapter context only.
  --kind stateful (Phase 2): the book's gendered categories (characters),
      stamped at the LAST appearance. Context is mainly chapter SUMMARIES
      (first appearance, the two busiest chapters, the last three — all at or
      before the stamp), plus a few prose windows; name-centred prose alone
      produced scene trivia and lost track of which life a scene was in.
      Short/full name pairs (镜辞 / 涂山镜辞) are batched together, and short
      forms and address forms ("Miss Bai") get pointer notes. The model also
      flags pronouns that contradict the recorded gender. A draft over the
      cap is kept, marked skip, for a hand trim; --only KEY redrafts one.

The convention mode is described below; the stateful mode follows the same
draft → review → apply shape.

Two steps, with a human in between:

  draft   Take the convention worklist from entity_note_coverage.py (status
          todo), give a model each entity's context from its FIRST-APPEARANCE
          CHAPTER ONLY, and write the drafts to a JSON file. Nothing touches
          the database.
  apply   Write the reviewed file through set_entity_note(author='script',
          chapter_number=<first appearance>). Every write is a revision, so a
          bad row is revertible (note_revisions.py / the Entities panel).

Why context from one chapter only: a note stamped at chapter F is in force
from F onward, so it may only carry facts known by F (invariant 4). The
model cannot leak what it is never shown. Related glossary terms are limited
to those whose origin_chapter is <= F for the same reason.

Each draft also reports whether the chapter's translation actually uses the
glossary rendering (`rendering_in_chapter`) and the model's `mismatch` flag.
Either one is a drift lead, the same kind the origin backfill turns up.

Excluded from the worklist: entities hidden by origin_chapter (a stamp at F
would stay invisible — fix origin first) and single-character keys (their
first "appearance" is often inside another word) unless --include-single-char.

Usage:
    python3 draft_entity_notes.py -b 14 --min-chapters 20 --limit 12 --out /tmp/d.json
    python3 draft_entity_notes.py -b 14 --min-chapters 20 --out d.json   # resumes: skips drafted keys
    python3 draft_entity_notes.py -b 14 --apply d.json                   # dry run
    python3 draft_entity_notes.py -b 14 --apply d.json --write
In the drafts file, set "skip": true on a row to leave it out of --apply, or
edit "note" in place.
"""

import argparse
import json
import os
import re
import sys
import time
import unicodedata
import warnings
from concurrent.futures import ThreadPoolExecutor

warnings.filterwarnings("ignore", category=FutureWarning)
os.environ.pop("DEBUG", None)

DEFAULT_MODEL = "claude:claude-sonnet-5"
NOTE_CAP = 220            # convention notes: one or two sentences
STATEFUL_CAP = 450        # character notes: identity plus current state
CAPS = {"convention": NOTE_CAP, "stateful": STATEFUL_CAP}
SRC_RADIUS = 110          # chars either side of a source hit
TL_RADIUS = 160           # chars either side of a translated hit
STATE_RADIUS = 260        # stateful mode reads translated prose, and needs more of it
REASONS = {
    "convention": "note backfill (phase 1): convention at first appearance",
    "stateful": "note backfill (phase 2): character state at last appearance",
}
REASON = REASONS["convention"]

SYSTEM_PROMPT = """You write glossary notes for a Chinese→English web-novel translation.

A note is standing guidance injected into the translator's prompt whenever the term appears. You are writing notes for CONVENTIONS — terms, titles, places, organizations, realms, creatures, items — not characters. Each note will be in force from the chapter the term first appears, so it must be true and spoiler-free AT THAT CHAPTER.

For each entity you get: the source term, its category, the established English rendering, the chapter it first appears in, source excerpts from that chapter, the translated lines from that chapter that use the rendering, and related glossary terms that already existed by then.

Write each note as 1-2 short sentences, at most 200 characters:
- What the term IS, as a definition — enough that a translator seeing it cold renders it correctly and consistently.
- The rendering rule: "render as 'X'", plus capitalization, and "distinct from <中文> 'Y'" where a related term could be confused with it.
- Use the style: Chinese terms in the note are followed by their English in quotes or parentheses, e.g. 真气 'true qi'.

Hard rules:
- ONLY facts evident from the excerpts given. Never use outside knowledge of this novel's plot.
- No plot events, no character fates, no chapter numbers, no "later"/"eventually".
- Do not tie a generic term to one particular character or storyline. Describe the term, not the scene.
- Do not restate the whole book's conventions; the translator already has them.
- Make no claims about how OTHER terms are rendered or about patterns across the glossary. Cite a related term only to draw a distinction, and only one listed in related_terms.
- Do not justify the rendering ("per its use as…"); state it.
- The rendering in the note must match the established rendering exactly, including capitalization.
- Do not rank a realm or grade against other realms ("above X", "entry-level", "after Y"): a novel can carry more than one cultivation ladder, and the excerpts show only one of them.
- Do not say whose it is or where the story is ("where the protagonist lives", "the setting's reigning dynasty", "here used of…"); say what it is.
- If the excerpts don't show what the term is, write only the rendering rule.
- If the translated lines render the term differently from the established rendering, still write the note for the established rendering, and set "mismatch": true with the variant in "reason".

Return ONLY a JSON object keyed by the source term exactly as given:
{"<source term>": {"note": "...", "mismatch": false, "reason": "one line: what the note rests on, or the mismatch"}}"""


STATEFUL_PROMPT = """You write glossary notes for CHARACTERS in a Chinese→English web-novel translation.

A note is standing guidance injected into the translator's prompt whenever the character appears. Each note is stamped at the character's LAST appearance in the book so far and describes them as of then. The translator uses it to keep identity, relationships, forms of address and pronouns straight.

For each character you get: the source name, the established English rendering, the recorded gender, the chapter the note will be stamped at, summaries of chapters they appear in (chronological, all at or before the stamp chapter — the last ones describe their current situation), a few translated prose excerpts, and related glossary names.

Stories can have more than one timeline, life, or world (simulations, reincarnations, flashbacks). Use the summaries to tell which one a scene belongs to, and describe the character's situation as of the LAST summaries.

Write each note as 2-4 short sentences, at most 400 characters:
- Who they are: role, affiliation, and their key relationships to other named characters.
- Their state as of the stamp chapter, where the excerpts show it: cultivation realm or rank, office, allegiance, alive or dead.
- How they are addressed or referred to, if that matters for rendering (titles, nicknames).

Hard rules:
- ONLY facts evident from the excerpts. Never use outside knowledge of this novel.
- Where the material conflicts, the latest chapter wins.
- Identity and relationships first; state only where the material shows it clearly. No scene trivia (what they cooked, a one-off joke, what they wore).
- Use only English names that appear in the material or in related_names. Never coin or guess an English name for anyone.
- No chapter numbers.
- Refer to other characters by their English names.
- If a listed entity is only the given-name or short form of another entity that is ALSO LISTED IN THIS BATCH, write a one-sentence note for it that points to the full form ("Short form of 涂山镜辞 'Tushan Jingci'.") and put the substance on the full form. Otherwise write a full note.
- If a listed entity is only an ADDRESS FORM, title or nickname for a character named in related_names or the material ("Miss Bai", "Madam Tushan", "Brother Xiao", a childhood name), write a one-sentence pointer: "Address form ('Miss Bai') for 白如雪 'Snow White'." Separate full notes for one person drift apart and contradict each other.
- Set "gender_mismatch": true, with the evidence in "reason", if the excerpts' pronouns contradict the recorded gender.

Return ONLY a JSON object keyed by the source name exactly as given:
{"<source name>": {"note": "...", "gender_mismatch": false, "reason": "one line: what the note rests on"}}"""


# ── selection ─────────────────────────────────────────────────────────────────

def select_entities(db, book_id, min_chapters, categories=None, include_single_char=False,
                    kind="convention"):
    from entity_note_coverage import compute_coverage, worklist
    cov = compute_coverage(db, book_id)
    rows = worklist(cov["rows"], min_chapters=min_chapters, statuses=("todo",),
                    kind=kind, categories=categories)
    return [r for r in rows
            if not r["origin_after_first"]
            and (include_single_char or not r["single_char"])]


def pair_up(rows):
    """Order rows so a short name sits next to its full form (镜辞 / 涂山镜辞),
    which lets one batch hold both and write the substance once."""
    keys = {r["untranslated"]: r for r in rows}
    out, seen = [], set()
    for r in rows:
        if r["untranslated"] in seen:
            continue
        group = [r] + [o for k, o in keys.items()
                       if k not in seen and k != r["untranslated"]
                       and len(k) >= 2 and len(r["untranslated"]) >= 2
                       and (k in r["untranslated"] or r["untranslated"] in k)]
        for g in group:
            if g["untranslated"] not in seen:
                seen.add(g["untranslated"])
                out.append(g)
    return out


# ── context ───────────────────────────────────────────────────────────────────

def _nfc(s):
    return unicodedata.normalize("NFC", s or "")


def _windows(lines, needle, radius, limit, fold=False):
    """Up to `limit` excerpts around occurrences of needle, one per line."""
    out = []
    key = needle.lower() if fold else needle
    for line in lines:
        hay = _nfc(line)
        pos = (hay.lower() if fold else hay).find(key)
        if pos < 0:
            continue
        lo, hi = max(0, pos - radius), min(len(hay), pos + len(needle) + radius)
        out.append(("…" if lo else "") + hay[lo:hi].strip() + ("…" if hi < len(hay) else ""))
        if len(out) >= limit:
            break
    return out


def build_context(db, book_id, rows, chapter_cache=None):
    """Per-entity payload, from the first-appearance chapter only."""
    chapter_cache = {} if chapter_cache is None else chapter_cache
    with db._conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT untranslated, translation, origin_chapter FROM entities "
                    "WHERE book_id = ? AND origin_chapter IS NOT NULL", (book_id,))
        glossary = [(_nfc(u), t, o) for u, t, o in cur.fetchall() if u]

    payloads = []
    for r in rows:
        f = r["first_seen"]
        ch = chapter_cache.get(f)
        if ch is None:
            ch = chapter_cache[f] = db.get_chapter(book_id=book_id, chapter_number=f) or {}
        key = _nfc(r["untranslated"])
        src = _windows(ch.get("untranslated") or [], key, SRC_RADIUS, 2)
        tl = _windows(ch.get("content") or [], r["translation"] or "", TL_RADIUS, 2, fold=True) \
            if r["translation"] else []
        related = [f"{u} = {t}" for u, t, o in glossary
                   if o <= f and u != key and len(u) >= 2 and (u in key or key in u)][:8]
        payloads.append({
            "untranslated": r["untranslated"],
            "category": r["category"],
            "translation": r["translation"],
            "first_chapter": f,
            "source_excerpts": src,
            "translated_lines": tl,
            "related_terms": related,
            "_row": r,
        })
    return payloads


def build_context_stateful(db, book_id, rows, chapter_cache=None):
    """Per-character payload: translated prose from chapters <= the last appearance."""
    chapter_cache = {} if chapter_cache is None else chapter_cache

    def chapter(n):
        if n not in chapter_cache:
            chapter_cache[n] = db.get_chapter(book_id=book_id, chapter_number=n) or {}
        return chapter_cache[n]

    with db._conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, untranslated, translation, origin_chapter, gender "
                    "FROM entities WHERE book_id = ?", (book_id,))
        glossary = cur.fetchall()
    gender = {g[0]: g[4] for g in glossary}

    payloads = []
    for r in rows:
        last, first = r["last_seen"], r["first_seen"]
        with db._conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT c.chapter_number, ce.occurrences, c.summary FROM chapter_entities ce "
                "JOIN chapters c ON c.id = ce.chapter_id "
                "WHERE ce.entity_id = ? AND c.chapter_number <= ? ORDER BY c.chapter_number",
                (r["id"], last))
            seen = cur.fetchall()
        # Chapter summaries frame each appearance (which life, which arc, who is
        # doing what) far better than name-centred snippets: first appearance,
        # the two busiest chapters, and the last three.
        busiest = sorted(seen, key=lambda s: (-(s[1] or 0), -s[0]))[:2]
        picked = {s[0]: s for s in seen[:1] + busiest + seen[-3:]}
        summaries = [{"chapter": n, "summary": (picked[n][2] or "").strip()}
                     for n in sorted(picked) if (picked[n][2] or "").strip()]
        top = busiest[0][0] if busiest else None
        name = r["translation"] or ""
        excerpts = []
        for n, limit in ((top if top != last else None, 1), (last, 2)):
            if n is None:
                continue
            excerpts.extend(_windows(chapter(n).get("content") or [], name, STATE_RADIUS,
                                     limit, fold=True))
        key = _nfc(r["untranslated"])
        related = [f"{_nfc(u)} = {t}" for _, u, t, o, _ in glossary
                   if u and o is not None and o <= last and _nfc(u) != key
                   and len(_nfc(u)) >= 2 and (_nfc(u) in key or key in _nfc(u))][:8]
        payloads.append({
            "untranslated": r["untranslated"],
            "translation": r["translation"],
            "recorded_gender": gender.get(r["id"]) or "unknown",
            "stamp_chapter": last,
            "chapter_summaries": summaries,
            "prose_excerpts": excerpts,
            "related_names": related,
            "_row": r,
            "_chapter": last,
        })
    return payloads


def build_user_prompt(book_title, book_notes, payloads):
    parts = [f"Book: {book_title}"]
    if book_notes:
        parts.append("Book conventions (for rendering rules only):\n" + book_notes.strip())
    parts.append("Entities:")
    for p in payloads:
        parts.append(json.dumps({k: v for k, v in p.items() if not k.startswith("_")},
                                ensure_ascii=False, indent=1))
    return "\n\n".join(parts)


# ── model call ────────────────────────────────────────────────────────────────

def _parse_reply(raw):
    from footnote_scan_core import strip_code_fence
    text = strip_code_fence(raw or "").strip()
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in reply")
    # Usually one object; sometimes one object per entity back to back. Merge
    # every top-level object found.
    decoder = json.JSONDecoder()
    data, pos = {}, start
    while pos < len(text):
        obj, end = decoder.raw_decode(text, pos)
        if not isinstance(obj, dict):
            raise ValueError("reply is not an object")
        data.update(obj)
        nxt = text.find("{", end)
        if nxt < 0:
            break
        pos = nxt
    return data


def call_model(model_spec, user_prompt, overload_wait=300, effort="low", system_prompt=None):
    from footnote_scan_core import provider_for_thread
    from providers.base import OverloadedError, looks_overloaded

    provider_name, model_name = model_spec.split(":", 1)
    provider = provider_for_thread(provider_name)
    messages = [{"role": "system", "content": system_prompt or SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt}]
    nudged = False
    empty = 0
    while True:
        try:
            resp = provider.chat_completion(model=model_name, messages=messages,
                                            temperature=0.0, thinking_effort=effort)
            raw = provider.get_response_content(resp) or ""
        except OverloadedError:
            time.sleep(overload_wait)
            continue
        except Exception as e:
            if looks_overloaded(str(e), strict=False):
                time.sleep(overload_wait)
                continue
            raise
        if not raw.strip():
            empty += 1
            if empty <= 2:
                continue
            raise RuntimeError("model returned an empty reply 3x")
        try:
            return _parse_reply(raw)
        except (ValueError, json.JSONDecodeError):
            if nudged:
                raise
            nudged = True
            messages = messages + [{"role": "assistant", "content": raw},
                                   {"role": "user", "content": "Return ONLY the JSON object."}]


# ── guards ────────────────────────────────────────────────────────────────────

_CHAPTER_RE = re.compile(r"\b(?:ch\.?\s*\d+|chapter\s+\d+)", re.I)
_LATER_RE = re.compile(r"\b(?:later|eventually|will become|would become)\b", re.I)


def check_draft(payload, reply, kind="convention"):
    """(draft_row, None) or (None, why)."""
    cap = CAPS[kind]
    if not isinstance(reply, dict):
        return None, "missing from reply"
    note = (reply.get("note") or "").strip()
    if not note:
        return None, "empty note"
    over = len(note) > cap        # kept for a hand trim rather than thrown away
    if _CHAPTER_RE.search(note):
        return None, "mentions a chapter number"
    flags = [f"OVER CAP: {len(note)} chars (cap {cap}) — trim, then clear skip"] if over else []
    if kind == "convention" and _LATER_RE.search(note):
        flags.append("forward-looking wording")
    if reply.get("mismatch"):
        flags.append("model: rendering mismatch")
    if reply.get("gender_mismatch"):
        flags.append("model: gender mismatch")
    if kind == "stateful" and not payload.get("chapter_summaries"):
        flags.append("no chapter summaries found")
    if kind == "convention" and payload["translation"] and not payload["translated_lines"]:
        flags.append("rendering not found in chapter translation")
    r = payload["_row"]
    return {
        "id": r["id"],
        "untranslated": payload["untranslated"],
        "category": r["category"],
        "translation": payload["translation"],
        "kind": kind,
        "chapter": payload.get("_chapter", payload.get("first_chapter")),
        "spread": r["spread"],
        "note": note,
        "reason": (reply.get("reason") or "").strip(),
        "flags": flags,
        "skip": over,
    }, None


# ── draft / apply ─────────────────────────────────────────────────────────────

def draft(db, args):
    from footnote_scan_core import book_notes
    book = db.get_book(book_id=args.book_id)
    existing = []
    if os.path.exists(args.out):
        existing = json.load(open(args.out, encoding="utf-8"))
    done = {d["untranslated"] for d in existing}

    rows = [r for r in select_entities(db, args.book_id, args.min_chapters, args.category,
                                       args.include_single_char, kind=args.kind)
            if r["untranslated"] not in done
            and (not args.only or r["untranslated"] in args.only)]
    if args.kind == "stateful":
        rows = pair_up(rows)      # before --limit, so a limit never splits a pair
    if args.limit:
        rows = rows[:args.limit]
    if not rows:
        print("Nothing to draft.")
        return
    builder = build_context_stateful if args.kind == "stateful" else build_context
    system = STATEFUL_PROMPT if args.kind == "stateful" else SYSTEM_PROMPT
    payloads = builder(db, args.book_id, rows)
    batches = [payloads[i:i + args.batch_size]
               for i in range(0, len(payloads), args.batch_size)]
    notes = book_notes(db, args.book_id)
    print(f"Drafting {len(payloads)} {args.kind} entities in {len(batches)} call(s) on {args.model}")

    def run(batch):
        prompt = build_user_prompt(book.get("title", ""), notes, batch)
        try:
            return batch, call_model(args.model, prompt, system_prompt=system), None
        except Exception as e:           # one bad batch must not lose the others
            return batch, {}, f"batch failed: {str(e)[:120]}"

    def save():
        merged = existing + drafted
        merged.sort(key=lambda d: (-d["spread"], d["untranslated"]))
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(merged, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
        return merged

    drafted, rejected = [], []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for batch, reply, err in pool.map(run, batches):
            for p in batch:
                row, why = check_draft(p, reply.get(p["untranslated"]), args.kind)
                if row:
                    drafted.append(row)
                else:
                    rejected.append((p["untranslated"], err or why))
            merged = save()               # a crash later keeps what finished
    print(f"Drafted {len(drafted)}, rejected {len(rejected)} → {args.out} "
          f"({len(merged)} total in file)")
    for key, why in rejected:
        print(f"  REJECTED {key}: {why}")
    for d in drafted:
        flag = f"  ⚑ {'; '.join(d['flags'])}" if d["flags"] else ""
        print(f"  ch{d['chapter']:<4} {d['untranslated']} → {d['translation']}{flag}\n"
              f"         {d['note']}")


def apply(db, args):
    drafts = json.load(open(args.apply, encoding="utf-8"))
    written = skipped = 0
    for d in drafts:
        if d.get("skip"):
            continue
        eid, f = d["id"], d["chapter"]
        with db._conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT book_id, untranslated, note, origin_chapter FROM entities "
                        "WHERE id = ?", (eid,))
            row = cur.fetchone()
            cur.execute("SELECT MAX(chapter_number) FROM entity_note_revisions "
                        "WHERE entity_id = ?", (eid,))
            latest = cur.fetchone()[0]
        why = None
        if not row or row[0] != args.book_id or row[1] != d["untranslated"]:
            why = "entity changed or gone"
        elif row[2]:
            why = "already has a note (not overwriting)"
        elif latest is not None and latest > f:
            why = f"revision at ch{latest} — would sit behind it"
        elif row[3] is not None and row[3] > f:
            why = f"origin_chapter ch{row[3]} would hide it"
        elif (not (d.get("note") or "").strip()
              or len(d["note"]) > CAPS[d.get("kind", "convention")] + 80):
            why = "note empty or too long"
        if why:
            skipped += 1
            print(f"  SKIP {d['untranslated']}: {why}")
            continue
        if args.write:
            db.set_entity_note(eid, d["note"].strip(), author="script", chapter_number=f,
                               reason=REASONS[d.get("kind", "convention")])
            at = db.notes_as_of(args.book_id, f, entity_ids=[eid]).get(eid)
            if at != d["note"].strip():
                print(f"  ⚠ VERIFY FAILED {d['untranslated']}: notes_as_of(ch{f}) = {at!r}")
                continue
        written += 1
    verb = "wrote" if args.write else "would write"
    print(f"{verb} {written}, skipped {skipped}" + ("" if args.write else " (dry run — add --write)"))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-b", "--book-id", type=int, required=True)
    ap.add_argument("--min-chapters", type=int, default=20)
    ap.add_argument("--category", action="append")
    ap.add_argument("--include-single-char", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="Entities to draft this run (0 = all)")
    ap.add_argument("--only", action="append", metavar="KEY",
                    help="Draft only this entity (repeatable) — for redrafting a rejection")
    ap.add_argument("--kind", choices=("convention", "stateful"), default="convention",
                    help="convention: stamp at first appearance (phase 1); "
                         "stateful: characters, stamp at last appearance (phase 2)")
    ap.add_argument("--batch-size", type=int, default=0,
                    help="Entities per call (default 12 convention, 6 stateful)")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--out", help="Drafts file (created or extended)")
    ap.add_argument("--apply", metavar="FILE", help="Apply a reviewed drafts file")
    ap.add_argument("--write", action="store_true", help="With --apply: actually write")
    args = ap.parse_args()
    if not args.apply and not args.out:
        ap.error("--out is required when drafting")
    if not args.batch_size:
        args.batch_size = 6 if args.kind == "stateful" else 12

    from config import TranslationConfig
    from database import DatabaseManager
    from logger import Logger
    config = TranslationConfig()
    db = DatabaseManager(config, Logger(config))
    if args.apply:
        apply(db, args)
    else:
        draft(db, args)


if __name__ == "__main__":
    main()
