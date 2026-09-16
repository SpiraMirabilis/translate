#!/usr/bin/env python3
"""Collect cultural-referent footnote CANDIDATES for a book with an LLM.

Sends each chapter's SOURCE (untranslated) text to a model that identifies
ancient- or modern-Chinese cultural referents an English reader wouldn't
recognise (观音土, 白蛇传, 懒羊羊 — not the Great Wall) and drafts a short
footnote for each in the house style ("English Name (中文): gloss.").

This is a COLLECTOR ONLY — it never touches chapters or the real footnotes
table. Candidates land in the main DB (footnote_candidates / footnote_scans;
shared with the footnote_scan per-book module, which scans newly ingested
chapters automatically). Review in the web GUI (Footnotes page) or with
--review here; the reviewed set exports to the {term: body} JSON map that
add_footnotes.py consumes.

Because the scan runs on source text (which never changes), every scanned
chapter is remembered in footnote_scans — including zero-find chapters — and
skipped on re-runs. A chapter whose source hash changed (e.g. a retroactive
trad→simp conversion) is re-scanned automatically; --force re-scans
regardless. A failed chapter writes no scan row, so the next run retries it.

The scanner's system prompt is per-book: the footnote_scan module's "Scan
system prompt" setting replaces the built-in one wholesale (blank = built-in),
so a book whose referents aren't purely Chinese can rewrite the scope rules.
This run honors that stored prompt; --print-prompt shows it, --stock-prompt
ignores it.

Entity-aware: each chapter is sent together with the {中文: English} glossary
of the book's entities that literally occur in that chapter (the same matcher
the translation engine uses), so term_en lands on the published English
rendering instead of the model's guess. The entity DB is never mutated.

Hallucination filter: cheaper models invent referents that are nowhere in the
chapter. Every candidate is checked against that chapter's own source text
before anything is printed or stored — a referent that isn't there is dropped
silently (only a count is reported). See footnote_scan_core.
Candidates collected before this filter existed can be cleaned out with
--prune-unverified.

Usage:
    python3 footnote_scan.py -b 35 --chapters 1-50              # collect
    python3 footnote_scan.py -b 35 --chapters ">1800" --model claude:claude-opus-4-8
    python3 footnote_scan.py -b 35 --chapters 42,44,46 --force
    python3 footnote_scan.py -b 35 --workers 1 --delay 5s       # slow scan (pace chapters)
    python3 footnote_scan.py -b 35 --dry-run                    # plan only
    python3 footnote_scan.py -b 35 --print-prompt                # effective scan prompt
    python3 footnote_scan.py -b 35 --review                     # fullscreen TUI
    python3 footnote_scan.py -b 35 --report [--all]
    python3 footnote_scan.py -b 35 --export /tmp/b35_footies.json
    python3 footnote_scan.py -b 35 --prune-unverified [--dry-run]

Chapter spec (--chapters): comma-separated union of
    N       exact chapter          N-M     inclusive range
    >N >=N  greater than (or eq)   <N <=N  less than (or eq)   =N  exact
e.g. "42", "1-50", ">100", "42,44,46", "1-3,>10". Default: all chapters.

Export semantics: only status='rejected' rows are excluded — rows marked keep
AND rows never reviewed at all both export. Rejection is the only veto.
"""

import argparse
import json
import operator
import os
import re
import sys
import time
import warnings
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

# Silence provider-import FutureWarnings and force a quiet logger for a one-shot CLI.
warnings.filterwarnings("ignore", category=FutureWarning)
os.environ.pop("DEBUG", None)

from footnote_scan_core import (
    DEFAULT_MODEL, book_notes, book_scan_prompt, build_user_prompt, call_model,
    chunk_lines, covered_registry_for_book, dedup_candidates,
    dedupe_first_mention, entity_glossary, footnoted_anchors,
    overload_wait_seconds, provider_for_thread, resolve_system_prompt,
    source_hash, term_in_translation, verify_candidates,
)


def plan_scan(jobs, scans, force):
    """Split jobs into (to_scan, skipped, stale) against prior scan rows.

    jobs: dicts with at least "chapter" and "content_hash". A job is skipped
    only when a scan row exists with the SAME content hash; a differing hash
    means the stored source changed (trad→simp retrofit) and forces a re-scan.
    """
    to_scan, skipped, stale = [], [], []
    for job in jobs:
        prev = scans.get(job["chapter"])
        if force or prev is None:
            to_scan.append(job)
        elif prev["content_hash"] != job["content_hash"]:
            stale.append(job)
            to_scan.append(job)
        else:
            skipped.append(job)
    return to_scan, skipped, stale


# ── chapter spec ──────────────────────────────────────────────────────────────

_OPS = {">": operator.gt, ">=": operator.ge,
        "<": operator.lt, "<=": operator.le, "=": operator.eq}


def parse_chapter_spec(expr):
    """Parse a --chapters expression into a predicate(int) -> bool, or None
    for "all chapters". Comma-separated parts are OR'd; each part is one of
    N, N-M, >N, >=N, <N, <=N, =N. Raises ValueError on malformed input."""
    if not expr:
        return None
    preds = []
    for part in expr.split(","):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", part)
        if m:
            lo, hi = sorted((int(m.group(1)), int(m.group(2))))
            preds.append(lambda n, lo=lo, hi=hi: lo <= n <= hi)
            continue
        m = re.fullmatch(r"(>=|<=|>|<|=)\s*(\d+)", part)
        if m:
            fn, v = _OPS[m.group(1)], int(m.group(2))
            preds.append(lambda n, fn=fn, v=v: fn(n, v))
            continue
        if re.fullmatch(r"\d+", part):
            v = int(part)
            preds.append(lambda n, v=v: n == v)
            continue
        raise ValueError(f"Unrecognized chapter spec part: {part!r}")
    if not preds:
        raise ValueError(f"Empty chapter spec: {expr!r}")
    return lambda n: any(p(n) for p in preds)


def parse_delay(expr):
    """Parse a --delay duration into seconds (float).

    Accepts a bare number (seconds) or a number with unit: ms, s, m, h.
    Examples: "5", "5s", "500ms", "1.5m", "1h". Returns 0.0 for None/empty.
    Raises ValueError on malformed input.
    """
    if expr is None:
        return 0.0
    s = str(expr).strip().lower()
    if not s:
        return 0.0
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(ms|s|m|h)?", s)
    if not m:
        raise ValueError(
            f"Invalid delay: {expr!r}  (use e.g. 5, 5s, 500ms, 1m, 1h)")
    n = float(m.group(1))
    unit = m.group(2) or "s"
    if unit == "ms":
        return n / 1000.0
    if unit == "s":
        return n
    if unit == "m":
        return n * 60.0
    return n * 3600.0  # h


def scan_chapter(job, provider_name, model_name, book_title, max_chars,
                 overload_wait, registry=None, notes=None, system_prompt=None):
    """Worker: send one chapter (chunked if huge) to the model. Returns
    (kept, dropped) — candidates whose referent is not in the chapter source
    are hallucinations and never leave this function. Runs in a
    ThreadPoolExecutor thread; all DB writes happen on the main thread as
    futures complete."""
    provider = provider_for_thread(provider_name)
    source = "\n".join(job["lines"])
    covered = []
    if registry is not None:
        covered = registry.covered_for(job["chapter"], source)
    chunks = chunk_lines(job["lines"], max_chars)
    found = []
    for i, chunk in enumerate(chunks, 1):
        prompt = build_user_prompt(book_title, job["chapter"], job.get("title"),
                                   job["glossary"], chunk,
                                   part=i, n_parts=len(chunks), covered=covered,
                                   notes=notes)
        found.extend(call_model(provider, model_name, prompt, overload_wait,
                                label=f"ch{job['chapter']}",
                                system_prompt=system_prompt))
    # Verify against the WHOLE chapter, not the chunk: a referent the model saw
    # in part 2 is still legitimately "in the chapter".
    return verify_candidates(found, source)


def load_candidates(db, book_id, pred=None):
    rows = db.list_footnote_candidates(book_id)
    if pred is not None:
        rows = [r for r in rows if pred(r["chapter_number"])]
    return rows


# ── modes ─────────────────────────────────────────────────────────────────────

def cmd_collect(args, config, db, book):
    book_id, book_title = book["id"], book.get("title") or f"book {book['id']}"
    pred = parse_chapter_spec(args.chapters)

    chap_rows = [c for c in db.list_chapters(book_id) if c["chapter"] is not None]
    targets = [c for c in chap_rows if pred is None or pred(c["chapter"])]
    if not targets:
        print("No chapters match the spec.")
        return 0

    # Entities loaded once, matched per chapter; never saved back.
    entities_by_cat = db.reload_entities(book_id)

    from footnotes import content_to_list

    print(f"Fetching source text for {len(targets)} chapter(s)...", flush=True)
    jobs, no_source = [], []
    for c in targets:
        ch = db.get_chapter(book_id=book_id, chapter_number=c["chapter"])
        lines = content_to_list(ch.get("untranslated")) if ch else []
        text = "\n".join(lines).strip()
        if not text:
            no_source.append(c["chapter"])
            continue
        jobs.append({
            "chapter": c["chapter"],
            "title": c.get("title"),
            "lines": lines,
            "content_hash": source_hash(text),
        })

    to_scan, skipped, stale = plan_scan(jobs, db.get_footnote_scans(book_id),
                                        args.force)

    delay_sec = parse_delay(args.delay)

    # The book's own translation conventions, straight from its custom system
    # prompt. Books without one contribute nothing here.
    notes = book_notes(db, book_id)

    # The scanner's own prompt, which a book may replace wholesale (the
    # footnote_scan module's "Scan system prompt" setting — the same one the
    # on-ingest scan uses). --stock-prompt ignores it for this run.
    custom_prompt = "" if args.stock_prompt else book_scan_prompt(db, book_id)
    system_prompt = resolve_system_prompt(custom_prompt)

    print(f"Book:    {book_id} — {book_title}")
    print(f"Model:   {args.model}")
    print(f"Prompt:  " + (f"book's custom scan prompt ({len(custom_prompt)} chars)"
                          if custom_prompt else "built-in scan prompt"))
    if notes:
        print(f"Notes:   book-specific notes from the custom prompt "
              f"({len(notes)} chars)")
    print(f"Plan:    {len(to_scan)} to scan"
          + (f" ({len(stale)} source-changed)" if stale else "")
          + f", {len(skipped)} already scanned"
          + (f", {len(no_source)} with no source text" if no_source else ""))
    if delay_sec > 0:
        print(f"Delay:   {delay_sec:g}s between chapters"
              f" (workers={args.workers})")
    print("=" * 70)

    if args.dry_run:
        for job in to_scan:
            why = " (source changed)" if job in stale else ""
            print(f"  would scan ch{job['chapter']}{why}")
        print("(dry run — no model calls, nothing written)")
        return 0
    if not to_scan:
        return 0

    for job in to_scan:
        job["glossary"] = entity_glossary(db, entities_by_cat,
                                          job["lines"], job["chapter"])

    # Already-covered terms: the book's real footnotes plus previously
    # collected candidates; grows as chapters complete this run.
    registry = covered_registry_for_book(db, book_id)

    provider_name, model_name = config.parse_model_spec(args.model)
    overload_wait = overload_wait_seconds()

    n_found = n_done = n_dropped = 0
    failed = []
    remaining = list(to_scan)
    # Replenish the pool as chapters finish so --delay can pause before the
    # next chapter is launched (with --workers 1 this is a simple sleep
    # between chapters; with higher workers it paces replacements).
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {}

        def _submit_one():
            if not remaining:
                return
            job = remaining.pop(0)
            fut = pool.submit(scan_chapter, job, provider_name, model_name,
                              book_title, args.max_chars, overload_wait,
                              registry, notes, system_prompt)
            futures[fut] = job

        for _ in range(min(args.workers, len(remaining))):
            _submit_one()
        try:
            while futures:
                done, _ = wait(futures, return_when=FIRST_COMPLETED)
                for fut in done:
                    job = futures.pop(fut)
                    n_done += 1
                    try:
                        found, dropped = fut.result()
                    except Exception as e:
                        failed.append(job["chapter"])
                        print(f"[{n_done}/{len(to_scan)}] ch{job['chapter']}: "
                              f"FAILED — {e}", flush=True)
                    else:
                        # A re-scan (--force, or a chapter whose source
                        # changed) must not throw away keep/reject decisions
                        # already made on it.
                        db.record_footnote_scan(book_id, job["chapter"],
                                                job.get("title"), args.model,
                                                job["content_hash"], found,
                                                preserve_reviewed=True)
                        registry.add(job["chapter"], found)
                        n_found += len(found)
                        n_dropped += len(dropped)
                        terms = ", ".join(
                            f["term_en"] or f["term_zh"] for f in found)
                        # Dropped candidates are counted, never named: they are
                        # not in the chapter, so there is nothing to review.
                        note = (f" [{len(dropped)} not in source, dropped]"
                                if dropped else "")
                        print(f"[{n_done}/{len(to_scan)}] ch{job['chapter']}: "
                              f"{len(found)} candidate(s)"
                              + (f" — {terms}" if terms else "") + note,
                              flush=True)
                if remaining:
                    if delay_sec > 0:
                        time.sleep(delay_sec)
                    while remaining and len(futures) < args.workers:
                        _submit_one()
        except KeyboardInterrupt:
            print("\nInterrupted — completed chapters are saved; "
                  "unfinished ones will be retried next run.")
            pool.shutdown(wait=False, cancel_futures=True)
            return 130

    print("=" * 70)
    print(f"Scanned {n_done - len(failed)} chapter(s): {n_found} candidate(s) collected.")
    if n_dropped:
        print(f"Dropped {n_dropped} hallucinated candidate(s) — referent not "
              f"present in the chapter's source text.")
    # Concurrent workers in one wave can't see each other's finds — drop the
    # later pending repeats they produced (reviewed rows are never touched).
    n_dup = dedup_candidates(db, book_id)
    if n_dup:
        print(f"Deduped {n_dup} repeat candidate(s) from concurrent workers.")
    if failed:
        print(f"FAILED (will retry next run): ch "
              + ", ".join(str(c) for c in sorted(failed)))
    print(f"Review with: python3 footnote_scan.py -b {book_id} --review")
    return 1 if failed else 0


def cmd_review(args, db, book):
    pred = parse_chapter_spec(args.chapters)
    rows = load_candidates(db, book["id"], pred)
    if not rows:
        print("No candidates collected yet for this book"
              + (" / chapter range" if args.chapters else "") + ".")
        return 1
    _, repeats = dedupe_first_mention(rows)
    dup_ids = {d["id"] for dups in repeats.values() for d in dups}
    already = footnoted_anchors(db, book["id"])
    for r in rows:
        r["dup"] = r["id"] in dup_ids
        r["already"] = (r.get("term_en") or "").strip().lower() in already

    from footnote_review_tui import run_review
    counts = run_review(
        lambda cand_id, status: db.update_footnote_candidate(cand_id, status=status),
        rows, book_label=f"Book {book['id']} — {book.get('title', '')}")
    print(f"Reviewed: {counts['accepted']} kept, {counts['rejected']} rejected, "
          f"{counts['pending']} unmarked (unmarked rows still export).")
    return 0


def cmd_report(args, db, book):
    pred = parse_chapter_spec(args.chapters)
    rows = load_candidates(db, book["id"], pred)
    if not rows:
        print("No candidates collected yet.")
        return 1
    already = footnoted_anchors(db, book["id"])

    def flags(r, extra=""):
        out = []
        if r["status"] == "accepted":
            out.append("KEEP")
        elif r["status"] == "rejected":
            out.append("REJECTED")
        if (r.get("term_en") or "").strip().lower() in already:
            out.append("already footnoted")
        if extra:
            out.append(extra)
        return f"  [{', '.join(out)}]" if out else ""

    if args.all:
        shown, repeats = rows, {}
    else:
        shown, repeats = dedupe_first_mention(rows)

    by_ch = {}
    for r in shown:
        by_ch.setdefault(r["chapter_number"], []).append(r)
    for cn in sorted(by_ch):
        first = by_ch[cn][0]
        title = f" — {first['chapter_title']}" if first.get("chapter_title") else ""
        print(f"\nch{cn}{title}")
        for r in by_ch[cn]:
            also = ""
            if r["id"] in repeats:
                also = "also ch" + ", ch".join(
                    str(d["chapter_number"]) for d in repeats[r["id"]])
            print(f"  #{r['id']} {r['term_zh']} -> {r['term_en']}{flags(r, also)}")
            print(f"      {r['body']}")
    n_rej = sum(1 for r in rows if r["status"] == "rejected")
    print(f"\n{len(rows)} candidate row(s), {len(shown)} shown"
          + ("" if args.all else " (first mentions; --all for every row)")
          + f", {n_rej} rejected.")
    return 0


def cmd_prune(args, db, book):
    """Re-run the hallucination filter over candidates ALREADY in the store —
    rows collected before the filter existed, or by a model that was swapped
    out. Deletes anything whose referent isn't in the chapter's source."""
    book_id = book["id"]
    pred = parse_chapter_spec(args.chapters)
    rows = load_candidates(db, book_id, pred)
    if not rows:
        print("No candidates collected yet for this book.")
        return 1

    from footnotes import content_to_list

    by_ch = {}
    for r in rows:
        by_ch.setdefault(r["chapter_number"], []).append(r)

    doomed, by_status, no_source = [], {}, []
    for cn in sorted(by_ch):
        ch = db.get_chapter(book_id=book_id, chapter_number=cn)
        source = "\n".join(content_to_list(ch.get("untranslated"))) if ch else ""
        if not source.strip():
            no_source.append(cn)          # can't verify — leave the rows alone
            continue
        _, bad = verify_candidates(by_ch[cn], source)
        for r in bad:
            doomed.append(r)
            by_status[r["status"]] = by_status.get(r["status"], 0) + 1

    print(f"Checked {len(rows)} candidate(s) across {len(by_ch)} chapter(s).")
    if no_source:
        print(f"Skipped {len(no_source)} chapter(s) with no source text.")
    if not doomed:
        print("All candidates are present in their chapter's source. Nothing to prune.")
        return 0

    breakdown = ", ".join(f"{n} {st}" for st, n in sorted(by_status.items()))
    verb = "Would delete" if args.dry_run else "Deleted"
    if not args.dry_run:
        db.delete_footnote_candidates([r["id"] for r in doomed])
        # Keep the scan rows' n_found honest for the chapters we touched.
        for cn in {r["chapter_number"] for r in doomed}:
            db.update_footnote_scan_count(book_id, cn)
    print(f"{verb} {len(doomed)} candidate(s) not present in the source "
          f"({breakdown}).")
    return 0


def cmd_export(args, db, book):
    book_id = book["id"]
    pred = parse_chapter_spec(args.chapters)
    rows = [r for r in load_candidates(db, book_id, pred)
            if r["status"] != "rejected"]
    firsts, _ = dedupe_first_mention(rows)
    already = footnoted_anchors(db, book_id)

    out, skipped_already, no_term = {}, [], []
    for r in firsts:
        term = (r.get("term_en") or "").strip()
        if not term:
            no_term.append(r)
            continue
        if term.lower() in already:
            skipped_already.append(term)
            continue
        out.setdefault(term, r["body"])

    with open(args.export, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    print(f"Wrote {len(out)} footnote(s) to {args.export}")
    if skipped_already:
        print(f"Skipped (already footnoted in book): {', '.join(skipped_already)}")
    if no_term:
        print(f"Skipped (no English term): "
              + ", ".join(r["term_zh"] or f"#{r['id']}" for r in no_term))

    # The scan ran on SOURCE text, so term_en is the model's rendering — warn
    # about keys add_footnotes.py won't find in the translated text.
    missing = [t for t in out if not term_in_translation(db, book_id, t)]
    if missing:
        print(f"\n⚠ {len(missing)}/{len(out)} term(s) NOT found in the "
              f"translated text — edit these keys before running add_footnotes.py:")
        for t in missing:
            print(f"    {t}")
    else:
        print(f"All {len(out)} term(s) found in the translated text.")
    print(f"\nApply later with:\n"
          f"  python3 add_footnotes.py --book-id {book_id} --file {args.export} --dry-run")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Collect (and review/export) cultural-referent footnote "
                    "candidates for a book. Collector only — never modifies chapters.",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    parser.add_argument("-b", "--book", required=True, help="Book id or exact title")
    parser.add_argument("--chapters", metavar="EXPR",
                        help='e.g. "42", "1-50", ">100", "42,44,46" (default: all)')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--review", action="store_true",
                      help="Fullscreen TUI to keep/reject collected candidates")
    mode.add_argument("--report", action="store_true",
                      help="Print collected candidates grouped by chapter")
    mode.add_argument("--export", metavar="PATH",
                      help="Write {term: body} JSON for add_footnotes.py "
                           "(everything except rejected rows)")
    mode.add_argument("--print-prompt", action="store_true",
                      help="Print the scan system prompt this book would use "
                           "(its custom one, else the built-in) and exit — "
                           "the starting point for editing a copy")
    mode.add_argument("--prune-unverified", action="store_true",
                      help="Re-check stored candidates against the chapter "
                           "source and delete the ones that aren't in it "
                           "(honours --dry-run)")
    parser.add_argument("--all", action="store_true",
                        help="With --report: show every row, not just first mentions")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"provider:model spec (default {DEFAULT_MODEL})")
    parser.add_argument("--workers", type=int, default=4,
                        help="Concurrent chapters in flight (default 4)")
    parser.add_argument("--delay", metavar="DURATION", default=None,
                        help="Pause between launching chapters (e.g. 5s, 500ms, "
                             "1m). With --workers 1 this is a simple gap between "
                             "chapters; with higher workers it paces replacements.")
    parser.add_argument("--max-chars", type=int, default=20000,
                        help="Chunk size for very long chapters (default 20000)")
    parser.add_argument("--force", action="store_true",
                        help="Re-scan chapters even if already scanned")
    parser.add_argument("--stock-prompt", action="store_true",
                        help="Ignore the book's custom scan prompt for this "
                             "run and use the built-in one")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show which chapters would be scanned (or, with "
                             "--prune-unverified, what would be deleted); "
                             "no model calls")
    args = parser.parse_args()

    try:
        parse_chapter_spec(args.chapters)
    except ValueError as e:
        parser.error(str(e))
    try:
        parse_delay(args.delay)
    except ValueError as e:
        parser.error(str(e))

    from config import TranslationConfig
    from database import DatabaseManager
    from logger import Logger
    from get_entities import resolve_book

    config = TranslationConfig()
    db = DatabaseManager(config, Logger(config))
    book = resolve_book(db, args.book)
    if not book:
        print(f"Book not found: {args.book!r}", file=sys.stderr)
        return 1

    if args.print_prompt:
        custom = "" if args.stock_prompt else book_scan_prompt(db, book["id"])
        print(f"# {'custom' if custom else 'built-in'} scan prompt for book "
              f"{book['id']} — {book.get('title')}", file=sys.stderr)
        print(resolve_system_prompt(custom))
        return 0
    if args.review:
        return cmd_review(args, db, book)
    if args.report:
        return cmd_report(args, db, book)
    if args.export:
        return cmd_export(args, db, book)
    if args.prune_unverified:
        return cmd_prune(args, db, book)
    return cmd_collect(args, config, db, book)


if __name__ == "__main__":
    sys.exit(main())
