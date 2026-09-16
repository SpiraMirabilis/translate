#!/usr/bin/env python3
"""Verify existing unit annotations and strip false-positive ones.

A global run of unit_converter.py left annotations like "thirty zhang (100 m)"
all over the database. The known failure mode: the numeric regex `\\d[\\d,]*`
greedily consumed a trailing comma (e.g. "Tier-11, Zhang Yu" → captured
num=`11,` and unit=`Zhang`), then annotated a personal name as a unit.

Detection has two stages:
  1. PROGRAMMATIC — any annotation whose captured number ends in a comma
     (e.g. `11,`, `00,`, `4,`) is a deterministic bug. These are stripped
     without consulting the LLM.
  2. LLM (optional, via --cleaning-model) — for annotations that passed the
     programmatic check, send the surrounding sentence (with the parenthetical
     temporarily stripped so it doesn't bias the model) to a cleaning LLM,
     using a conservative prompt that defaults to KEEP.

Either way, the action is the same: strip only the " (X unit)" tail and leave
the original phrase intact.

Usage:
    # Programmatic strip only (catches the known bug class):
    python3 verify_unit_annotations.py --book-id 9 --dry-run
    python3 verify_unit_annotations.py --all

    # Add LLM secondary check:
    python3 verify_unit_annotations.py --all --cleaning-model claude:claude-sonnet-4-6

    # Save a dry-run plan, review, then apply it without re-running the LLM:
    python3 verify_unit_annotations.py --all --dry-run \\
        --cleaning-model claude:claude-sonnet-4-6 --save-plan plan.json
    python3 verify_unit_annotations.py --apply-plan plan.json
"""

import argparse
import datetime
import json
import logging
import os
import re
import sys
from typing import List, Optional, Set, Tuple

from config import TranslationConfig
from database import DatabaseManager
from logger import Logger
from unit_converter import (
    _extract_sentence_context,
    _fraction_phrase,
    _number_words,
    _vague_prefix,
)

# Intentionally permissive numeric — accepts a trailing comma (e.g. `11,`)
# which is the unit_converter bug we're cleaning up. The fixed unit_converter
# rejects this shape, but the existing DB annotations were inserted under the
# broken regex, so detection has to match the broken shape too.
_numeric_relaxed = r"(?:\d[\d,]*\.?\d*)"

_log = logging.getLogger(__name__)


# action='annotate' units in units.json. action='replace' units (shichen, ke,
# double-hour) replace text outright and can't be detected post-facto.
_ANNOTATE_UNITS = ["zhang", "li", "chi", "cun", "jin", "liang", "qian", "mu", "qing"]
_unit_names_re = "|".join(sorted(_ANNOTATE_UNITS, key=len, reverse=True))

# Metric units that an annotation can contain
_METRIC_UNITS_RE = r"km|cm|kg|ha|m|g"

# Mirrors unit_converter._PATTERN but REQUIRES a trailing annotation parenthetical.
# Captures the annotation tail separately as `annot` so we can strip it.
_ANNOTATION_PATTERN = re.compile(
    r"(?<!['\w])"
    r"(?!" + _vague_prefix + r")"
    r"(?:(?P<frac>" + _fraction_phrase + r")\s+of\s+)?"
    r"(?P<num>" + _numeric_relaxed + r"|a\s+single|single|another|"
    + _number_words + r"|a\s+full|an\s+full|full|a|an)"
    r"[\s\-]+"
    r"(?P<unit>" + _unit_names_re + r")"
    r"s?"
    r"(?P<annot>\s*\(\s*[\d.,]+\s+(?:" + _METRIC_UNITS_RE + r")\s*\))",
    re.IGNORECASE,
)


class _UnitPhraseMatch:
    """Match-like shim for the LLM check.

    `start()` / `end()` report the position of just the unit phrase (number +
    unit), not the trailing annotation. `num_str` is the captured number text
    (used for the programmatic trailing-comma rule). The annotation span is
    kept separately so we can strip it later. The shim's start/end is built
    against the *stripped* line so the LLM doesn't see the giveaway
    parenthetical.
    """

    __slots__ = ("_start", "_end", "orig_annot_start", "orig_annot_end", "num_str")

    def __init__(self, unit_start_stripped: int, unit_end_stripped: int,
                 orig_annot_start: int, orig_annot_end: int, num_str: str):
        self._start = unit_start_stripped
        self._end = unit_end_stripped
        self.orig_annot_start = orig_annot_start
        self.orig_annot_end = orig_annot_end
        self.num_str = num_str

    def start(self) -> int:
        return self._start

    def end(self) -> int:
        return self._end

    def has_trailing_comma_num(self) -> bool:
        """True if the captured number ends with a comma not followed by digits.

        This is the unit_converter regex bug signature: `\\d[\\d,]*` greedily
        consumed a trailing comma (e.g. `11,` from "Tier-11, Zhang Yu"), so the
        annotation was inserted on a personal-name token, not a real unit.
        """
        return self.num_str.endswith(",")


def _scan_line(line: str) -> Tuple[str, List[_UnitPhraseMatch]]:
    """Return (stripped_line, matches).

    `stripped_line` has every annotation tail removed. Each match's
    start()/end() points into `stripped_line`; `orig_annot_*` points into
    the original `line` so removal can be applied later.
    """
    matches: List[_UnitPhraseMatch] = []
    raw_matches = list(_ANNOTATION_PATTERN.finditer(line))
    if not raw_matches:
        return line, matches

    parts: List[str] = []
    last_end_orig = 0
    cumulative_removed = 0
    for m in raw_matches:
        annot_start_orig = m.start("annot")
        annot_end_orig = m.end("annot")
        unit_start_orig = m.start()
        unit_end_orig = annot_start_orig  # unit phrase ends where annotation starts

        # Append text up to (but not including) the annotation tail
        parts.append(line[last_end_orig:annot_start_orig])
        last_end_orig = annot_end_orig

        unit_start_stripped = unit_start_orig - cumulative_removed
        unit_end_stripped = unit_end_orig - cumulative_removed
        cumulative_removed += (annot_end_orig - annot_start_orig)

        matches.append(_UnitPhraseMatch(
            unit_start_stripped, unit_end_stripped,
            annot_start_orig, annot_end_orig,
            m.group("num"),
        ))

    parts.append(line[last_end_orig:])
    return "".join(parts), matches


def _load_verification_prompt() -> str:
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "prompts", "unit_verification_prompt.txt")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _llm_flag_false_positives(stripped_lines: List[str],
                              all_matches: list,
                              cleaning_model: str) -> Set[int]:
    """Call the cleaning LLM with the verification (conservative) prompt.

    `all_matches` is a list of (line_idx, _UnitPhraseMatch, match_id).
    Returns the set of match_ids the model flagged as false positives.
    Mirrors unit_converter._filter_false_positives but uses our own prompt
    and re-raises so a real LLM failure doesn't silently no-op.
    """
    from providers import create_provider
    config = TranslationConfig()

    context = {}
    for line_idx, match, match_id in all_matches:
        line = stripped_lines[line_idx]
        sentence, ctx_offset = _extract_sentence_context(
            line, match.start(), match.end(),
        )
        rel_start = match.start() - ctx_offset
        rel_end = match.end() - ctx_offset
        highlighted = (sentence[:rel_start] + ">>>" +
                       sentence[rel_start:rel_end] + "<<<" +
                       sentence[rel_end:])
        context[str(match_id)] = highlighted

    if not context:
        return set()

    system_prompt = _load_verification_prompt()
    user_prompt = json.dumps(context, ensure_ascii=False, indent=2)

    provider_name, model_name = config.parse_model_spec(cleaning_model)
    provider = create_provider(provider_name)

    _log.info(f"Verifying {len(context)} unit annotation(s) with {model_name}...")

    response = provider.chat_completion(
        model=model_name,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.0,
    )

    raw = provider.get_response_content(response).strip()
    if raw.startswith("```"):
        raw_lines = raw.split("\n")
        raw = "\n".join(raw_lines[1:-1]) if len(raw_lines) > 2 else raw
        if raw.startswith("json"):
            raw = raw[4:].strip()

    parsed = json.loads(raw)
    if not isinstance(parsed, list):
        _log.warning("Verification model returned non-list; treating as empty.")
        return set()

    return {int(x) for x in parsed if str(x) in context}


def _strip_annotations_in_line(line: str, matches: List[_UnitPhraseMatch]) -> str:
    """Strip the annotation tails for the supplied matches from `line`.

    `matches` carry the annotation spans in the *original* line coords. We
    apply them in reverse offset order so earlier positions stay stable.
    """
    out = line
    for m in sorted(matches, key=lambda x: x.orig_annot_start, reverse=True):
        out = out[:m.orig_annot_start] + out[m.orig_annot_end:]
    return out


def process_chapter(db: DatabaseManager, book_id: int, ch_num: int,
                    cleaning_model: Optional[str], dry_run: bool,
                    verbose: bool) -> Tuple[int, int, int, Optional[dict]]:
    """Returns (annotations_seen, false_positives_stripped, lines_changed,
    chapter_plan).

    `chapter_plan` is None when the chapter has no changes. Otherwise it's a
    dict suitable for serializing into a re-applicable plan file:
        {
          "book_id": ..., "chapter_number": ..., "title": ...,
          "line_changes": [{"line_idx": i, "old": "...", "new": "..."}, ...]
        }
    """
    full = db.get_chapter(book_id=book_id, chapter_number=ch_num)
    if not full:
        return 0, 0, 0, None

    lines = full["content"] or []
    if not lines:
        return 0, 0, 0, None

    # Pass 1: scan every line, build stripped-context lines + match shims
    stripped_lines: List[str] = []
    matches_by_line: dict = {}
    all_matches: list = []  # (line_idx, shim, match_id)
    for line_idx, line in enumerate(lines):
        stripped, line_matches = _scan_line(line)
        stripped_lines.append(stripped)
        for m in line_matches:
            mid = len(all_matches)
            all_matches.append((line_idx, m, mid))
            matches_by_line.setdefault(line_idx, []).append((m, mid))

    if not all_matches:
        return 0, 0, 0, None

    # Pass 2a: programmatic — any annotation whose captured number ends in a
    # comma was inserted by the unit_converter bug; strip without LLM.
    fp_ids: Set[int] = set()
    for _, m, mid in all_matches:
        if m.has_trailing_comma_num():
            fp_ids.add(mid)

    # Pass 2b: optional LLM check on the survivors
    if cleaning_model:
        survivors = [(li, m, mid) for li, m, mid in all_matches if mid not in fp_ids]
        if survivors:
            llm_fps = _llm_flag_false_positives(stripped_lines, survivors, cleaning_model)
            fp_ids |= llm_fps

    # Pass 3: strip annotation tails for false positives in the *original* lines
    if not fp_ids:
        if verbose:
            print(f"  Ch {ch_num}: {len(all_matches)} annotation(s) checked — all OK")
        return len(all_matches), 0, 0, None

    new_lines = list(lines)
    examples: List[Tuple[str, str]] = []
    stripped_count = 0
    for line_idx, pairs in matches_by_line.items():
        fp_matches = [m for m, mid in pairs if mid in fp_ids]
        if not fp_matches:
            continue
        original = new_lines[line_idx]
        new_lines[line_idx] = _strip_annotations_in_line(original, fp_matches)
        stripped_count += len(fp_matches)
        if len(examples) < 4:
            # Capture a snippet around the first stripped annotation
            m0 = sorted(fp_matches, key=lambda x: x.orig_annot_start)[0]
            ctx_a = max(0, m0.orig_annot_start - 40)
            ctx_b = min(len(original), m0.orig_annot_end + 40)
            before = original[ctx_a:ctx_b]
            # Build the matching `after` snippet
            after_full = new_lines[line_idx]
            delta = sum(
                (mm.orig_annot_end - mm.orig_annot_start)
                for mm in fp_matches
                if mm.orig_annot_end <= m0.orig_annot_start
            )
            after = after_full[ctx_a - delta:ctx_b - delta -
                               (m0.orig_annot_end - m0.orig_annot_start)]
            examples.append((before, after))

    lines_changed = sum(1 for a, b in zip(lines, new_lines) if a != b)

    print(f"  Ch {ch_num}: {len(all_matches)} found, "
          f"{stripped_count} stripped (false positives), "
          f"{lines_changed} line(s) changed")
    for before, after in examples:
        print(f"     - …{before.strip()}…")
        print(f"     + …{after.strip()}…")

    chapter_plan = {
        "book_id": book_id,
        "chapter_number": ch_num,
        "title": full["title"],
        "line_changes": [
            {"line_idx": i, "old": lines[i], "new": new_lines[i]}
            for i in range(len(lines))
            if lines[i] != new_lines[i]
        ],
    }

    if not dry_run:
        db.save_chapter(
            book_id,
            ch_num,
            full["title"],
            full["untranslated"],
            new_lines,
            summary=full.get("summary"),
            translation_model=full.get("model"),
        )

    return len(all_matches), stripped_count, lines_changed, chapter_plan


def process_book(db: DatabaseManager, book_id: int, cleaning_model: Optional[str],
                 dry_run: bool, verbose: bool,
                 plan_chapters: Optional[list]) -> Tuple[int, int, int]:
    book = db.get_book(book_id=book_id)
    if not book:
        print(f"Book {book_id} not found.", file=sys.stderr)
        return 0, 0, 0

    chapters = db.list_chapters(book_id) or []
    print(f"\nBook {book_id}: {book['title']!r} ({len(chapters)} chapters)")

    tot_seen = tot_fp = tot_lines = 0
    for meta in chapters:
        s, fp, ln, ch_plan = process_chapter(
            db, book_id, meta["chapter"], cleaning_model, dry_run, verbose,
        )
        tot_seen += s
        tot_fp += fp
        tot_lines += ln
        if ch_plan is not None and plan_chapters is not None:
            plan_chapters.append(ch_plan)

    print(f"  → Book {book_id} totals: {tot_seen} annotation(s) seen, "
          f"{tot_fp} stripped, {tot_lines} line(s) changed.")
    return tot_seen, tot_fp, tot_lines


def apply_plan(db: DatabaseManager, plan_path: str) -> int:
    """Apply a previously-saved plan file. No scanning, no LLM, just per-line
    delta application with verification.
    """
    with open(plan_path, "r", encoding="utf-8") as f:
        plan = json.load(f)

    meta = plan.get("metadata", {})
    chapters = plan.get("chapters", [])
    print(f"Applying plan {plan_path!r}")
    if meta:
        print(f"  generated_at: {meta.get('generated_at', '?')}")
        print(f"  cleaning_model: {meta.get('cleaning_model', 'disabled')}")
        totals = meta.get("totals", {})
        if totals:
            print(f"  plan totals: {totals}")
    print(f"  chapters in plan: {len(chapters)}")

    applied_chapters = skipped_chapters = 0
    applied_lines = skipped_lines = 0

    for ch in chapters:
        bid = ch["book_id"]
        cn = ch["chapter_number"]
        full = db.get_chapter(book_id=bid, chapter_number=cn)
        if not full:
            print(f"  WARN: book {bid} ch {cn} not found — skipping", file=sys.stderr)
            skipped_chapters += 1
            continue

        content = list(full["content"] or [])
        chapter_mismatches = 0
        chapter_applied = 0
        for change in ch["line_changes"]:
            i = change["line_idx"]
            if i >= len(content):
                chapter_mismatches += 1
                continue
            if content[i] == change["new"]:
                # Already applied (idempotent re-run)
                continue
            if content[i] != change["old"]:
                chapter_mismatches += 1
                continue
            content[i] = change["new"]
            chapter_applied += 1

        if chapter_mismatches:
            skipped_lines += chapter_mismatches
            print(f"  WARN: book {bid} ch {cn}: {chapter_mismatches} line(s) "
                  f"no longer match plan (skipped)", file=sys.stderr)

        if chapter_applied == 0:
            skipped_chapters += 1
            continue

        db.save_chapter(
            bid, cn,
            full["title"], full["untranslated"], content,
            summary=full.get("summary"),
            translation_model=full.get("model"),
        )
        applied_chapters += 1
        applied_lines += chapter_applied
        print(f"  Applied book {bid} ch {cn}: {chapter_applied} line(s) updated")

    print()
    print(f"Applied: {applied_chapters} chapter(s), {applied_lines} line(s).")
    if skipped_chapters or skipped_lines:
        print(f"Skipped: {skipped_chapters} chapter(s), {skipped_lines} line(s) "
              f"(no match or already applied).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--book-id", type=int, help="Process a single book by ID.")
    group.add_argument("--all", action="store_true", help="Process every book.")
    group.add_argument("--apply-plan", metavar="PATH",
                       help="Apply a previously-saved plan JSON file. "
                            "No scanning, no LLM calls.")
    parser.add_argument(
        "--cleaning-model",
        default=None,
        help=("Optional LLM spec (e.g. claude:claude-sonnet-4-6) used as a "
              "secondary check on annotations the regex didn't already flag. "
              "Omit to run programmatic-only (the trailing-comma rule alone "
              "catches the known unit_converter bug class)."),
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change without writing to the DB.")
    parser.add_argument("--save-plan", metavar="PATH", default=None,
                        help="Write per-chapter line deltas to PATH. Works "
                             "with --dry-run (preview then apply later) or "
                             "with a real run (audit log).")
    parser.add_argument("--verbose", action="store_true",
                        help="Print per-chapter status even when nothing changes.")
    args = parser.parse_args()

    config = TranslationConfig()
    logger = Logger(config)
    db = DatabaseManager(config, logger)

    if args.apply_plan:
        if args.cleaning_model or args.dry_run or args.save_plan:
            parser.error("--apply-plan cannot be combined with --cleaning-model, "
                         "--dry-run, or --save-plan.")
        return apply_plan(db, args.apply_plan)

    print(f"Mode: {'DRY RUN' if args.dry_run else 'WRITE'}")
    print(f"Programmatic filter: trailing-comma `num` capture")
    print(f"LLM secondary check: {args.cleaning_model or 'disabled'}")
    if args.save_plan:
        print(f"Plan will be saved to: {args.save_plan}")

    if args.all:
        book_ids = [b["id"] for b in (db.list_books() or [])]
    else:
        book_ids = [args.book_id]

    plan_chapters: list = []  # always collect; only persisted if --save-plan set
    grand_seen = grand_fp = grand_lines = 0
    for bid in book_ids:
        s, fp, ln = process_book(
            db, bid, args.cleaning_model, args.dry_run, args.verbose, plan_chapters,
        )
        grand_seen += s
        grand_fp += fp
        grand_lines += ln

    print()
    print(f"Total annotations seen:    {grand_seen}")
    print(f"Total false positives:     {grand_fp}")
    print(f"Total lines changed:       {grand_lines}")
    if args.dry_run:
        print("(Dry run — no changes written. "
              "Re-run without --dry-run to apply, or pass --apply-plan if you saved one.)")

    if args.save_plan:
        plan = {
            "metadata": {
                "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
                "cleaning_model": args.cleaning_model,
                "dry_run": bool(args.dry_run),
                "scope": ("all-books" if args.all else f"book-{args.book_id}"),
                "totals": {
                    "annotations_seen": grand_seen,
                    "false_positives": grand_fp,
                    "lines_changed": grand_lines,
                    "chapters_in_plan": len(plan_chapters),
                },
            },
            "chapters": plan_chapters,
        }
        with open(args.save_plan, "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False, indent=2)
        print(f"Plan written to {args.save_plan} "
              f"({len(plan_chapters)} chapter(s) with changes).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
