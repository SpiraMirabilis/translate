#!/usr/bin/env python3
"""
Print the untranslated context of one or more entities' appearances in a book.

Given a book and a comma-separated list of untranslated entity strings, find the
chapters whose source text contains each string, then print whichever is longer:
the 3-paragraph window around the hit (prev/current/next) or an 80-char window
before and after the match.

--mentions selects which occurrences to print (1-indexed, global across all
chapters; defaults to "1", the first mention). Missing occurrences are skipped
silently. Use '$' to mean "the last occurrence" (resolved per entity).

Usage:
    python get_entity_context.py --book 1 --entities 明洛
    python get_entity_context.py -b "Cosmic Entrance Exam" -e 明洛,赵子轩,白虎
    # Show just the 2nd occurrence (opt out of the default first):
    python get_entity_context.py -b 1 -e 明洛 --mentions 2
    # Show 1st + 2nd:
    python get_entity_context.py -b 1 -e 明洛 --mentions 1,2
    # Show 3rd + 5th + 7th occurrences:
    python get_entity_context.py -b 1 -e 明洛 --mentions 3,5,7
    # Show just the last occurrence (scans every chapter that mentions it):
    python get_entity_context.py -b 1 -e 明洛 --mentions '$'
    # Show 1st + 2nd + last:
    python get_entity_context.py -b 1 -e 明洛 --mentions '1,2,$'
    # Read entities from stdin (one per line or comma-separated):
    echo "明洛,赵子轩" | python get_entity_context.py -b 1 -e -
    # Restrict the search to specific chapters (list, range, or comparison):
    python get_entity_context.py -b 1 -e 明洛 --chapters 5,7,9
    python get_entity_context.py -b 1 -e 明洛 --chapters 1-20
    python get_entity_context.py -b 1 -e 明洛 --chapters '>20'
    python get_entity_context.py -b 1 -e 明洛 --chapters '<=20'
"""

import argparse
import json
import os
import sys
import warnings

# Silence any FutureWarnings emitted at provider import time.
warnings.filterwarnings("ignore", category=FutureWarning)
# Force quiet logger regardless of DEBUG env var — this is a one-shot CLI.
os.environ.pop("DEBUG", None)

from config import TranslationConfig
from database import DatabaseManager
from logger import Logger


def resolve_book(db_manager, book_arg):
    if book_arg.isdigit():
        book = db_manager.get_book(book_id=int(book_arg))
        if book:
            return book
    return db_manager.get_book(title=book_arg)


def find_occurrences(db_manager, book_id, entity, max_n, chapter_filter=None):
    """Find up to max_n occurrences of entity across all chapters, in order.

    Returns (hits, matched_chapters) where hits is a list of dicts with keys
    chapter_number, paragraphs, hit_idx, occurrence_in_paragraph; and
    matched_chapters is the list of (chapter_number, chapter) tuples whose
    raw text matched the LIKE query (used as a fallback if paragraph-splitting
    drops the match).

    `chapter_filter`, if given, is a predicate fn(chapter_number) -> bool that
    restricts the search to chapters for which it returns True.
    """
    conn = db_manager.backend.get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT chapter_number
            FROM chapters
            WHERE book_id = ? AND untranslated_content LIKE ?
            ORDER BY chapter_number
            """,
            (book_id, f"%{entity}%"),
        )
        chapter_numbers = [row[0] for row in cursor.fetchall()]
    finally:
        conn.close()

    if chapter_filter is not None:
        chapter_numbers = [n for n in chapter_numbers if chapter_filter(n)]

    hits = []
    matched_chapters = []
    for chapter_number in chapter_numbers:
        if len(hits) >= max_n:
            break
        chapter = db_manager.get_chapter(book_id=book_id, chapter_number=chapter_number)
        if not chapter:
            continue
        matched_chapters.append((chapter_number, chapter))
        paragraphs = paragraphs_from_chapter(chapter)
        for i, paragraph in enumerate(paragraphs):
            if len(hits) >= max_n:
                break
            count_in_para = paragraph.count(entity)
            for occ_idx in range(count_in_para):
                if len(hits) >= max_n:
                    break
                hits.append({
                    "chapter_number": chapter_number,
                    "paragraphs": paragraphs,
                    "hit_idx": i,
                    "occurrence_in_paragraph": occ_idx,
                })
    return hits, matched_chapters


def paragraphs_from_chapter(chapter):
    """Return chapter untranslated text as a list of paragraphs."""
    raw = chapter.get("untranslated")
    if isinstance(raw, list):
        paragraphs = raw
    elif isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            paragraphs = parsed if isinstance(parsed, list) else raw.split("\n")
        except json.JSONDecodeError:
            paragraphs = raw.split("\n")
    else:
        paragraphs = []
    return [p for p in paragraphs if p is not None and str(p).strip()]


def parse_entities(raw, delimiter):
    """Split a raw entity argument into a deduplicated, ordered list."""
    if raw == "-":
        raw = sys.stdin.read()
    # Allow newlines as additional separators so stdin "one per line" works.
    pieces = []
    for line in raw.splitlines() or [raw]:
        pieces.extend(line.split(delimiter))
    seen = set()
    result = []
    for piece in pieces:
        e = piece.strip()
        if e and e not in seen:
            seen.add(e)
            result.append(e)
    return result


def parse_mentions(raw):
    """Parse the --mentions argument into a list of occurrence numbers.

    Each entry is either a positive int or the string '$' (last occurrence).
    """
    mentions = []
    seen = set()
    for piece in (raw or "").split(","):
        s = piece.strip()
        if not s:
            continue
        if s == "$":
            if "$" not in seen:
                seen.add("$")
                mentions.append("$")
            continue
        try:
            n = int(s)
        except ValueError:
            print(f"error: invalid --mentions value {s!r}, must be a positive integer or '$'",
                  file=sys.stderr)
            sys.exit(1)
        if n < 1:
            print(f"error: --mentions values must be positive integers (got {n})",
                  file=sys.stderr)
            sys.exit(1)
        if n not in seen:
            seen.add(n)
            mentions.append(n)
    return mentions


def parse_chapter_filter(raw):
    """Parse the --chapters argument into a predicate fn(chapter_number)->bool.

    Returns None if `raw` is empty (no filtering). Supports a comma-separated
    list whose pieces may be:
      - a single number:        5
      - an inclusive range:     1-20
      - a comparison:           >20, >=20, <20, <=20, =20
    A chapter matches if it satisfies any piece (pieces are OR-ed together).
    """
    raw = (raw or "").strip()
    if not raw:
        return None

    predicates = []
    for piece in raw.split(","):
        s = piece.strip()
        if not s:
            continue
        try:
            if s.startswith(">="):
                n = int(s[2:]); predicates.append(lambda c, n=n: c >= n)
            elif s.startswith("<="):
                n = int(s[2:]); predicates.append(lambda c, n=n: c <= n)
            elif s.startswith(">"):
                n = int(s[1:]); predicates.append(lambda c, n=n: c > n)
            elif s.startswith("<"):
                n = int(s[1:]); predicates.append(lambda c, n=n: c < n)
            elif s.startswith("="):
                n = int(s[1:]); predicates.append(lambda c, n=n: c == n)
            elif "-" in s:
                lo_s, hi_s = s.split("-", 1)
                lo, hi = int(lo_s), int(hi_s)
                if lo > hi:
                    lo, hi = hi, lo
                predicates.append(lambda c, lo=lo, hi=hi: lo <= c <= hi)
            else:
                n = int(s); predicates.append(lambda c, n=n: c == n)
        except ValueError:
            print(f"error: invalid --chapters value {s!r}", file=sys.stderr)
            sys.exit(1)

    if not predicates:
        return None
    return lambda c: any(p(c) for p in predicates)


def _cjk_ratio(text):
    """Fraction of non-space chars that are CJK (Hangul syllables/jamo or Han).

    Korean web-novel paragraphs are short, so the per-hit char window usually
    wins over the paragraph window; an 80-char radius is too tight for CJK,
    where each char carries more meaning. Used to auto-scale the radius.
    """
    cjk = 0
    total = 0
    for ch in text:
        if ch.isspace():
            continue
        total += 1
        o = ord(ch)
        if (
            0xAC00 <= o <= 0xD7A3      # Hangul syllables
            or 0x1100 <= o <= 0x11FF   # Hangul Jamo
            or 0x3130 <= o <= 0x318F   # Hangul compatibility Jamo
            or 0x4E00 <= o <= 0x9FFF   # CJK unified ideographs
            or 0x3400 <= o <= 0x4DBF   # CJK ext A
            or 0xF900 <= o <= 0xFAFF   # CJK compatibility ideographs
        ):
            cjk += 1
    return (cjk / total) if total else 0.0


def _char_radius(flat):
    """Char-window radius: 160 for CJK-heavy source, 80 otherwise."""
    return 160 if _cjk_ratio(flat) > 0.3 else 80


def _paragraph_offsets(paragraphs, separator):
    """Return list of char offsets where each paragraph starts in flat text."""
    sep_len = len(separator)
    offsets = []
    cur = 0
    for p in paragraphs:
        offsets.append(cur)
        cur += len(p) + sep_len
    return offsets


def compute_hit_window(hit, entity, separator, chapter_cache):
    """Compute char-range windows for a single hit.

    Returns a dict with chosen (char_lo, char_hi) — the longer of the
    paragraph window vs ±80-char window, matching the original per-hit rule —
    plus metadata used for header rendering and downstream merging.

    `chapter_cache` keyed by chapter_number memoizes flat text and offsets.
    """
    paragraphs = hit["paragraphs"]
    hit_idx = hit["hit_idx"]
    chapter_number = hit["chapter_number"]
    occ_in_para = hit["occurrence_in_paragraph"]

    cached = chapter_cache.get(chapter_number)
    if cached is None:
        flat = separator.join(paragraphs)
        offsets = _paragraph_offsets(paragraphs, separator)
        cached = {"flat": flat, "offsets": offsets, "radius": _char_radius(flat)}
        chapter_cache[chapter_number] = cached
    flat = cached["flat"]
    offsets = cached["offsets"]
    radius = cached["radius"]

    para_lo = max(0, hit_idx - 1)
    para_hi = min(len(paragraphs), hit_idx + 2)
    char_lo_para = offsets[para_lo]
    char_hi_para = offsets[para_hi - 1] + len(paragraphs[para_hi - 1])

    skip = sum(p.count(entity) for p in paragraphs[:hit_idx])
    target = skip + occ_in_para
    pos = -1
    for _ in range(target + 1):
        pos = flat.find(entity, pos + 1)
        if pos == -1:
            break

    if pos == -1:
        char_lo_char = char_hi_char = char_lo_para
    else:
        char_lo_char = max(0, pos - radius)
        char_hi_char = min(len(flat), pos + len(entity) + radius)

    para_span = char_hi_para - char_lo_para
    char_span = char_hi_char - char_lo_char
    if char_span > para_span:
        char_lo, char_hi, mode = char_lo_char, char_hi_char, f"±{radius} chars"
    else:
        char_lo, char_hi, mode = char_lo_para, char_hi_para, "±1 paragraph"

    return {
        "chapter_number": chapter_number,
        "paragraphs": paragraphs,
        "flat": flat,
        "hit_idx": hit_idx,
        "char_lo": char_lo,
        "char_hi": char_hi,
        "mode": mode,
    }


def merge_windows(windows):
    """Group windows by chapter, sort, and merge overlapping/touching intervals.

    Input: list of (occurrence_n, window) tuples in occurrence order.
    Output: list of merged groups; each group is a dict:
        {chapter_number, paragraphs, flat, char_lo, char_hi, members}
    where members is a list of {occurrence, hit_idx, mode} in occurrence order.
    """
    by_chapter = {}
    chapter_order = []
    for n, w in windows:
        cn = w["chapter_number"]
        if cn not in by_chapter:
            by_chapter[cn] = []
            chapter_order.append(cn)
        by_chapter[cn].append((n, w))

    merged = []
    for cn in chapter_order:
        entries = sorted(by_chapter[cn], key=lambda x: (x[1]["char_lo"], x[0]))
        current = None
        for n, w in entries:
            if current is None:
                current = {
                    "chapter_number": cn,
                    "paragraphs": w["paragraphs"],
                    "flat": w["flat"],
                    "char_lo": w["char_lo"],
                    "char_hi": w["char_hi"],
                    "members": [{"occurrence": n, "hit_idx": w["hit_idx"], "mode": w["mode"]}],
                }
                continue
            if w["char_lo"] <= current["char_hi"]:
                current["char_hi"] = max(current["char_hi"], w["char_hi"])
                current["members"].append({"occurrence": n, "hit_idx": w["hit_idx"], "mode": w["mode"]})
            else:
                merged.append(current)
                current = {
                    "chapter_number": cn,
                    "paragraphs": w["paragraphs"],
                    "flat": w["flat"],
                    "char_lo": w["char_lo"],
                    "char_hi": w["char_hi"],
                    "members": [{"occurrence": n, "hit_idx": w["hit_idx"], "mode": w["mode"]}],
                }
        if current is not None:
            merged.append(current)
    for g in merged:
        g["members"].sort(key=lambda m: m["occurrence"])
    return merged


def render_merged_group(book, entity, group):
    """Build (header, body) for a merged group of one or more hits."""
    members = group["members"]
    paragraphs = group["paragraphs"]
    flat = group["flat"]
    total_paragraphs = len(paragraphs)
    chapter_number = group["chapter_number"]

    body = flat[group["char_lo"]:group["char_hi"]]

    occ_str = ",".join(f"#{m['occurrence']}" for m in members)
    para_str = ",".join(str(m["hit_idx"] + 1) for m in members)
    if len(members) == 1:
        mode_tag = members[0]["mode"]
        occ_label = "occurrence"
        para_label = "paragraph"
    else:
        mode_tag = "merged"
        occ_label = "occurrences"
        para_label = "paragraphs"

    header = (
        f"# {book.get('title')} (book_id={book['id']}) — chapter {chapter_number}"
        f" — entity {entity!r} {occ_label} {occ_str}"
        f" at {para_label} {para_str}/{total_paragraphs}"
        f" [{mode_tag}]"
    )
    return header, body


def contexts_for_entity(db_manager, book, entity, separator, occurrences, chapter_filter=None):
    """Resolve contexts for each requested occurrence of an entity.

    Returns a list of dicts: {header, body, occurrences: list[int], is_last,
    ok}. Adjacent hits within a chapter whose context windows overlap or
    touch are merged into a single result. Missing higher occurrences are
    silently omitted. If the entity is not found at all, returns a single
    ok=False entry.
    """
    if not occurrences:
        return []
    needs_last = "$" in occurrences
    int_occurrences = [o for o in occurrences if isinstance(o, int)]
    if needs_last:
        max_n = float("inf")
    elif int_occurrences:
        max_n = max(int_occurrences)
    else:
        max_n = 0
    hits, matched_chapters = find_occurrences(db_manager, book["id"], entity, max_n, chapter_filter)

    if not hits:
        if matched_chapters:
            # LIKE matched at least one chapter but paragraph-splitting dropped
            # the entity — fall back to printing the first such chapter whole.
            chapter_number, chapter = matched_chapters[0]
            paragraphs = paragraphs_from_chapter(chapter)
            header = (
                f"# {entity!r}: matched chapter {chapter_number} but no paragraph "
                f"contained it after splitting; printing full chapter"
            )
            return [{
                "header": header,
                "body": "\n".join(paragraphs),
                "occurrences": [1],
                "is_last": False,
                "ok": True,
            }]
        return [{
            "header": f"# {entity!r}: NOT FOUND in book {book['id']} ({book.get('title')!r})",
            "body": "",
            "occurrences": [1],
            "is_last": False,
            "ok": False,
        }]

    last_n = len(hits)
    resolved = []
    seen = set()
    for o in occurrences:
        n = last_n if o == "$" else o
        if n < 1 or n > last_n:
            continue
        if n not in seen:
            seen.add(n)
            resolved.append(n)
    resolved.sort()

    chapter_cache = {}
    windows = []
    for n in resolved:
        hit = hits[n - 1]
        w = compute_hit_window(hit, entity, separator, chapter_cache)
        windows.append((n, w))

    groups = merge_windows(windows)

    results = []
    for g in groups:
        header, body = render_merged_group(book, entity, g)
        occ_list = [m["occurrence"] for m in g["members"]]
        results.append({
            "header": header,
            "body": body,
            "occurrences": occ_list,
            "is_last": last_n in occ_list,
            "ok": True,
        })
    return results


def main():
    parser = argparse.ArgumentParser(
        description="Print untranslated context (prev/current/next paragraph) "
                    "of one or more entities' appearances in a book.",
    )
    parser.add_argument("--book", "-b", required=True,
                        help="Book ID (numeric) or exact title.")
    parser.add_argument("--entities", "--entity", "-e", required=True, dest="entities",
                        help="One or more untranslated entity strings, separated by "
                             "the delimiter (default ','). Pass '-' to read from stdin.")
    parser.add_argument("--mentions", default="1",
                        help="Comma-separated occurrence numbers to show "
                             "(1-indexed, global across chapters). Default '1' "
                             "shows only the first mention. Use '$' for the last "
                             "occurrence (scans every matching chapter). Missing "
                             "occurrences are skipped silently. Examples: '2' shows "
                             "only the 2nd; '1,2' shows 1st+2nd; '3,5,7' shows "
                             "3rd+5th+7th; '$' shows only the last; '1,2,$' shows "
                             "1st+2nd+last.")
    parser.add_argument("--chapters", default=None,
                        help="Restrict the search to specific chapters. Accepts a "
                             "comma-separated list of numbers, inclusive ranges, "
                             "and comparisons. Examples: '5,7,9'; '1-20'; '>20'; "
                             "'<=20'; '1-20,30,>100'.")
    parser.add_argument("--delimiter", default=",",
                        help="Delimiter between entities in --entities (default: ',').")
    parser.add_argument("--separator", default="\n\n",
                        help="Separator between paragraphs within a context block "
                             "(default: blank line).")
    parser.add_argument("--divider", default="\n" + ("-" * 60) + "\n",
                        help="Divider printed between batch entries on stdout.")
    args = parser.parse_args()

    entities = parse_entities(args.entities, args.delimiter)
    if not entities:
        print("error: no entities provided", file=sys.stderr)
        sys.exit(1)

    occurrences = parse_mentions(args.mentions)
    if not occurrences:
        print("error: --mentions resolved to no occurrences", file=sys.stderr)
        sys.exit(1)

    chapter_filter = parse_chapter_filter(args.chapters)

    config = TranslationConfig()
    logger = Logger(config)
    db_manager = DatabaseManager(config, logger)

    book = resolve_book(db_manager, args.book)
    if not book:
        print(f"error: book not found: {args.book!r}", file=sys.stderr)
        sys.exit(1)

    any_failure = False
    first_block = True
    for entity in entities:
        results = contexts_for_entity(db_manager, book, entity, args.separator, occurrences, chapter_filter)
        for r in results:
            if not r["ok"]:
                any_failure = True
            print(r["header"], file=sys.stderr)
            if not first_block:
                sys.stdout.write(args.divider)
            first_block = False
            marker = f"## {entity}"
            if occurrences != [1]:
                occ_list = r.get("occurrences", [])
                joined = "+".join(f"#{n}" for n in occ_list)
                label = "occurrence" if len(occ_list) == 1 else "occurrences"
                suffix = f"{label} {joined}"
                if r.get("is_last") and "$" in occurrences:
                    suffix += ", last"
                marker += f" ({suffix})"
            sys.stdout.write(marker + "\n")
            if r["body"]:
                sys.stdout.write(r["body"])
                if not r["body"].endswith("\n"):
                    sys.stdout.write("\n")
            else:
                sys.stdout.write("(no context available)\n")

    if any_failure and len(entities) == 1:
        sys.exit(1)


if __name__ == "__main__":
    main()
