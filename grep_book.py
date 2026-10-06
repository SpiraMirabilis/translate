#!/usr/bin/env python3
"""
Search a book's chapter text and print every WHOLE matching line.

Covers both halves of a book's text: chapters already translated (the
`chapters` table) and chapters still awaiting translation (the `queue`
table). Searching one alone silently under-reports on an actively
translating book — the review frontier is exactly where the two meet.

Usage:
    python grep_book.py -b 77 "唐装"                       # source, whole book
    python grep_book.py -b 77 --field en "Tang suit"        # translated text
    python grep_book.py -b 77 --field both --chapters 1-15 "核力"
    python grep_book.py -b 77 -F "[TABLE]"                  # literal, no regex
    python grep_book.py -b 77 -i --context 2 "gaokao"       # case-insensitive
    python grep_book.py -b 77 --count "十八区"              # per-chapter tallies
    python grep_book.py -b 77 --field en -i 'gaokao|hukou'  # tags each line with the term that hit

Every printed line is prefixed with the substring(s) that actually matched
(`ch1 [Honor of Kings] [50] …`), so a one-pass scan over an alternation of a
dozen terms stays legible — you can see which term fired without re-reading
the sentence. Pass --no-match-tag for the older bare format.

Filter syntax for --chapters (same as get_entities.py --origin-chapter):
    N         exact chapter
    N-M       inclusive range
    >N, >=N   greater than (or equal)
    <N, <=N   less than (or equal)
    a,b,c     comma-separated list of any of the above

Why this exists — two traps it is built to avoid:

  1. **Substring greps lie.** Matching `urn` inside "returned" or `ant`
     inside "instant" produces confident nonsense. This always prints the
     full line so a match can be judged in context, never a bare count.
  2. **Queued chapters are invisible to the chapters table.** Pre-review
     term censuses (does this entity actually occur? where does it first
     appear?) must span both, or every number is wrong by however much of
     the book is already translated.

Read-only: this script never writes. See TranslationRepairTask.md and
IdentifyingFootnotes.md for the workflows it feeds.
"""

import argparse
import json
import os
import re
import sys
import warnings

warnings.filterwarnings("ignore", category=FutureWarning)
os.environ.pop("DEBUG", None)

from config import TranslationConfig
from db import DatabaseManager
from logger import Logger


FIELD_COLUMNS = {
    "src": "untranslated_content",
    "en": "translated_content",
}


def parse_chapter_filter(expr):
    """Parse a --chapters expression into a predicate over chapter numbers.

    Returns None when expr is falsy (meaning: no filtering).
    """
    if not expr:
        return None

    tests = []
    for part in str(expr).split(","):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", part)
        if m:
            lo, hi = int(m.group(1)), int(m.group(2))
            tests.append(lambda n, lo=lo, hi=hi: lo <= n <= hi)
            continue
        m = re.fullmatch(r"(>=|<=|>|<)\s*(\d+)", part)
        if m:
            op, val = m.group(1), int(m.group(2))
            ops = {
                ">": lambda n, v: n > v,
                ">=": lambda n, v: n >= v,
                "<": lambda n, v: n < v,
                "<=": lambda n, v: n <= v,
            }
            tests.append(lambda n, op=op, v=val: ops[op](n, v))
            continue
        if part.isdigit():
            val = int(part)
            tests.append(lambda n, v=val: n == v)
            continue
        raise ValueError(f"Unrecognized --chapters term: {part!r}")

    if not tests:
        return None
    return lambda n: n is not None and any(t(n) for t in tests)


def decode_lines(raw):
    """Return a list of text lines from a stored content value.

    Chapter and queue content is normally a JSON-serialized list of lines,
    but older rows may hold a raw list or a newline-joined string.
    """
    if raw is None:
        return []
    if isinstance(raw, list):
        return list(raw)
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return raw.split("\n")
        if isinstance(decoded, list):
            return decoded
        if isinstance(decoded, str):
            return decoded.split("\n")
        return [str(decoded)]
    return [str(raw)]


def collect_chapters(db_manager, book_id, fields, include_queue):
    """Return {chapter_number: {"title":…, "src":[…], "en":[…], "queued":bool}}.

    Translated chapters win over queue rows for the same chapter number —
    a chapter mid-migration can briefly exist in both.
    """
    chapters = {}

    columns = ["chapter_number", "title"]
    for f in fields:
        if f in FIELD_COLUMNS:
            columns.append(FIELD_COLUMNS[f])

    with db_manager._conn(dict_rows=True) as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT {', '.join(columns)} FROM chapters WHERE book_id = ? "
            "ORDER BY chapter_number",
            (book_id,),
        )
        for row in cursor.fetchall():
            entry = {
                "title": row["title"],
                "queued": False,
                "src": [],
                "en": [],
            }
            for f in fields:
                col = FIELD_COLUMNS.get(f)
                if col:
                    entry[f] = decode_lines(row[col])
            chapters[row["chapter_number"]] = entry

        if include_queue and "src" in fields:
            cursor.execute(
                "SELECT chapter_number, title, content FROM queue "
                "WHERE book_id = ? ORDER BY position",
                (book_id,),
            )
            for row in cursor.fetchall():
                num = row["chapter_number"]
                if num in chapters:
                    continue
                chapters[num] = {
                    "title": row["title"],
                    "queued": True,
                    "src": decode_lines(row["content"]),
                    "en": [],
                }

    return chapters


def build_matcher(pattern, fixed, ignore_case):
    """Compile the search pattern (a literal with fixed=True).

    Raises ValueError on an invalid regex.
    """
    if fixed:
        pattern = re.escape(pattern)
    flags = re.IGNORECASE if ignore_case else 0
    try:
        return re.compile(pattern, flags)
    except re.error as exc:
        raise ValueError(f"bad regex {pattern!r}: {exc}") from None


MATCH_LABEL_MAXLEN = 40


def match_labels(matcher, text):
    """Distinct matched substrings in a line, in order of first appearance.

    With an alternation pattern this answers "which term of the set hit
    here?" — the whole point of scanning a dozen terms in one pass.
    """
    labels = []
    for m in matcher.finditer(text):
        hit = m.group(0)
        if not hit:
            continue
        hit = " ".join(hit.split())
        if len(hit) > MATCH_LABEL_MAXLEN:
            hit = hit[:MATCH_LABEL_MAXLEN - 1] + "…"
        if hit not in labels:
            labels.append(hit)
    return labels


def format_labels(labels):
    return f" [{', '.join(labels)}]" if labels else ""


def _sorted_chapter_numbers(chapters):
    return sorted(chapters, key=lambda n: (n is None, n))


def grep_chapters(chapters, matcher, fields, *, chapter_filter=None, titles=False):
    """Every matching line of a collect_chapters() mapping, in reading order.

    `matcher` is a compiled regex (build_matcher); `fields` a list drawn from
    "src"/"en"; `chapter_filter` a parse_chapter_filter() predicate or None;
    `titles` also tests each chapter title (once per field, as the CLI does).

    Returns one dict per hit:
        chapter_number  int (or None)
        queued          True when the chapter is a not-yet-translated queue row
        field           "src" | "en"
        is_title        True for a title hit (line_index is then None)
        line_index      0-based index into chapters[n][field], or None
        text            the whole matching line (or the title)
        labels          distinct matched substrings, first-appearance order
        tag             display tag: "" with one field, " src"/" en" with
                        several, plus " title" for title hits
    """
    hits = []
    multi = len(fields) != 1
    for num in _sorted_chapter_numbers(chapters):
        if chapter_filter and not chapter_filter(num):
            continue
        entry = chapters[num]
        for field in fields:
            lines = entry.get(field) or []
            tag = f" {field}" if multi else ""
            for i, line in enumerate(lines):
                text = str(line)
                labels = match_labels(matcher, text)
                if labels or matcher.search(text):
                    hits.append({
                        "chapter_number": num, "queued": entry["queued"],
                        "field": field, "is_title": False, "line_index": i,
                        "text": text, "labels": labels, "tag": tag,
                    })
            if titles and entry["title"]:
                title = str(entry["title"])
                labels = match_labels(matcher, title)
                if labels or matcher.search(title):
                    hits.append({
                        "chapter_number": num, "queued": entry["queued"],
                        "field": field, "is_title": True, "line_index": None,
                        "text": title, "labels": labels, "tag": f"{tag} title",
                    })
    return hits


def _group_by_chapter(hits):
    groups = []
    for hit in hits:
        if groups and groups[-1][0] == hit["chapter_number"]:
            groups[-1][1].append(hit)
        else:
            groups.append((hit["chapter_number"], [hit]))
    return groups


def format_hits(hits, chapters, *, context=0, count=False, files_only=False,
                match_tag=True):
    """Render grep_chapters() hits exactly as the CLI prints them to stdout.

    files_only (-l): one "chN[ (queued)]" line per matching chapter.
    count (-c):      "chN[ (queued)]: K[ [label ×n, …]]" per chapter.
    otherwise:       every hit line, "chN[ (queued)][tag][ [labels]] [i] line";
                     context=N adds N lines either side ('>' marks the hit)
                     followed by a 60-dash rule. Title hits print the title.
    `chapters` (the collect_chapters mapping) supplies context lines and titles.
    match_tag=False drops the "[labels]" prefix (--no-match-tag).
    Returns the text, newline-terminated, or "" when there are no hits.
    """
    out = []
    for num, group in _group_by_chapter(hits):
        entry = chapters[num]
        label = f"ch{num}" if num is not None else "ch?"
        queued = " (queued)" if entry["queued"] else ""

        if files_only:
            out.append(f"{label}{queued}")
            continue
        if count:
            tally = ""
            if match_tag:
                counts = {}
                for hit in group:
                    for lbl in hit["labels"]:
                        counts[lbl] = counts.get(lbl, 0) + 1
                if counts:
                    tally = " [" + ", ".join(
                        lbl if n == 1 else f"{lbl} ×{n}"
                        for lbl, n in counts.items()
                    ) + "]"
            out.append(f"{label}{queued}: {len(group)}{tally}")
            continue

        for hit in group:
            tag = hit["tag"]
            shown = format_labels(hit["labels"]) if match_tag else ""
            i = hit["line_index"]
            if i is None:
                out.append(f"{label}{queued}{tag}{shown}: {entry['title']}")
                continue
            lines = entry.get(hit["field"]) or []
            if context:
                lo = max(0, i - context)
                hi = min(len(lines), i + context + 1)
                for j in range(lo, hi):
                    marker = ">" if j == i else " "
                    pad = shown if j == i else ""
                    out.append(f"{marker}{label}{queued}{tag}{pad} [{j}] {lines[j]}")
                out.append("-" * 60)
            else:
                out.append(f"{label}{queued}{tag}{shown} [{i}] {lines[i]}")
    return "\n".join(out) + "\n" if out else ""


def main():
    parser = argparse.ArgumentParser(
        description="Print whole matching lines from a book's chapters "
                    "(translated and queued).",
    )
    parser.add_argument("pattern", help="Regex (or literal with -F) to search for.")
    parser.add_argument("-b", "--book-id", type=int, required=True,
                        help="Book ID to search.")
    parser.add_argument("--field", choices=["src", "en", "both"], default="src",
                        help="Search untranslated source, translated English, "
                             "or both (default: src).")
    parser.add_argument("--chapters", default=None,
                        help="Restrict to chapters: N, N-M, >N, <=N, or a comma list.")
    parser.add_argument("-F", "--fixed", action="store_true",
                        help="Treat the pattern as a literal string, not a regex.")
    parser.add_argument("-i", "--ignore-case", action="store_true",
                        help="Case-insensitive matching.")
    parser.add_argument("--context", type=int, default=0, metavar="N",
                        help="Print N lines of context around each match.")
    parser.add_argument("--titles", action="store_true",
                        help="Also search chapter titles.")
    parser.add_argument("--no-queue", action="store_true",
                        help="Skip not-yet-translated queue chapters.")
    parser.add_argument("--count", action="store_true",
                        help="Print per-chapter match counts instead of lines.")
    parser.add_argument("--files-with-matches", "-l", action="store_true",
                        help="Print only the chapter numbers that match.")
    parser.add_argument("--no-match-tag", action="store_true",
                        help="Don't prefix each line with the matched term(s).")
    args = parser.parse_args()

    try:
        chapter_filter = parse_chapter_filter(args.chapters)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        matcher = build_matcher(args.pattern, args.fixed, args.ignore_case)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        print("(pass -F to search for it as a literal string)", file=sys.stderr)
        sys.exit(1)

    fields = ["src", "en"] if args.field == "both" else [args.field]

    config = TranslationConfig()
    logger = Logger(config)
    db_manager = DatabaseManager(config, logger)

    chapters = collect_chapters(
        db_manager, args.book_id, fields, include_queue=not args.no_queue
    )
    if not chapters:
        print(f"No chapters found for book {args.book_id}.", file=sys.stderr)
        sys.exit(1)

    hits = grep_chapters(chapters, matcher, fields,
                         chapter_filter=chapter_filter, titles=args.titles)
    sys.stdout.write(format_hits(
        hits, chapters, context=args.context, count=args.count,
        files_only=args.files_with_matches, match_tag=not args.no_match_tag))

    if not args.files_with_matches:
        total_matches = len(hits)
        matched_chapters = len(_group_by_chapter(hits))
        print(f"\n{total_matches} match(es) in {matched_chapters} chapter(s).",
              file=sys.stderr)


if __name__ == "__main__":
    main()
