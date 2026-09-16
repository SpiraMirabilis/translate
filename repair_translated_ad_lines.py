#!/usr/bin/env python3
"""Repair site-ad spam that leaked into TRANSLATED chapter text.

``bulk_strip_ads.py`` fixes the *source* side: it re-runs the twkan module over
stored Chinese so the spam never reaches the model again. But chapters that were
already translated before a pattern was added kept the ad in their English —
``--scan-translated`` only reports those. This script is the English-side repair.

Two dispositions, because the two ad families sit differently in the prose:

* **Site-plug lines** (TWKAN / Taiwan Novel Network / "GOOGLE search TWKAN") are
  whole-line interpolations by the mirror. The entire line is deleted.
* **``69shux.com``** is usually appended to a *real* sentence, so only the domain
  token is cut and the sentence survives. When stripping the domain would leave
  nothing but the ad's own wrapper (``(Note: )``, an empty line), the line is
  deleted instead — ``_residue_is_scaffolding`` decides that, not a kept list.

Plugs are sometimes dressed up as a one-row markdown table, so a table run is
removed as a unit rather than leaving an orphan ``| --- |`` delimiter behind;
content-free table shells left over from an already-stripped plug go the same
way (see :func:`_is_empty_table`).

Both families are written in homoglyph/obfuscated alphabets that rotate per
chapter (🆃🆆🅺🅰🅽.🅲🅾🅼, ᴛᴡᴋᴀɴ.ᴄᴏᴍ, էաҟąղ.çօʍ, t҉w҉k҉a҉n.҉c҉o҉m, …), so matching is
done on an NFKD-folded copy with combining marks, zero-width joiners and the
usual lookalike letters mapped back to ASCII (:func:`_fold`). Line *text* is
never rewritten from the folded form — only spans located in it are cut from the
original, so untouched characters stay byte-identical.

Deliberately narrow, like ``bulk_strip_ads.py``: it does NOT go through
``save_chapter``, so no modules re-run and footnotes are not re-rendered.
Footnotes anchor by (text, occurrence) rather than line index, so deleting a
whole line does not disturb them. ``books.modified_date`` IS bumped for books
that changed, because translated text is the rendered artifact — without the
bump the cached EPUB/AZW3 would keep serving the ad.

Usage:
    python3 repair_translated_ad_lines.py --dry-run              # every book
    python3 repair_translated_ad_lines.py --apply
    python3 repair_translated_ad_lines.py --book-id 69 --dry-run
"""

import argparse
import datetime
import json
import os
import re
import sys
import unicodedata
import warnings

warnings.filterwarnings("ignore", category=FutureWarning)
os.environ.pop("DEBUG", None)

from config import TranslationConfig
from db import DatabaseManager
from logger import Logger

# Characters that carry no text of their own and only exist to break substring
# matching: zero-width spaces/joiners, the combining-glyph "glitch" overlays,
# and the variation selectors that trail enclosed-alphanumeric emoji.
_INVISIBLE = re.compile(
    r"[​-‏⁠﻿̀-ͯ︀-️\U000e0100-\U000e01ef]"
)

# Lookalikes NFKD does not normalise. Mapped only for *matching*.
_LOOKALIKE = str.maketrans({
    "ƚ": "t", "ɯ": "w", "ƙ": "k", "α": "a", "ɳ": "n", "ƈ": "c", "σ": "o", "ɱ": "m",
    "է": "t", "ա": "w", "ҟ": "k", "ą": "a", "ղ": "n", "ç": "c", "օ": "o", "ʍ": "m",
    "₮": "t", "₩": "w", "₭": "k", "₳": "a", "₦": "n", "₵": "c", "Ø": "o", "₥": "m",
    "к": "k", "т": "t", "ѕ": "s", "һ": "h", "ц": "u", "х": "x", "ᴋ": "k",
})


def _fold(text):
    """Casefolded, homoglyph-flattened copy of ``text``, same length policy aside.

    Used only to *find* ad spans; the cut is applied to the original string.
    Index alignment with the original is not assumed anywhere — spans are
    re-located by scanning the original with the same folding applied per slice
    (see :func:`_find_span`).
    """
    if text.isascii():
        # Exactly equivalent, and the common case by far over a 386MB corpus:
        # NFKD is the identity on ASCII, every _INVISIBLE range is >= U+0300,
        # and every _LOOKALIKE key is non-ASCII, so only the casefold can bite.
        return text.casefold()
    folded = unicodedata.normalize("NFKD", text)
    folded = _INVISIBLE.sub("", folded)
    return folded.translate(_LOOKALIKE).casefold()


# A whole line is a site plug when it mentions the site by any of its names.
# "taiwan novel" alone is enough: the mirror's plug always names itself, and no
# story prose in these books talks about a Taiwanese novel website.
_PLUG_MARKERS = ("twkan", "taiwan novel")

# The 69shux domain, in folded space. NFKD turns 🅂🄷🅄🅇 into "SHUX" and 𝟨𝟫 into
# "69"; the optional separators absorb any invisible leftovers between glyphs.
_SHUX = re.compile(r"(?:69)?\s*shux\s*\.\s*(?:com|co)\b")

# Simple (GFM pipe) table rows, as stored by the markdown pipeline. The mirror
# sometimes dresses its plug up as a one-row table, which has to be removed as a
# unit — header row *and* the `| --- |` delimiter that follows it.
# DOTALL matters: a cell may hold a hard break, so a perfectly ordinary row can
# contain newlines. Without it such a row reads as "not a table row", which
# strands the delimiter beside it and makes a real table look like an empty one.
_PIPE_ROW = re.compile(r"\s*\|.*\|\s*\Z", re.DOTALL)
# At least one dash is required, per GFM — without it `|  |` would read as its
# own delimiter and a lone blank row would look like a complete table.
_DELIM_ROW = re.compile(r"^\s*\|[\s:|-]*-[\s:|-]*\|\s*$")


def _lines(content):
    """(lines, was_json) from a stored translated_content blob."""
    if isinstance(content, list):
        return list(content), True
    if isinstance(content, str):
        try:
            data = json.loads(content)
            if isinstance(data, list):
                return data, True
        except (json.JSONDecodeError, TypeError):
            pass
        return content.split("\n"), False
    return [], False


def _dump(lines, was_json):
    return json.dumps(lines, ensure_ascii=False) if was_json else "\n".join(lines)


def _is_plug_line(line):
    """True when the whole line is one of the mirror's site plugs."""
    return any(m in _fold(line) for m in _PLUG_MARKERS)


def _find_span(line):
    """Character span of the 69shux domain in ``line``, or None.

    The folded string can differ in length from the original (NFKD expands the
    enclosed-letter glyphs, invisibles are dropped), so the span is found by
    folding progressively longer prefixes of the original until the fold reaches
    the match boundaries. Slower than index arithmetic, irrelevant at this scale,
    and correct for every obfuscation variant rather than a chosen few.
    """
    folded = _fold(line)
    m = _SHUX.search(folded)
    if not m:
        return None

    # Map folded offsets back to original offsets: prefix lengths are monotonic
    # in the fold, so the first original index whose fold is long enough wins.
    start = end = None
    for i in range(len(line) + 1):
        flen = len(_fold(line[:i]))
        if start is None and flen > m.start():
            start = i - 1
        if flen >= m.end():
            end = i
            break
    if start is None or end is None:
        return None
    return start, end


def _residue_is_scaffolding(line):
    """True when what survives the domain cut is only the ad's own wrapper.

    A bare ``69shux.com`` leaves nothing; ``(Note: 69shux.com)`` leaves the
    label ``Note:`` introducing something that is no longer there. Neither is
    prose, so the line is dropped rather than left as debris. A real sentence
    ("Ten-Foot Crimson.") ends in normal punctuation and survives.
    """
    core = line.strip().strip("()[]{}（）【】「」|\"'“”‘’ 　")
    return not core or core.endswith((":", "：", "—", "-", "→"))


def _strip_shux(line):
    """Cut the 69shux domain (and the whitespace it hangs off) from ``line``."""
    span = _find_span(line)
    if not span:
        return line
    start, end = span
    # Absorb the separator that attached the domain to the sentence, on either
    # side, so "Ghosts. 69shux.com" does not leave a trailing space.
    while start > 0 and line[start - 1] in "  \t":
        start -= 1
    while end < len(line) and line[end] in "  \t":
        end += 1
    out = line[:start] + line[end:]
    # A domain lifted from inside a quoted sentence leaves the closing quote
    # jammed against the period, which is what we want; only collapse a double
    # space created in the middle of running text.
    return re.sub(r"  +", " ", out)


def _is_pipe_row(line):
    return isinstance(line, str) and bool(_PIPE_ROW.match(line))


def _table_run(lines, idx):
    """Span of the GFM pipe-table ``lines[idx]`` belongs to, or None.

    Some mirrors wrap the plug in a one-row table (the plug line followed by a
    ``| --- |`` delimiter). Deleting only the text row would leave the orphan
    delimiter rendering as a stray table, so the whole run has to go together.
    A run is the maximal block of consecutive pipe rows around ``idx``, and it
    only counts as a table when a delimiter row is present in it.
    """
    if not _is_pipe_row(lines[idx]):
        return None
    start = idx
    while start > 0 and _is_pipe_row(lines[start - 1]):
        start -= 1
    end = idx + 1
    while end < len(lines) and _is_pipe_row(lines[end]):
        end += 1
    if not any(_DELIM_ROW.match(l) for l in lines[start:end]):
        return None
    return start, end


def _absorb_blank(new_lines, lines, end_idx, drop):
    """Drop the blank line trailing a removed block when one also precedes it.

    Chapters store prose as alternating ``paragraph, ""`` lines, so removing a
    whole paragraph would otherwise leave two blanks adjacent. Only the removal
    site is touched — pre-existing blank runs elsewhere are left alone.
    """
    if new_lines and not (isinstance(new_lines[-1], str) and not new_lines[-1].strip()):
        # Something real precedes the hole; its own separator has to stay.
        return
    if end_idx < len(lines) and isinstance(lines[end_idx], str) \
            and not lines[end_idx].strip():
        drop.add(end_idx)


def _row_is_blank(row):
    """True when a pipe row's cells are all empty (``|  |``, ``|  |  |``)."""
    return not any(cell.strip() for cell in row.strip().strip("|").split("|"))


def _is_empty_table(lines, start, end):
    """True when a table run carries no content at all.

    The same mirror plug sometimes survives as an empty shell — ``|  |`` over a
    ``| --- |`` — after its text was stripped elsewhere. It renders as a blank
    table box in the reader, so it is removed like any other ad remnant. A run
    counts as empty only when *every* row is either the alignment delimiter or
    a row of empty cells, so a real table with one blank cell is untouched.

    At least one blank *row* is required too: a bare delimiter with no row at
    all is a different defect (a stray line, or a row this parser failed to
    recognise) and is left for a human rather than silently swallowed.
    """
    rows = lines[start:end]
    if not any(_row_is_blank(l) and not _DELIM_ROW.match(l) for l in rows):
        return False
    return all(_DELIM_ROW.match(l) or _row_is_blank(l) for l in rows)


def plan_chapter(lines):
    """Return (new_lines, actions) for one chapter's translated lines.

    ``actions`` is a list of ``(index, kind, before, after)`` where kind is
    ``"delete"``, ``"edit"`` or ``"skip"`` (a table the sweep refuses to touch).
    """
    new_lines = []
    actions = []
    drop = set()          # indices removed as part of an ad table run
    idx = 0
    for idx, line in enumerate(lines):
        if idx in drop:
            continue
        if not isinstance(line, str):
            new_lines.append(line)
            continue

        run = _table_run(lines, idx) if _is_pipe_row(line) else None
        if run and _is_empty_table(lines, *run):
            start, end = run
            actions.append((idx, "delete", line, None))
            drop.update(range(start, end))
            _absorb_blank(new_lines, lines, end, drop)
            continue

        is_ad = _is_plug_line(line)
        span = None if is_ad else _find_span(line)
        if not is_ad and not span:
            new_lines.append(line)
            continue

        if run:
            start, end = run
            rows = [l for l in lines[start:end] if not _DELIM_ROW.match(l)]
            if all(_is_plug_line(r) or _find_span(r) for r in rows):
                actions.append((idx, "delete", line, None))
                drop.update(range(start, end))
                _absorb_blank(new_lines, lines, end, drop)
                continue
            # A table that mixes an ad row with real content: a partial delete
            # would corrupt the table's shape, so leave it for a human.
            actions.append((idx, "skip", line, None))
            new_lines.append(line)
            continue

        if is_ad:
            actions.append((idx, "delete", line, None))
            _absorb_blank(new_lines, lines, idx + 1, drop)
            continue

        stripped = _strip_shux(line)
        if _residue_is_scaffolding(stripped):
            actions.append((idx, "delete", line, None))
            _absorb_blank(new_lines, lines, idx + 1, drop)
            continue
        actions.append((idx, "edit", line, stripped))
        new_lines.append(stripped)

    return new_lines, actions


def repair_book(db, book, dry_run, quiet):
    """Sweep one book's translated chapters.

    Returns ``(deleted, edited, skipped, chapters)``.
    """
    book_id = book["id"]
    deleted = edited = skipped = touched = 0
    header_shown = False

    with db._conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, chapter_number, translated_content FROM chapters "
            "WHERE book_id = ? ORDER BY chapter_number ASC",
            (book_id,),
        )
        rows = cursor.fetchall()

        for row_id, ch_num, content in rows:
            if not content:
                continue
            lines, was_json = _lines(content)
            if not lines:
                continue
            new_lines, actions = plan_chapter(lines)
            if not actions:
                continue

            if not header_shown and not quiet:
                print(f"\n  Book {book_id} — {book.get('title', '?')}")
                header_shown = True
            for idx, kind, before, after in actions:
                if kind == "delete":
                    deleted += 1
                    if not quiet:
                        print(f"    ch{ch_num} line {idx}  DELETE  {before[:100]}")
                elif kind == "edit":
                    edited += 1
                    if not quiet:
                        print(f"    ch{ch_num} line {idx}  EDIT    {before[:100]}")
                        print(f"    {'':>{len(str(ch_num)) + 12}}      ->      {after[:100]}")
                else:
                    skipped += 1
                    print(f"    ch{ch_num} line {idx}  SKIP    ad row inside a table "
                          f"with real content — fix by hand: {before[:80]}")

            if not any(k in ("delete", "edit") for _, k, _, _ in actions):
                # Nothing but refusals: the chapter is untouched, so don't count
                # it as repaired and don't rewrite the row.
                continue
            touched += 1

            if not dry_run:
                cursor.execute(
                    "UPDATE chapters SET translated_content = ? WHERE id = ?",
                    (_dump(new_lines, was_json), row_id),
                )

        if touched and not dry_run:
            # Translated text is the rendered artifact: without this bump the
            # cached EPUB/AZW3 (.ver stamp) keeps serving the ad.
            cursor.execute(
                "UPDATE books SET modified_date = ? WHERE id = ?",
                (datetime.datetime.now().isoformat(), book_id),
            )

    return deleted, edited, skipped, touched


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--book-id", type=int, action="append",
                        help="Limit to this book (repeatable).")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true",
                      help="Preview without writing (the default).")
    mode.add_argument("--apply", action="store_true", help="Commit the changes.")
    parser.add_argument("--quiet", action="store_true",
                        help="Totals only, no per-line listing.")
    args = parser.parse_args()

    dry_run = not args.apply

    config = TranslationConfig()
    logger = Logger(config)
    db = DatabaseManager(config, logger, strict_writes=True)

    books = db.list_books()
    if args.book_id:
        wanted = set(args.book_id)
        books = [b for b in books if b["id"] in wanted]
        missing = wanted - {b["id"] for b in books}
        if missing:
            print(f"No book with id {sorted(missing)}.")
            return 1

    print(f"Books to sweep: {len(books)}   Mode: {'DRY RUN' if dry_run else 'APPLY'}")
    print("=" * 72)

    tot_del = tot_edit = tot_skip = tot_ch = 0
    for book in books:
        d, e, s_, t = repair_book(db, book, dry_run, args.quiet)
        tot_del += d
        tot_edit += e
        tot_skip += s_
        tot_ch += t

    print("\n" + "=" * 72)
    print(f"Chapters affected   : {tot_ch}")
    print(f"Lines deleted       : {tot_del}")
    print(f"Lines edited        : {tot_edit}")
    if tot_skip:
        print(f"Lines skipped       : {tot_skip} (ad row inside a real table — fix by hand)")
    if dry_run:
        print("\nDRY RUN — nothing was written. Re-run with --apply to commit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
