#!/usr/bin/env python3
"""Delete lines matching a glob pattern from every queue item of a book,
*ignoring* Unicode lookalike / obfuscation tricks.

This is the confusable-aware sibling of strip_queue_prefix_lines.py. Spammers
hide URLs by swapping ASCII letters for visually-identical Unicode characters,
piling on combining "zalgo" marks, using full-width forms, or splicing in
zero-width spaces. Examples that should all match the pattern "twkan":

    twkan
    t͎͎w͎͎k͎͎a͎͎n͎͎.c͎͎o͎͎m͎͎       (combining marks)
    𑢼WKAN.COM                  (𑢼 = U+118BC WARANG CITI, looks like T)
    Ｔｗｋａｎ.com                  (full-width letters)
    tw<zero-width-space>kan.com

We normalize each line to a canonical "skeleton" (Unicode TR39): NFKD
decomposition, drop combining marks + format/zero-width chars, map every
confusable codepoint to its prototype via the Unicode confusables table, then
casefold. The pattern is skeletonized the same way, so matching is immune to
all of the above.

Matching is a glob (``*``, ``?``, ``[...]``) evaluated as a SUBSTRING search by
default (so "twkan" matches a line containing "twkan.com"). Pass --whole-line to
require the glob to match the entire normalized line.

Defaults to a dry run; pass --apply (or --commit) to write changes.

Usage:
    python strip_queue_confusable_lines.py <book_id> --match "twkan"
    python strip_queue_confusable_lines.py <book_id> --match "twkan" --apply
    python strip_queue_confusable_lines.py <book_id> --match "*.twkan.com" --whole-line
    python strip_queue_confusable_lines.py <book_id> --match "twkan" --show
"""

import functools
import json
import os
import re
import sys
import unicodedata
import urllib.request

# Project root (this script lives in it) — needed for db_backend import.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db_backend import create_backend


CONFUSABLES_URL = "https://www.unicode.org/Public/security/latest/confusables.txt"
CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "confusables_skeleton.json")


# --------------------------------------------------------------------------- #
# Confusable skeleton table
# --------------------------------------------------------------------------- #

def _parse_confusables(txt):
    """Parse Unicode confusables.txt into {source_char: prototype_string}."""
    table = {}
    for line in txt.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(";")]
        if len(parts) < 2:
            continue
        try:
            src = chr(int(parts[0], 16))
            tgt = "".join(chr(int(h, 16)) for h in parts[1].split())
        except ValueError:
            continue
        table[src] = tgt
    return table


def load_confusables(regen=False):
    """Return the confusable-skeleton map, loading (and caching) as needed.

    Prefers the bundled JSON cache; falls back to downloading the authoritative
    table from unicode.org and caching it next to this script.
    """
    if not regen and os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError):
            pass  # fall through to (re)download

    print(f"Fetching Unicode confusables table from {CONFUSABLES_URL} ...")
    txt = urllib.request.urlopen(CONFUSABLES_URL, timeout=30).read().decode("utf-8-sig")
    table = _parse_confusables(txt)
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as fh:
            json.dump(table, fh, ensure_ascii=False)
        print(f"Cached {len(table)} confusable mappings -> {CACHE_FILE}")
    except OSError as exc:
        print(f"Warning: could not write cache ({exc}); continuing in-memory.")
    return table


# Symbol glyphs used as letter lookalikes in scraper spam. These are Unicode
# *symbols*, not letters, so the TR39 confusables table has no entry for them
# (₮₩₭₳₦.₵Ø₥ is a real twkan.com banner seen in book 82). The last two entries
# are TR39 *prototypes* that are themselves non-ASCII: the table maps ᴋ->ĸ and
# ᴍ->ʍ while leaving ASCII 'k'/'m' alone, so without folding the prototype back
# down, a pattern and its obfuscated form skeletonize into different alphabets
# and can never match.
_GLYPH_LOOKALIKES = {
    "₮": "T", "₩": "W", "₭": "K", "₳": "A", "₦": "N", "₵": "C", "₥": "M",
    "₱": "P", "₴": "S", "₲": "G", "₹": "R", "₺": "L", "₽": "P", "₡": "C",
    "₤": "L", "₣": "F", "₸": "T", "₼": "M", "₾": "L", "₿": "B", "₪": "S",
    "€": "E", "£": "L", "¥": "Y", "Ø": "O", "ø": "o",
    "ĸ": "K", "ʍ": "M",
}


@functools.lru_cache(maxsize=None)
def _glyph_fold(ch):
    """Fold one decorated glyph to its plain ASCII letter/digit, or None.

    Covers the families NFKD and the TR39 table both miss, by reading the
    character's Unicode *name*:

        LATIN LETTER SMALL CAPITAL T           (ᴛ)   -> "T"
        NEGATIVE SQUARED LATIN CAPITAL LETTER T (🆃)  -> "T"
        NEGATIVE CIRCLED LATIN CAPITAL LETTER T (🅣)  -> "T"
        DINGBAT NEGATIVE CIRCLED DIGIT SIX      (❻)  -> "6"

        LATIN SMALL LETTER T WITH PALATAL HOOK  (ƚ)  -> "T"

    Only fires when the name names a single LATIN letter — either directly
    after "LETTER" or as the final token. Multi-letter names (LATIN SMALL
    LETTER KRA, ARMENIAN SMALL LETTER EH) are left to the confusables table
    rather than guessed at.
    """
    if ch < "\x80":
        return None
    hit = _GLYPH_LOOKALIKES.get(ch)
    if hit:
        return hit
    try:
        name = unicodedata.name(ch)
    except ValueError:
        return None
    parts = name.split()
    if "LATIN" in parts and "LETTER" in parts:
        # "…LETTER T WITH PALATAL HOOK" -> the token right after LETTER;
        # "LATIN LETTER SMALL CAPITAL T" -> the final token.
        after = parts.index("LETTER") + 1
        if after < len(parts) and len(parts[after]) == 1 and parts[after].isalpha():
            return parts[after]
        if len(parts[-1]) == 1 and parts[-1].isalpha():
            return parts[-1]
    digit = unicodedata.digit(ch, None)
    if digit is not None and "DIGIT" in parts:
        return str(digit)
    return None


def _is_mark(ch):
    """True for any combining mark — Mn, Mc *and* Me.

    ``unicodedata.combining()`` reports 0 for enclosing marks such as U+0489
    COMBINING CYRILLIC MILLION SIGN, which is exactly what the ҉҉t҉҉w҉҉k҉a҉҉n
    zalgo banners are built from, so testing the category is what actually
    strips them.
    """
    return unicodedata.category(ch)[0] == "M"


def make_skeletonizer(table):
    """Return a skeleton(s) function that strips Unicode obfuscation.

    We fold decorated glyphs to ASCII, map confusables, then casefold (the TR39
    order — it keeps the case-dependent shape of letters like Warang Citi
    U+118BC, which looks like 'T' upper but 'y' lower). One wrinkle: the table
    is case-asymmetric for a few ASCII multi-char confusables (lower 'm' -> 'rn'
    but upper 'M' -> 'M'), which would make ".COM" and ".com" skeletonize
    differently. We case-complete those entries so both cases share a mapping;
    setdefault never clobbers an existing, intentional case-specific entry.

    Fold-and-map is iterated to a *fixed point* (bounded), because several TR39
    prototypes are themselves non-ASCII and need a further hop: ᴍ -> "ʍ" -> "M"
    -> "rn", ᴋ -> "ĸ" -> "k", ₮ -> "T"+U+20EB -> "t". A single pass leaves the
    pattern and its obfuscated form in different alphabets, so they never meet.
    """
    table = dict(table)
    for key, val in list(table.items()):
        if len(key) == 1 and key.isalpha():
            for variant in (key.upper(), key.lower()):
                # Only complete genuine case pairs. Some characters uppercase
                # onto an unrelated ASCII letter — U+017F LATIN SMALL LETTER
                # LONG S ('ſ', legitimately a confusable of 'f') uppercases to
                # 'S', which would otherwise register S -> "f" and skeletonize
                # every capital S in every book as an f ("Springs" -> "fprlngs").
                if variant.lower() != key.lower():
                    continue
                table.setdefault(variant, val)

    def _pass(text):
        out = []
        for ch in unicodedata.normalize("NFKD", text):
            if _is_mark(ch):                      # zalgo / accents / enclosing
                continue
            if unicodedata.category(ch) == "Cf":  # zero-width / formatting
                continue
            # The TR39 table is authoritative where it has an opinion; the
            # name-based fold only fills the gaps it leaves.
            out.append(table[ch] if ch in table else (_glyph_fold(ch) or ch))
        return "".join(out)

    def skeleton(s):
        text = s
        for _ in range(4):                        # bounded fixed point
            nxt = _pass(text)
            if nxt == text:
                break
            text = nxt
        return text.casefold()
    return skeleton


# --------------------------------------------------------------------------- #
# Glob matching
# --------------------------------------------------------------------------- #

def glob_to_regex(pattern):
    """Translate a shell glob to a regex *fragment* (no anchors).

    Supports ``*`` (any run), ``?`` (any single char), and ``[...]`` classes.
    Everything else is matched literally. Anchoring is decided by the caller.
    """
    i, n = 0, len(pattern)
    res = []
    while i < n:
        c = pattern[i]
        i += 1
        if c == "*":
            res.append(".*")
        elif c == "?":
            res.append(".")
        elif c == "[":
            j = i
            if j < n and pattern[j] in ("!", "^"):
                j += 1
            if j < n and pattern[j] == "]":
                j += 1
            while j < n and pattern[j] != "]":
                j += 1
            if j >= n:
                res.append(r"\[")  # unterminated class -> literal '['
            else:
                stuff = pattern[i:j].replace("\\", "\\\\")
                i = j + 1
                if stuff.startswith("!"):
                    stuff = "^" + stuff[1:]
                res.append("[" + stuff + "]")
        else:
            res.append(re.escape(c))
    return "".join(res)


def build_matcher(pattern, skeleton, whole_line):
    """Compile a predicate that tests a raw line against the skeletonized glob."""
    skel_pattern = skeleton(pattern)
    frag = glob_to_regex(skel_pattern)
    regex = re.compile(("(?s:" + frag + r")\Z") if whole_line else ("(?s:" + frag + ")"))
    search = regex.fullmatch if whole_line else regex.search

    def matches(line):
        return bool(search(skeleton(line)))

    return matches, skel_pattern


# --------------------------------------------------------------------------- #
# Queue content filtering (shape-preserving, mirrors strip_queue_prefix_lines)
# --------------------------------------------------------------------------- #

def filter_lines(content, matches):
    """Return (filtered_content, removed_lines).

    Content can be a JSON-serialized list, a raw list, or a string. We always
    return the same shape we received so the queue row stays valid.
    removed_lines is a list of the dropped line strings (for reporting).
    """
    parsed = None
    was_json_list = False
    if isinstance(content, str):
        try:
            decoded = json.loads(content)
            if isinstance(decoded, list):
                parsed = decoded
                was_json_list = True
        except (json.JSONDecodeError, TypeError):
            pass

    if parsed is not None:
        removed = [ln for ln in parsed if isinstance(ln, str) and matches(ln)]
        kept = [ln for ln in parsed if not (isinstance(ln, str) and matches(ln))]
        if was_json_list:
            return json.dumps(kept, ensure_ascii=False), removed
        return kept, removed

    if isinstance(content, list):
        removed = [ln for ln in content if isinstance(ln, str) and matches(ln)]
        kept = [ln for ln in content if not (isinstance(ln, str) and matches(ln))]
        return kept, removed

    if isinstance(content, str):
        lines = content.split("\n")
        removed = [ln for ln in lines if matches(ln)]
        kept = [ln for ln in lines if not matches(ln)]
        return "\n".join(kept), removed

    return content, []


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def parse_args(argv):
    opts = {
        "apply": False,
        "match": None,
        "whole_line": False,
        "show": False,
        "regen_cache": False,
        "positional": [],
    }

    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--apply", "--commit"):
            opts["apply"] = True
        elif a in ("--match", "-m"):
            if i + 1 >= len(argv):
                print("Error: --match requires a value")
                sys.exit(1)
            opts["match"] = argv[i + 1]
            i += 1
        elif a.startswith("--match="):
            opts["match"] = a[len("--match="):]
        elif a == "--whole-line":
            opts["whole_line"] = True
        elif a == "--show":
            opts["show"] = True
        elif a == "--regen-cache":
            opts["regen_cache"] = True
        elif a.startswith("--"):
            print(f"Error: unknown option {a!r}")
            sys.exit(1)
        else:
            opts["positional"].append(a)
        i += 1

    return opts


def usage():
    print("Usage: python strip_queue_confusable_lines.py <book_id> --match <glob> "
          "[--whole-line] [--show] [--apply]")


def main():
    opts = parse_args(sys.argv[1:])
    dry_run = not opts["apply"]

    positional = opts["positional"]
    # Allow the pattern as a second positional for convenience.
    if opts["match"] is None and len(positional) == 2:
        opts["match"] = positional[1]
        positional = positional[:1]

    if len(positional) != 1 or not opts["match"]:
        usage()
        sys.exit(1)

    try:
        book_id = int(positional[0])
    except ValueError:
        print(f"Error: book_id must be an integer (got {positional[0]!r})")
        sys.exit(1)

    table = load_confusables(regen=opts["regen_cache"])
    skeleton = make_skeletonizer(table)
    matches, skel_pattern = build_matcher(opts["match"], skeleton, opts["whole_line"])

    backend = create_backend()
    conn = backend.get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT id, chapter_number, title, content FROM queue "
        "WHERE book_id = ? ORDER BY position ASC",
        (book_id,),
    )
    rows = cursor.fetchall()

    if not rows:
        print(f"No queue items found for book {book_id}.")
        conn.close()
        return

    mode = "whole-line glob" if opts["whole_line"] else "substring glob"
    print(f"Scanning {len(rows)} queue item(s) for book {book_id}"
          f"{'  [DRY RUN]' if dry_run else ''}...")
    print(f"Match pattern : {opts['match']!r}  ({mode})")
    print(f"Skeletonized  : {skel_pattern!r}\n")

    total_removed = 0
    items_modified = 0

    for queue_id, ch_num, title, content in rows:
        new_content, removed = filter_lines(content, matches)
        if not removed:
            continue

        items_modified += 1
        total_removed += len(removed)
        ch_label = f"ch {ch_num}" if ch_num is not None else "no chapter#"
        print(f"  Queue {queue_id} ({ch_label}, {title!r}): removed {len(removed)} line(s)")
        if opts["show"]:
            for ln in removed:
                print(f"      - {ln!r}")

        if not dry_run:
            cursor.execute(
                "UPDATE queue SET content = ? WHERE id = ?",
                (new_content, queue_id),
            )

    if not dry_run:
        conn.commit()
    conn.close()

    print(f"\nDone.")
    print(f"  Queue items scanned:  {len(rows)}")
    print(f"  Queue items modified: {items_modified}")
    print(f"  Lines removed:        {total_removed}")
    if dry_run:
        print("  (dry run — no changes written; pass --apply to commit)")


if __name__ == "__main__":
    main()
