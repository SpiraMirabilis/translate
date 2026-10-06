"""
Post-translation East Asian unit → metric conversion.

Appends metric equivalents in parentheses, e.g. "1000 zhang (3.3 km)".
Uses regex to find matches, with optional false positive filtering: the Jev
classifier when it is configured and ``jev_unit_filter`` is on, and/or an LLM
cleaning model. When both are available Jev decides the matches it is sure of
and the LLM gets only the rest (and everything, if the Jev call fails).

The unit table is keyed by the book's source language (``units.json``): the
same romanisation can mean different things (Japanese ri = 3.93 km, Korean
ri = 393 m), and a language's collision-prone names never enter another
language's pattern. A language with no table is left untouched.
"""

import json
import logging
import os
import re
import threading
from typing import Dict, List, NamedTuple, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# ── Unit table ──────────────────────────────────────────────────────
# units.json: {"common": {...}, "zh": {...}, "ja": {...}, "ko": {...}}. Each
# entry has value, unit, type, and optionally action/numeral/strict. "common"
# holds names no language can misread (tsubo, pyeong) and is merged into every
# language table, the language's own entry winning a clash. A legacy flat file
# (entries at the top level) is read as the zh table.

UNITS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "units.json")

LANGUAGE_NAMES = {"zh": "Chinese", "ja": "Japanese", "ko": "Korean"}
_LANGUAGE_ALIASES = {"cn": "zh", "jp": "ja", "kr": "ko"}
DEFAULT_LANGUAGE = "zh"


class Unit(NamedTuple):
    value: float
    unit: str
    type: str
    action: str = "annotate"   # annotate: "3 jin (1.5 kg)"; replace: "two shichen" -> "four hours"
    numeral: str = "arabic"
    # A strict unit is also an ordinary English word ("a ping sounded", "two
    # ping-pong balls", "next of kin"). It converts only after a real count —
    # never a bare article, a vague quantifier or a fraction alone — takes no
    # plural "s", and is skipped when a hyphen follows.
    strict: bool = False


def _parse_entry(lang: str, name: str, entry) -> Unit:
    where = f"units.json {lang}.{name}"
    if not isinstance(entry, dict):
        raise ValueError(f"{where}: expected an object")
    try:
        value = float(entry["value"])
        unit, utype = entry["unit"], entry["type"]
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError(f"{where}: needs numeric value, unit and type ({e})")
    if value <= 0 or not isinstance(unit, str) or not isinstance(utype, str):
        raise ValueError(f"{where}: value must be > 0, unit and type strings")
    action = entry.get("action", "annotate")
    numeral = entry.get("numeral", "arabic")
    strict = entry.get("strict", False)
    if action not in ("annotate", "replace"):
        raise ValueError(f"{where}: action must be annotate or replace")
    if numeral not in ("arabic", "english"):
        raise ValueError(f"{where}: numeral must be arabic or english")
    if not isinstance(strict, bool):
        raise ValueError(f"{where}: strict must be true or false")
    return Unit(value, unit, utype, action, numeral, strict)


def parse_units(raw) -> Dict[str, Dict[str, Unit]]:
    """``{lang: {name: Unit}}`` from units.json's parsed content.

    Raises ValueError on a malformed table (the Settings editor validates with
    this before saving)."""
    if not isinstance(raw, dict):
        raise ValueError("units.json: expected an object")
    if raw and all(isinstance(v, dict) and "value" in v for v in raw.values()):
        raw = {DEFAULT_LANGUAGE: raw}   # legacy flat file
    for lang, entries in raw.items():
        if not isinstance(entries, dict):
            raise ValueError(f"units.json {lang}: expected an object of units")
    common = raw.get("common", {})
    return {
        lang: {name.lower(): _parse_entry(lang, name, e)
               for name, e in {**common, **entries}.items()}
        for lang, entries in raw.items() if lang != "common"
    }


def normalize_language(source_language: Optional[str]) -> str:
    """'zh-TW' -> 'zh', 'JP' -> 'ja'; empty/None -> the zh default."""
    if not source_language:
        return DEFAULT_LANGUAGE
    base = re.split(r"[-_]", str(source_language).strip().lower())[0]
    return _LANGUAGE_ALIASES.get(base, base) or DEFAULT_LANGUAGE


# ── Smart scaling ───────────────────────────────────────────────────
# Each entry: (threshold, target_unit, factor). For SCALE_UP, target = value / factor
# when value >= threshold. For SCALE_DOWN, target = value * factor when value < threshold.
SCALE_UP = {
    "m":      [(1000.0, "km",   1000.0)],
    "kg":     [(1000.0, "t",    1000.0)],
    "m²":     [(10000.0, "ha",  10000.0)],
    "L":      [(1000.0, "m³",   1000.0)],
    "minute": [(60.0,   "hour", 60.0)],
}

SCALE_DOWN = {
    "m":    [(1.0, "cm", 100.0)],
    "kg":   [(1.0, "g",  1000.0)],
    "L":    [(1.0, "mL", 1000.0)],
    "hour": [(1.0, "minute", 60.0)],
}


def _round_for_readability(value: float, unit: str) -> float:
    """Snap to a granularity that matches casual narrative phrasing.
    Leaves small minute values alone so we don't smear precision into nothing."""
    if unit == "minute":
        if value < 5:
            return value
        if value < 30:
            return round(value / 5) * 5
        return round(value / 15) * 15
    if unit == "hour":
        return round(value * 2) / 2
    if unit == "km" and value >= 5:
        return round(value * 2) / 2
    return value


def _scale(value: float, base_unit: str, *, approximate: bool = False) -> tuple:
    """Scale value to the most readable unit.

    Returns (value, unit, was_rounded). was_rounded is True iff approximate
    rounding actually moved the value."""
    def _finish(v, u):
        if not approximate:
            return v, u, False
        r = _round_for_readability(v, u)
        return r, u, r != v

    for threshold, small_unit, factor in SCALE_DOWN.get(base_unit, []):
        if value < threshold:
            return _finish(value * factor, small_unit)

    for threshold, big_unit, factor in SCALE_UP.get(base_unit, []):
        if value >= threshold:
            return _finish(value / factor, big_unit)

    return _finish(value, base_unit)


def _format_number(value: float) -> str:
    """Format number: 1-2 decimals, no trailing zeros, commas for thousands."""
    if value == int(value):
        return f"{int(value):,}"

    # Use up to 2 decimal places
    if value >= 100:
        formatted = f"{value:,.1f}"
    else:
        formatted = f"{value:,.2f}"

    # Strip trailing zeros after decimal point
    if '.' in formatted:
        formatted = formatted.rstrip('0').rstrip('.')

    return formatted


# ── Number-to-words (for "english" numeral mode) ─────────────────

_WORD_ONES = [
    "", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
    "seventeen", "eighteen", "nineteen",
]
_WORD_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def _int_to_words(n: int) -> str:
    """Convert a non-negative integer to English words (e.g. 24 -> 'twenty-four')."""
    if n == 0:
        return "zero"
    if n < 0:
        return "negative " + _int_to_words(-n)

    parts = []
    if n >= 1_000_000:
        parts.append(_int_to_words(n // 1_000_000) + " million")
        n %= 1_000_000
    if n >= 1000:
        parts.append(_int_to_words(n // 1000) + " thousand")
        n %= 1000
    if n >= 100:
        parts.append(_WORD_ONES[n // 100] + " hundred")
        n %= 100
    if n >= 20:
        tens_word = _WORD_TENS[n // 10]
        ones_word = _WORD_ONES[n % 10]
        parts.append(f"{tens_word}-{ones_word}" if ones_word else tens_word)
    elif n > 0:
        parts.append(_WORD_ONES[n])

    return " ".join(parts)


def _number_to_words(value: float) -> str:
    """Convert a numeric value to English words.

    Handles integers (24 -> 'twenty-four') and simple halves (1.5 -> 'one and a half').
    Falls back to formatted arabic numeral for complex decimals.
    """
    if value == int(value):
        return _int_to_words(int(value))

    whole = int(value)
    frac = round(value - whole, 4)

    # Standalone form (whole part is zero) uses idiomatic English
    standalone = {0.25: "a quarter", 0.5: "half", 0.75: "three-quarters"}
    # Suffix form (after "N and ...") uses article forms
    suffix = {0.25: "a quarter", 0.5: "a half", 0.75: "three-quarters"}

    if frac in suffix:
        if whole == 0:
            return standalone[frac]
        return _int_to_words(whole) + " and " + suffix[frac]

    # For other decimals, fall back to arabic — words like "three point three three" are awkward
    return _format_number(value)


# ── Word-to-number parser ──────────────────────────────────────────

_ONES = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "half": 0.5,
}

_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}

_MULTIPLIERS = {
    "hundred": 100, "thousand": 1000, "million": 1_000_000,
}

# Vague quantifiers — skip these
_VAGUE = {
    "several", "few", "many", "some", "numerous", "dozens", "hundreds",
    "thousands", "countless", "myriad", "various", "multiple",
}


def _word_to_number(text: str) -> Optional[float]:
    """Parse English word numbers like 'three hundred' or 'ten thousand' to int.
    Returns None for vague quantifiers."""
    text = text.strip().lower().replace(",", "")

    # Check for plain numeric
    try:
        return float(text.replace(",", ""))
    except ValueError:
        pass

    # Normalize hyphens to spaces
    words = text.replace("-", " ").split()

    if not words:
        return None

    # Check for vague quantifiers
    for w in words:
        if w in _VAGUE:
            return None
    # "a few", "a couple" etc
    if len(words) >= 2 and words[0] == "a" and words[1] in ("few", "couple"):
        return None

    # "a" / "an" as 1
    if words == ["a"] or words == ["an"]:
        return 1.0

    # Filter out "and", "a", "an" as connectors
    words = [w for w in words if w not in ("and", "a", "an", "of")]

    if not words:
        return None

    current = 0
    result = 0
    prev_kind = None  # 'ones' | 'tens' | 'mult'

    for word in words:
        if word in _ONES:
            # Adjacent ones-words ("two three") are a range/enumeration, not a
            # compound number — summing them silently misstates the magnitude
            # ("two-three shichen" is 2–3, never 5). Sole exception: a trailing
            # "half" ("one and a half" = 1.5; the "and" was filtered above).
            if prev_kind == "ones" and word != "half":
                return None
            current += _ONES[word]
            prev_kind = "ones"
        elif word in _TENS:
            # "five twenty" / "twenty thirty" are not compound numbers either.
            if prev_kind in ("ones", "tens"):
                return None
            current += _TENS[word]
            prev_kind = "tens"
        elif word in _MULTIPLIERS:
            if current == 0:
                current = 1
            mult = _MULTIPLIERS[word]
            if mult >= 1000:
                # "two thousand three hundred" → accumulate
                result += current * mult
                current = 0
            else:
                current *= mult
            prev_kind = "mult"
        else:
            return None  # Unknown word

    return float(result + current) if (result + current) > 0 else None


# ── Main regex and conversion ──────────────────────────────────────

# Build unit alternation — escape special regex chars in unit names and
# convert spaces to [\s\-] so "double hour" matches "double-hour" too
def _escape_unit_name(name: str) -> str:
    """Escape a unit name for use in regex, treating spaces as flexible separators."""
    parts = re.escape(name).split(r"\ ")  # re.escape turns space into "\ "
    return r"[\s\-]".join(parts)

def _unit_alternation(names) -> str:
    return "|".join(_escape_unit_name(n) for n in sorted(names, key=len, reverse=True))

# Number words that can appear before a unit
# "and" belongs INSIDE a number ("one hundred and twenty"), never at its start:
# a leading "and" swallowed the conjunction as the count — "long, and an entire
# shichen" matched num="and an", failed to parse, and the duration was left as
# pinyin (book 106 ch194, ch209).
_number_words = (
    r"(?!and\b)"
    r"(?:(?:one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|"
    r"twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|"
    r"hundred|thousand|million|half|and|a|an)[\s\-]*)+"
)

# Numeric patterns: 1000, 1,000, 3.5
# The `(?:[\d,]*\d)?` middle clause forbids a trailing comma in the integer
# part — without it the regex greedy-eats a stray comma (e.g. "Tier-11, Zhang
# Yu" → num="11,", unit="Zhang") and a personal name gets mis-annotated.
_numeric = r"(?:\d(?:[\d,]*\d)?\.?\d*)"

# Vague quantifiers. For hour-based replace units (shichen, double-hour) these
# are converted to the same vague count of the English unit ("several shichen"
# -> "several hours") — accepting that the magnitude is approximate. For all
# other units they're matched but left untouched (see _convert_match).
_vague_quantifier = (
    r"(?:a\s+few|few|several|so\s+many|many|some|numerous|countless|"
    r"myriad|various|multiple|dozens\s+of|hundreds\s+of|thousands\s+of)"
)

# Fraction phrases like "a quarter", "three-quarters", "half" that can prefix
# a unit phrase via "... of a <unit>" (e.g. "a quarter of a ke")
_FRACTION_DENOMS = {
    "quarter": 4, "quarters": 4, "fourth": 4, "fourths": 4,
    "third": 3, "thirds": 3,
    "fifth": 5, "fifths": 5,
    "sixth": 6, "sixths": 6,
    "seventh": 7, "sevenths": 7,
    "eighth": 8, "eighths": 8,
    "ninth": 9, "ninths": 9,
    "tenth": 10, "tenths": 10,
}

_fraction_phrase = (
    r"(?:(?:a|an|one|two|three|four|five|six|seven|eight|nine)[\s\-]"
    r"(?:quarters?|fourths?|thirds?|fifths?|sixths?|sevenths?|eighths?|ninths?|tenths?)"
    r"|half)"
)

# Range prefix: the low bound of "two or three shichen" / "two to three ke".
# Without this the pattern binds <num> to the numeral touching the unit and the
# low bound is left unscaled ("two or three shichen" -> "two or six hours").
#
# Word separators ("or"/"to") require surrounding whitespace; dashes do not.
# A bare hyphen is NOT a separator, because word-numbers are themselves
# hyphenated ("twenty-one shichen" is one quantity, not a range).
_RANGE_SEP = r"(?:\s+(?P<sep>or|to)\s+|\s*(?P<dash>[–—])\s*)"

# Emphasis modifiers the translator puts in front of a duration ("a full
# half-shichen", "an entire shichen", "a good two ke"). These are consumed INTO
# the match so the replacement can re-place them grammatically — left outside,
# "a full" + "half-shichen"→"an hour" collided as "a full an hour" (book 71).
# Where the word lands depends on the word itself:
#   lead — before the count: "a full two hours", "a good two hours"
#   mid  — after the count:  "two whole hours" ("an entire two hours" is wrong)
_LEAD_ADJ = ("full", "good", "solid", "mere")
_MID_ADJ = ("whole", "entire", "complete")
_EMPHASIS_ADJ = _LEAD_ADJ + _MID_ADJ
# Lead words that also read fine after the count, so a rounding hedge can take
# the front slot instead: "about ten full minutes". "mere" is not one of them.
_HEDGE_MID_ADJ = ("full", "good", "solid")
_adj_alt = "|".join(sorted(_EMPHASIS_ADJ, key=len, reverse=True))

# Main pattern. The quantity prefix is one of three branches:
#   1. a vague quantifier ("several", "a few")
#   2. a number / word-number, optionally with a range low bound and/or a
#      fractional prefix ("two or three ke", "a quarter of a ke")
#   3. a hyphenated/bare fraction directly on the unit ("a quarter-shichen")
def _build_pattern(unit_names: str) -> re.Pattern:
    return re.compile(
        r"(?<!['\w])"                       # not preceded by word char or apostrophe
        # Optional leading emphasis phrase ("a full <qty>", "another <qty>"). Only
        # matches when a quantity follows; "a full shichen" (no quantity) falls
        # through to num="a" + more="full", which lands in the same place.
        r"(?:(?P<det>(?:(?:a|an|the)[\s\-]+)?(?P<detadj>" + _adj_alt + r")|another)[\s\-]+)?"
        r"(?:"
            r"(?P<vague>" + _vague_quantifier + r")[\s\-]+"               # branch 1
            r"|"
            r"(?:(?P<frac>" + _fraction_phrase + r")\s+of\s+)?"          # branch 2
            r"(?:"
                r"(?:(?P<lo>" + _numeric + r"|" + _number_words + r")"
                    + _RANGE_SEP + r")"                                  # word/dash range low bound
                r"|"
                # ASCII-hyphen range, DIGITS ONLY on both sides ("3-5 shichen").
                # Word-numbers are themselves hyphenated ("twenty-one"), so a bare
                # hyphen is only a range separator between two plain numerals.
                r"(?:(?P<lo_d>" + _numeric + r")\s*(?P<hyph>-)\s*(?=\d))"
            r")?"                                                        # optional range low bound
            # An emphasis word standing in for the count is itself "one"
            # ("the full shichen" = one shichen), captured so it survives the swap.
            r"(?P<num>" + _numeric + r"|a\s+single|single|another|" + _number_words
                + r"|(?:(?:a|an|the)[\s\-]+)?(?P<numfill>" + _adj_alt + r")|a|an)[\s\-]+"
            r"|"
            r"(?P<fracunit>" + _fraction_phrase + r")[\s\-]+"            # branch 3
        r")"
        r"(?:(?P<more>more|" + _adj_alt + r")[\s\-]+)?"  # optional filler ("two more/whole/full shichen")
        r"(?P<unit>" + unit_names + r")"    # unit name
        r"s?"                               # optional plural
        r"(?!\s*\()"                        # negative lookahead: not already annotated
        r"(?!['\w])",                       # not followed by word char or apostrophe
        re.IGNORECASE
    )

# Bare unit pattern: a time unit word with no quantity at all, used as a point
# in time ("at the appointed shichen"). Only hour-based replace units qualify —
# for those, the bare word maps to the English time word ("hour").
def _build_bare_pattern(unit_names: str) -> re.Pattern:
    return re.compile(
        r"(?<!['\w])"
        r"(?P<unit>" + unit_names + r")"
        r"s?"
        r"(?!\s*\()"
        r"(?!['\w])",
        re.IGNORECASE
    )


class _UnitSet:
    """One language's units and the patterns compiled from them."""

    def __init__(self, lang: str, units: Dict[str, Unit]):
        self.lang = lang
        self.units = units
        self.pattern = _build_pattern(_unit_alternation(units))
        bare = [n for n, u in units.items() if u.action == "replace" and u.unit == "hour"]
        self.bare_pattern = _build_bare_pattern(_unit_alternation(bare)) if bare else None
        # The earthly-branch clock ("third ke of the wu hour") and qualified
        # ke ("every ke") are the Chinese time system; they run only for a
        # table that carries it.
        self.chinese_time = "ke" in units and "shichen" in units

    def lookup(self, matched_text: str) -> Optional[Unit]:
        """Look up a unit by its matched text, normalizing spaces/hyphens."""
        normalized = matched_text.lower()
        for key in (normalized, normalized.replace("-", " "), normalized.replace(" ", "-")):
            if key in self.units:
                return self.units[key]
        return None


_tables_lock = threading.Lock()
_tables_state: dict = {"mtime": None, "tables": None, "sets": {}}


def _unit_set(source_language: Optional[str]) -> Optional[_UnitSet]:
    """The language's unit set, or None when units.json has no table for it.

    Re-reads units.json when its mtime changes, so an edit from the Settings
    page applies without a restart. A reload that fails to parse keeps the
    previous table (and logs) rather than taking conversion down."""
    lang = normalize_language(source_language)
    try:
        mtime = os.stat(UNITS_PATH).st_mtime_ns
    except OSError:
        mtime = None
    with _tables_lock:
        st = _tables_state
        if st["tables"] is None or mtime != st["mtime"]:
            try:
                with open(UNITS_PATH, "r", encoding="utf-8") as f:
                    tables = parse_units(json.load(f))
            except (OSError, ValueError) as e:   # JSONDecodeError is a ValueError
                if st["tables"] is None:
                    raise
                logger.error(f"units.json reload failed, keeping the previous table: {e}")
                tables = st["tables"]
            st.update(mtime=mtime, tables=tables, sets={})
        if lang not in st["tables"]:
            return None
        uset = st["sets"].get(lang)
        if uset is None:
            uset = st["sets"][lang] = _UnitSet(lang, st["tables"][lang])
        return uset


# ── Earthly-branch hours (points in time on the traditional 12-hour clock) ──
# A traditional Chinese day is twelve double-hours, each named for an earthly
# branch / zodiac animal (子=Rat, 午=Horse, …). A "ke" (刻) is 1/8 of a
# double-hour = 15 minutes, so 午时三刻 ("third ke of the wu hour") is a point in
# time 45 minutes into the Horse hour.
#
# The translation model emits these points in a wild variety of English forms
# ("third quarter of the noon hour", "fourth ke of the You hour", "three-quarters
# past the Hour of the Rabbit", "the start of the hour of the snake", …). We
# normalise them all to one canonical form:
#       "<minutes> minutes past the hour of the <Animal>"
# keeping the idiomatic "noon"/"midnight" only when the translator used them.
# This is distinct from the span conversions above: shichen/ke as *durations*
# become hours/minutes, but as *points in time* they become clock positions.

_BRANCH_ANIMALS = [
    "Rat", "Ox", "Tiger", "Rabbit", "Dragon", "Snake",
    "Horse", "Goat", "Monkey", "Rooster", "Dog", "Pig",
]

# pinyin reading of the earthly branch -> branch index
_BRANCH_PINYIN = {
    "zi": 0, "chou": 1, "yin": 2, "mao": 3, "chen": 4, "si": 5,
    "wu": 6, "wei": 7, "shen": 8, "you": 9, "xu": 10, "hai": 11,
}

# zodiac animal word (incl. common synonyms) -> branch index
_BRANCH_ANIMAL_WORDS = {
    "rat": 0, "ox": 1, "tiger": 2, "rabbit": 3, "hare": 3, "dragon": 4,
    "snake": 5, "serpent": 5, "horse": 6, "goat": 7, "sheep": 7, "ram": 7,
    "monkey": 8, "rooster": 9, "cock": 9, "chicken": 9, "dog": 10,
    "pig": 11, "boar": 11,
}

# idioms the translator may use for 午时 (noon) and 子时 (midnight)
_BRANCH_SPECIAL = {"noon": 6, "midnight": 0}

# Map a ke/quarter count word to its integer value (1..8).
_KE_VALUES = {}
for _i, _w in enumerate(
    ["first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth"], start=1
):
    _KE_VALUES[_w] = _i
for _i, _w in enumerate(
    ["one", "two", "three", "four", "five", "six", "seven", "eight"], start=1
):
    _KE_VALUES[_w] = _i
for _i in range(1, 9):
    _KE_VALUES[str(_i)] = _i

_BRANCH_PIN_ALT = "|".join(sorted(_BRANCH_PINYIN, key=len, reverse=True))
# "you" / "hour of you" is too collision-prone in English, so the bare
# "hour of <pinyin>" frame excludes it (the distinctive "you hour" still works).
_BRANCH_PIN_NOYOU = "|".join(
    sorted((p for p in _BRANCH_PINYIN if p != "you"), key=len, reverse=True)
)
_BRANCH_ANI_ALT = "|".join(sorted(_BRANCH_ANIMAL_WORDS, key=len, reverse=True))
# Joined romanisations like "sishi" (巳时), "maoshi" (卯时).
_BRANCH_JOINED_ALT = "|".join(
    sorted((p + "shi" for p in _BRANCH_PINYIN), key=len, reverse=True)
)

_KE_NUM = (
    r"(?:[1-8]|one|two|three|four|five|six|seven|eight|"
    r"first|second|third|fourth|fifth|sixth|seventh|eighth)"
)

# Optional prefix specifying the position within the hour. Each branch consumes
# any leading article so a sentence-initial "The third quarter of ..." doesn't
# leave a dangling "The".
_POINT_PREFIX = (
    r"(?P<prefix>"
        # "third quarter of", "fourth ke of", "third mark of", "a fifth quarter of"
        r"(?:the\s+|an?\s+)?(?P<ke>" + _KE_NUM + r")(?:st|nd|rd|th)?[\s\-](?:ke|quarters?|marks?)\s+(?:of|into)\s+"
        r"|"
        # "a quarter past", "half past", "three-quarters past"
        r"(?:the\s+)?(?P<fp>a\s+quarter|quarter|half|three[\s\-]quarters)\s+past\s+"
        r"|"
        # already-converted "forty-five minutes past"
        r"(?:the\s+|an?\s+)?(?P<mp>\d{1,3}|" + _number_words + r")[\s\-]minutes?\s+(?:past|after|into)\s+"
        r"|"
        # 初: "the start of", "the beginning of" (a leading "at" stays outside)
        r"(?P<st>(?:the\s+)?(?:start|beginning)\s+of\s+)"
    r")"
)

# The hour itself, in any of the forms the model produces. Every branch allows a
# leading article so the bare form ("the wu hour") is consumed whole.
_POINT_HOUR = (
    r"(?:"
        r"(?:the\s+)?(?P<pin>" + _BRANCH_PIN_ALT + r")[\s\-](?:hour|shichen)"
        r"|(?:the\s+)?hour\s+of\s+(?:the\s+)?(?P<pin2>" + _BRANCH_PIN_NOYOU + r")\b"
        # "hour of You" only when capitalised: the pronoun is lowercase
        # mid-sentence. Not before a hyphen ("You-know-what") or a capitalised
        # word ("an hour of You Wei's time" — You is also a surname).
        r"|(?:the\s+)?hour\s+of\s+(?P<pinyou>(?-i:You))\b(?!-|(?-i:\s+[A-Z]))"
        # Joined romanisations ("sishi") only in lowercase: capitalised, they
        # are names — 无始 Wushi, 海石 Haishi, 孟无世 Meng Wushi were all
        # rewritten into "the hour of the Horse/Pig" before this guard.
        r"|(?P<pinj>(?-i:" + _BRANCH_JOINED_ALT + r"))\b"
        r"|(?:the\s+)?(?P<ani>" + _BRANCH_ANI_ALT + r"|noon|midnight)[\s\-]hour"
        r"|(?:the\s+)?hour\s+of\s+(?:the\s+)?(?P<ani2>" + _BRANCH_ANI_ALT + r")\b"
        r"|(?P<sp>noon|midnight)\b"
        # Bare branch names ("third quarter of Zi", "first ke of the Snake"):
        # only resolved when a position prefix precedes them (see resolver), since
        # bare "Zi"/"Snake" are far too collision-prone on their own.
        # …and never when a capitalised word follows: that is a name
        # ("ten minutes after Chen Yiran").
        r"|(?:the\s+)?(?P<pinbare>" + _BRANCH_PIN_NOYOU + r")\b(?!(?-i:\s+[A-Z]))"
        r"|(?:the\s+)?(?P<anibare>" + _BRANCH_ANI_ALT + r")\b"
    r")"
)

# The almanac stamp puts the ke AFTER the hour: "Yuzhong, Si hour, third ke".
# Lowercase "ke" only — "Ke" is also a surname (柯).
_POINT_KE_SUFFIX = (
    r"(?:,\s+(?:the\s+)?(?P<kesuf>" + _KE_NUM + r")(?:st|nd|rd|th)?\s+(?-i:ke))?"
)

_POINT_RE = re.compile(
    r"(?<![\w'])"
    + r"(?:" + _POINT_PREFIX + r")?"
    + _POINT_HOUR
    + _POINT_KE_SUFFIX
    + r"(?![\w'])",
    re.IGNORECASE,
)

# A bare "ke" with a qualifier but no count ("every ke", "the next ke", "per
# ke", "the first ke" … "the ninth ke") is a quarter hour, as a span or a slot
# in a countdown. Counted forms ("two ke", "a ke") go through the unit pattern, and
# "third ke of the wu hour" through _POINT_RE; this catches what both miss.
_KE_QUALIFIED_RE = re.compile(
    r"(?<![\w'])(?P<q>(?:every|each|next|last|final|extra|per|another|same|single|whole|entire|"
    r"first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh|twelfth)\s+)"
    r"(?-i:ke)(?![\w'(])",
    re.IGNORECASE,
)


# Text before a point match that means the match opens a sentence: start of line
# or table cell, or after terminal punctuation — then any opening quotes/brackets.
_POINT_SENTENCE_START_RE = re.compile(
    r"(?:^\s*|[.!?…][\"”’')\]】]*\s+|\|\s*)[\"“‘'(\[【]*$"
)
# An opening quote starts dialogue ('he said, "Zi hour is near"') — unless an
# article precedes it, which makes it a quoted term ('the "Mao hour roll call"').
_POINT_OPEN_QUOTE_RE = re.compile(r"[\"“]$")
_POINT_QUOTED_TERM_RE = re.compile(r"\b(?:the|an?)\s+[\"“]$", re.IGNORECASE)
# A definite article right before the match ("the very ", 'the "'). Not
# "that"/"this": "knew that the hour of the Rat had come" is a conjunction.
_POINT_DEFINITE_BEFORE_RE = re.compile(
    r"\bthe(?:\s+very)?\s+[\"“‘']?$", re.IGNORECASE
)
# An indefinite article right before the match ("at a Yin hour").
_POINT_INDEFINITE_BEFORE_RE = re.compile(r"\b(an?)\s+$", re.IGNORECASE)


def _drop_indefinite_before_point(prefix: str, replacement: str) -> tuple:
    """'at a Yin hour' must not become 'at a the hour of the Tiger'. When the
    canonical label brings its own "the", drop the model's "a"/"an" instead,
    carrying its capital over to the label. Returns (prefix, replacement)."""
    if not replacement[:4].lower() == "the ":
        return prefix, replacement
    m = _POINT_INDEFINITE_BEFORE_RE.search(prefix)
    if not m:
        return prefix, replacement
    if m.group(1)[:1].isupper():
        replacement = replacement[:1].upper() + replacement[1:]
    return prefix[:m.start()], replacement


def _minutes_phrase(minutes: int) -> str:
    """Render a minute offset as words: 45 -> 'forty-five minutes',
    60 -> 'an hour', 105 -> 'an hour and forty-five minutes'."""
    minutes = int(round(minutes))
    if minutes < 60:
        suffix = "" if minutes == 1 else "s"
        return f"{_int_to_words(minutes)} minute{suffix}"
    hours, rem = divmod(minutes, 60)
    hours_word = "an hour" if hours == 1 else f"{_int_to_words(hours)} hours"
    if rem == 0:
        return hours_word
    suffix = "" if rem == 1 else "s"
    return f"{hours_word} and {_int_to_words(rem)} minute{suffix}"


def _convert_point_match(match: re.Match) -> str:
    """Replace callback for traditional point-in-time expressions (_POINT_RE).

    Returns the original text unchanged when the match can't be resolved or is
    already canonical (e.g. a bare "noon" with no position prefix)."""
    full = match.group(0)
    gd = match.groupdict()
    has_prefix = bool(gd.get("prefix"))

    # ── Resolve which earthly branch this is, and whether the translator used a
    #    noon/midnight idiom we should preserve. ──
    idx = None
    special_word = None
    # Lowercase "yin" is yin/yang: 阴年阴月阴时 = "a yin year, a yin month, a yin
    # hour". The branch 寅 is a proper noun and comes capitalised ("Yin hour").
    if gd.get("pin") == "yin" or gd.get("pin2") == "yin" or gd.get("pinbare") == "yin":
        return full
    if gd.get("pin"):
        idx = _BRANCH_PINYIN[gd["pin"].lower()]
    elif gd.get("pin2"):
        idx = _BRANCH_PINYIN[gd["pin2"].lower()]
    elif gd.get("pinyou"):
        idx = _BRANCH_PINYIN["you"]
    elif gd.get("pinj"):
        idx = _BRANCH_PINYIN[gd["pinj"].lower()[:-3]]  # strip trailing "shi"
    elif gd.get("ani") or gd.get("ani2"):
        word = (gd.get("ani") or gd.get("ani2")).lower()
        if word in _BRANCH_SPECIAL:
            idx = _BRANCH_SPECIAL[word]
            special_word = word
        else:
            idx = _BRANCH_ANIMAL_WORDS[word]
    elif gd.get("sp"):
        # A bare "noon"/"midnight" is only a point-in-time worth rewriting when a
        # position prefix pins it down ("half past noon"); otherwise leave it.
        if not has_prefix:
            return full
        idx = _BRANCH_SPECIAL[gd["sp"].lower()]
        special_word = gd["sp"].lower()
    elif gd.get("pinbare") or gd.get("anibare"):
        # A bare branch name with no "hour" word ("third quarter of Zi") is only
        # safe to resolve when a position prefix precedes it.
        if not has_prefix:
            return full
        # "the start of Wei" is a dynasty far more often than an hour; only a
        # quarter/ke/minute prefix makes a bare branch name a clock position.
        if gd.get("st"):
            return full
        if gd.get("pinbare"):
            idx = _BRANCH_PINYIN[gd["pinbare"].lower()]
        else:
            word = gd["anibare"].lower()
            if word in _BRANCH_SPECIAL:
                idx = _BRANCH_SPECIAL[word]
                special_word = word
            else:
                idx = _BRANCH_ANIMAL_WORDS[word]

    if idx is None:
        return full

    # A hyphenated "X-hour" in front of a lowercase noun is a compound name,
    # not a time: 午时草 "Wu-hour grass" had become "the hour of the Horse grass".
    if (not has_prefix and (gd.get("pin") or gd.get("ani"))
            and re.search(r"-hour$", full, re.IGNORECASE)
            and re.match(r"\s+[a-z]", match.string[match.end():])):
        return full

    # ── Determine the position within the hour. ──
    minutes = None
    start = False
    if gd.get("ke"):
        minutes = 15 * _KE_VALUES[gd["ke"].lower()]
    elif gd.get("fp"):
        frac = re.sub(r"[\s\-]+", " ", gd["fp"].lower())
        minutes = {"a quarter": 15, "quarter": 15, "half": 30, "three quarters": 45}.get(frac)
        if minutes is None:
            return full
    elif gd.get("mp"):
        val = _word_to_number(gd["mp"])
        if val is None:
            return full
        minutes = int(round(val))
    elif gd.get("st"):
        start = True
    if gd.get("kesuf") and minutes is None and not start:
        minutes = 15 * _KE_VALUES[gd["kesuf"].lower()]

    # ── Build the canonical labels. ──
    animal = _BRANCH_ANIMALS[idx]
    if special_word == "noon":
        past_label, of_label, bare_label = "noon", "the noon hour", "noon"
    elif special_word == "midnight":
        past_label, of_label, bare_label = "midnight", "the midnight hour", "midnight"
    else:
        past_label = of_label = bare_label = f"the hour of the {animal}"

    if start:
        out = f"the start of {of_label}"
    elif minutes is not None:
        out = f"{_minutes_phrase(minutes)} past {past_label}"
    else:
        out = bare_label

    # Casing. A branch name is capitalised as a proper noun wherever it stands
    # ("Yuzhong, Si hour"), so its capital says nothing about the sentence —
    # copying it produced "Yuzhong, The hour of the Snake". Capitalise only a
    # phrase that opens its sentence; ALL-CAPS still mirrors.
    before = match.string[:match.start()]
    if len(full) > 1 and full.isupper():
        out = out.upper()
    elif full[:1].isupper() and (
        _POINT_SENTENCE_START_RE.search(before)
        or (_POINT_OPEN_QUOTE_RE.search(before)
            and not _POINT_QUOTED_TERM_RE.search(before))
    ):
        out = out[:1].upper() + out[1:]

    # Article collisions. Every canonical label starts with "the", and the
    # model's own determiner sits just outside the match: "at the very start of
    # Xu hour" became "the very the start", 'the "Mao hour roll call"' became
    # 'the "the hour…'. After "the" the label drops its own "the"; an
    # indefinite "a Yin hour" (any Yin hour) is handled by the caller, which
    # drops the "a" itself (see _drop_indefinite_before_point).
    if out.startswith("the ") and _POINT_DEFINITE_BEFORE_RE.search(before):
        out = out[4:]
    return out


def _parse_fraction_phrase(text: str) -> Optional[float]:
    """Parse phrases like 'a quarter', 'three-quarters', 'half' to a float multiplier."""
    words = text.strip().lower().replace("-", " ").split()
    if not words:
        return None
    if len(words) == 1:
        if words[0] == "half":
            return 0.5
        if words[0] in _FRACTION_DENOMS:
            return 1.0 / _FRACTION_DENOMS[words[0]]
        return None
    numerator_word, denom_word = words[0], words[1]
    if numerator_word in ("a", "an"):
        numerator = 1.0
    else:
        parsed = _word_to_number(numerator_word)
        if parsed is None:
            return None
        numerator = parsed
    if denom_word in _FRACTION_DENOMS:
        return numerator / _FRACTION_DENOMS[denom_word]
    return None


_VAGUE_BEFORE = re.compile(
    r"(?:several|few|many|some|numerous|dozens\s+of|hundreds\s+of|"
    r"thousands\s+of|countless|myriad|various|multiple)\s+$",
    re.IGNORECASE,
)


def _match_case(template: str, text: str) -> str:
    """Mirror the casing of `template` onto `text` (UPPER / Title / lower).

    "Half a shichen" -> "An hour"; "half a shichen" -> "an hour";
    "HALF A SHICHEN" -> "AN HOUR". Only the leading character is touched.
    """
    if not text or not template:
        return text
    if len(template) > 1 and template.isupper():
        return text.upper()
    if template[:1].isupper():
        return text[:1].upper() + text[1:]
    return text


def _place_emphasis(art: Optional[str], adj: Optional[str], *,
                    singular: bool, hedged: bool) -> Tuple[Optional[str], Optional[str]]:
    """Decide where an emphasis modifier goes in the replacement.

    Returns (lead, mid): `lead` sits in front of the count and keeps its article
    ("a full two hours"), `mid` sits between count and unit ("two whole hours").
    A singular value drops the count entirely, so the article carries it ("a
    full hour"). A hedged plural reads better with the word mid — "about ten
    full minutes" beats "about a full ten minutes".
    """
    if not adj:
        return None, None
    lead = f"{art} {adj}" if art else adj
    if singular:
        return lead, None                 # "a full hour", "another hour"
    if adj in _MID_ADJ:
        return None, adj                  # "two whole hours"
    if hedged and adj in _HEDGE_MID_ADJ:
        return None, adj                  # "about ten full minutes"
    # Words that only work in front of the count keep it, and the hedge moves
    # ahead of them: "about another ten minutes", never "another about …".
    return lead, None


def _convert_match(match: re.Match, uset: _UnitSet) -> str:
    """Replace callback for unit matches (from the unit set's pattern)."""
    full = match.group(0)
    gd = match.groupdict()
    fraction_str = gd.get("frac")
    fracunit_str = gd.get("fracunit")
    vague_str = gd.get("vague")
    num_str = (gd.get("num") or "").strip()
    more_str = gd.get("more")
    det_str = gd.get("det")
    det_adj = gd.get("detadj")
    numfill_str = gd.get("numfill")
    unit_text = gd["unit"]

    # A unit word immediately followed by a capitalised word is part of a
    # personal name, not a measurement. Several Chinese surnames romanise onto
    # unit names (李 -> li, 张 -> zhang, 梁 -> liang), and the article or numeral
    # in front of them reads as a quantity, so "a Zhang Juzheng", "one Li
    # Sancai" and "another Li Zaiting" all match perfectly well and came out
    # annotated as distances. A genuine measurement is never followed directly
    # by a capitalised word, so the check costs nothing.
    #
    # Only a CAPITALISED unit word can be that surname. A lowercase one is the
    # unit, whatever follows it: "in less than two ke Zhao Xing saw a city" and
    # "the two shichen Zhao Xing spent with them" are durations followed by the
    # sentence's subject, and the unconditional check left them as pinyin
    # (book 106). "Twelve Shichen Grass" still stands — its unit is capitalised.
    if unit_text[:1].isupper() and re.match(r"\s+[A-Z][a-z]", match.string[match.end():]):
        return full

    unit_info = uset.lookup(unit_text)
    if unit_info is None:
        return full
    base_value, base_unit, action, numeral = (
        unit_info.value, unit_info.unit, unit_info.action, unit_info.numeral)

    # ── Vague quantifier path ("several shichen" -> "several hours") ──
    # Only for hour-based replace units; anything else is left untouched so we
    # don't misstate magnitude ("several jiazi", "several ke") or try to annotate
    # an uncountable phrase ("several zhang").
    if vague_str:
        # An emphasis word on a vague count ("a full several shichen") is
        # incoherent phrasing; leave it for a human rather than guess.
        if det_str or action != "replace":
            return full
        vague_norm = re.sub(r"\s+", " ", vague_str.strip())
        if base_unit == "hour":
            return _match_case(full, f"{vague_norm} {base_unit}s")
        # The ke is a quarter hour, so a vague count of them is a vague count
        # of quarter hours ("how many ke" -> "how many quarter hours"); the
        # magnitude needs no scaling. Before this they were left as pinyin.
        if base_unit == "minute" and base_value == 15:
            return _match_case(full, f"{vague_norm} quarter hours")
        return full

    # ── Determine the numeric quantity ──
    if fracunit_str:
        # Hyphenated/bare fraction directly on the unit ("a quarter-shichen").
        number = _parse_fraction_phrase(fracunit_str)
        if number is None:
            return full
    else:
        # Check text before match for vague quantifiers ("several thousand X")
        before = match.string[:match.start()]
        if _VAGUE_BEFORE.search(before):
            return full

        # Handle "a"/"an"/"a single"/"single"/"the full" (numfill) as 1
        normalized = re.sub(r"[\s\-]+", " ", num_str.strip().lower())
        if normalized in ("a", "an", "a single", "single", "another") or numfill_str:
            number = 1.0
        else:
            number = _word_to_number(num_str)

        if number is None:
            return full  # vague quantifier, skip

        # Apply fractional multiplier if we matched one (e.g. "a quarter of a ke")
        if fraction_str:
            frac_mult = _parse_fraction_phrase(fraction_str)
            if frac_mult is None:
                return full
            number *= frac_mult

    # ── Emphasis modifier ("a full …", "another …", "the whole …") ──
    # Three ways it reaches us; all reduce to (article, adjective) so the output
    # assembly can place it where it reads correctly.
    art = adj = None
    if det_str:
        det_words = re.sub(r"[\s\-]+", " ", det_str.strip().lower()).split()
        if det_adj:
            adj = det_adj.lower()
            art = det_words[0] if len(det_words) > 1 else None
        else:
            adj = det_words[0]                      # bare "another"
    elif numfill_str:
        adj = numfill_str.lower()
        num_words = re.sub(r"[\s\-]+", " ", num_str.strip().lower()).split()
        art = num_words[0] if len(num_words) > 1 else None
    elif (more_str and more_str.lower() in _EMPHASIS_ADJ
            and re.sub(r"[\s\-]+", " ", num_str.strip().lower()) in ("a", "an")):
        # "a full shichen": the article carries the count of one, the adjective
        # the emphasis. Re-emit as a lead phrase ("a full two hours") instead of
        # stranding the article in front of a plural.
        art, adj = num_str.strip().lower(), more_str.lower()
        more_str = None

    # ── Range path ("two or three shichen" -> "four or six hours") ──
    # Both endpoints must scale. Handled before the single-value path so the low
    # bound is never left in the source unit.
    lo_str = gd.get("lo") or gd.get("lo_d")
    if lo_str and not fracunit_str and not fraction_str:
        lo_number = _word_to_number(lo_str)
        if lo_number is None:
            return full
        is_word_form = not any(c.isdigit() for c in num_str) and not any(c.isdigit() for c in lo_str)
        approximate = is_word_form and action == "replace"

        lo_scaled, lo_unit, lo_rounded = _scale(lo_number * base_value, base_unit,
                                                approximate=approximate)
        hi_scaled, hi_unit, hi_rounded = _scale(number * base_value, base_unit,
                                                approximate=approximate)
        # If the two endpoints land in different units ("half an hour or two hours"),
        # bail out rather than emit a nonsense range.
        if lo_unit != hi_unit:
            return full

        if numeral == "english":
            lo_fmt, hi_fmt = _number_to_words(lo_scaled), _number_to_words(hi_scaled)
        else:
            lo_fmt, hi_fmt = _format_number(lo_scaled), _format_number(hi_scaled)

        dash = gd.get("dash") or gd.get("hyph")
        span = (f"{lo_fmt}{dash}{hi_fmt}" if dash
                else f"{lo_fmt} {(gd.get('sep') or 'or').lower()} {hi_fmt}")
        if action == "replace":
            unit_label = hi_unit
            if hi_scaled != 1.0 and not unit_label.endswith("s"):
                unit_label += "s"
            lead, mid = _place_emphasis(art, adj, singular=False,
                                        hedged=bool(lo_rounded or hi_rounded))
            filler = mid or (more_str.lower() if more_str else None)
            body = f"{span} {filler} {unit_label}" if filler else f"{span} {unit_label}"
            if lead:
                body = f"{lead} {body}"
            output = f"about {body}" if (lo_rounded or hi_rounded) else body
            return _match_case(full, output)
        return f"{full} ({lo_fmt}–{hi_fmt} {hi_unit})"

    is_another = num_str.strip().lower() == "another"
    raw = number * base_value

    # Word-form source ("twenty ke") signals casual phrasing; numeric source
    # ("20 ke", "20.5 ke") is treated as deliberate. Approximation only kicks
    # in on the replace path — annotations stay literal.
    is_word_form = not any(c.isdigit() for c in num_str)
    approximate = is_word_form and action == "replace"
    scaled, final_unit, was_rounded = _scale(raw, base_unit, approximate=approximate)

    if numeral == "english":
        formatted = _number_to_words(scaled)
    else:
        formatted = _format_number(scaled)

    if action == "replace":
        lead, mid = _place_emphasis(art, adj, singular=(scaled == 1.0),
                                    hedged=was_rounded)
        filler = mid or (more_str.lower() if more_str else None)
        # Special case: "an hour" reads better than "one hour" — but only when
        # nothing else needs the slot in front of the unit ("one more hour", not
        # "an more hour"; "a full hour", never "a full an hour").
        if (numeral == "english" and scaled == 1.0 and final_unit == "hour"
                and not is_another and not filler and not lead):
            output = "about an hour" if was_rounded else "an hour"
        elif lead and scaled == 1.0:
            # The lead phrase's article already says "one" ("a full hour").
            body = f"{lead} {final_unit}"
            output = f"about {body}" if was_rounded else body
        else:
            # Pluralize the unit if value != 1
            unit_label = final_unit
            if scaled != 1.0 and not unit_label.endswith("s"):
                unit_label += "s"
            # Keep the filler word between the number and unit, echoing whatever
            # matched: "four more hours", "four whole hours", "two full hours".
            body = f"{formatted} {filler} {unit_label}" if filler else f"{formatted} {unit_label}"
            if lead:
                body = f"{lead} {body}"      # "a full four hours"
            if is_another:
                body = f"another {body}"
            output = f"about {body}" if was_rounded else body
        # Mirror the original phrase's casing onto the replacement so a
        # sentence-initial "Half a shichen" yields "An hour", not "an hour".
        return _match_case(full, output)
    else:
        # Default: annotate — leaves the original phrase untouched
        return f"{full} ({formatted} {final_unit})"


def _convert_bare_match(match: re.Match, uset: _UnitSet) -> str:
    """Replace callback for bare unit matches (from the unit set's bare_pattern).

    A bare time unit with no quantity is a point in time ("the appointed
    shichen") rather than a span, so it maps to the English time word with no
    number: "shichen" -> "hour", "shichens" -> "hours".
    """
    full = match.group(0)
    unit_text = match.group("unit")

    unit_info = uset.lookup(unit_text)
    if unit_info is None:
        return full
    base_unit, action = unit_info.unit, unit_info.action
    if action != "replace" or base_unit != "hour":
        return full

    # Preserve plurality from the matched token ("shichens" -> "hours").
    plural = full.lower() != unit_text.lower()
    label = f"{base_unit}s" if plural else base_unit
    return _match_case(full, label)


def _extract_sentence_context(line: str, match_start: int, match_end: int,
                               max_len: int = 200) -> Tuple[str, int]:
    """Extract the sentence containing the match, capped at max_len chars.

    Returns (context_string, offset_of_context_start_within_line).
    """
    # Search backwards for sentence start
    sent_start = 0
    for i in range(match_start - 1, -1, -1):
        if line[i] in '.!?;\n':
            sent_start = i + 1
            break

    # Search forwards for sentence end
    sent_end = len(line)
    for i in range(match_end, len(line)):
        if line[i] in '.!?;\n':
            sent_end = i + 1
            break

    # Offset past the whitespace strip() removes, or every match after the first
    # sentence of a line is highlighted one character late ("t>>>hree jin.<<<").
    raw = line[sent_start:sent_end]
    context = raw.strip()
    ctx_offset = sent_start + (len(raw) - len(raw.lstrip()))

    # Cap at max_len centered on match if sentence is very long
    if len(context) > max_len:
        match_center = (match_start + match_end) // 2 - sent_start
        half = max_len // 2
        ctx_start = max(0, match_center - half)
        ctx_end = min(len(context), ctx_start + max_len)
        context = context[ctx_start:ctx_end]
        ctx_offset = sent_start + ctx_start

    return context, ctx_offset


def _load_cleaning_prompt() -> str:
    """Load the unit cleaning prompt from the prompts directory."""
    prompt_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "prompts", "unit_cleaning_prompt.txt")
    with open(prompt_path, "r", encoding="utf-8") as f:
        return f.read()


def _match_contexts(lines: List[str], matches: list) -> dict:
    """``{str(match_id): sentence}`` with each match wrapped in >>> <<< markers."""
    context = {}
    for line_idx, match, match_id in matches:
        line = lines[line_idx]
        sentence, ctx_offset = _extract_sentence_context(
            line, match.start(), match.end()
        )
        # Calculate match position within the context string
        rel_start = match.start() - ctx_offset
        rel_end = match.end() - ctx_offset
        # Highlight the match
        context[str(match_id)] = (sentence[:rel_start] + ">>>" +
                                  sentence[rel_start:rel_end] + "<<<" +
                                  sentence[rel_end:])
    return context


# ── Jev false-positive filter ───────────────────────────────────────
# The same rules as unit_cleaning_prompt.txt, as a Jev choice question. Each
# match is its own question with the sentence in its instructions, so a whole
# chapter goes in one request (~0.3s) rather than one request per match.
def _unit_examples(uset: _UnitSet, limit: int = 10) -> str:
    """'li, jin, zhang, …' — the language's own unit names, for the prompts."""
    names = [n for n in uset.units if " " not in n and "-" not in n]
    return ", ".join(names[:limit])


def _jev_instructions(uset: _UnitSet) -> str:
    lang = LANGUAGE_NAMES.get(uset.lang, uset.lang)
    return (
        f"In the sentence below, from an English translation of a {lang} novel, the "
        f"word between >>> and <<< was matched as a possible {lang} measurement unit "
        f"({_unit_examples(uset)}, etc.). Is it being used as a measurement unit?"
    )


JEV_UNIT_CRITERIA = {
    "unit": (
        "Used as a measurement of distance, length, weight, area, volume or time, "
        "usually after a number or quantity: 'thirty li', 'three jin', 'a hundred "
        "zhang', 'two liang of silver', 'ten mu of land', 'a thirty-ping flat', "
        "'a ke later'. Exaggerated quantities still count: 'a force of a thousand "
        "jun', 'ten thousand zhang tall'."
    ),
    "not_unit": (
        "Not a measurement: a surname or given name ('Elder Jin', 'Old Zhang', "
        "'Zhang Wei'), part of a place name ('Li Village', 'Nine Li Town'), a number "
        "beside a name ('Chapter 3 Zhang Wei'), part of an English word, a different "
        "meaning (jin as gold or money, chi as qi/tai chi, liang as bright, ping as "
        "a sound or network ping, dan as a pill or elixir, kin as relatives), or "
        "figurative use where a metric conversion would be nonsensical."
    ),
}
# The state cap is 32k tokens including the longest question; sentences are
# capped at 200 chars, so this is about request size, not that limit.
JEV_UNIT_BATCH = 40
DEFAULT_JEV_UNIT_CONFIDENCE = 0.9


def _jev_unit_filter_enabled() -> bool:
    """``jev_unit_filter`` on and a TypeSafe key configured."""
    import jev_client
    import settings_store
    if not jev_client.is_configured():
        return False
    return bool(settings_store.get("jev_unit_filter", True))


def _jev_unit_threshold() -> float:
    import settings_store
    try:
        t = float(settings_store.get("jev_unit_confidence", DEFAULT_JEV_UNIT_CONFIDENCE))
    except (TypeError, ValueError):
        return DEFAULT_JEV_UNIT_CONFIDENCE
    return t if 0.0 < t <= 1.0 else DEFAULT_JEV_UNIT_CONFIDENCE


def _jev_false_positives(context: dict, uset: _UnitSet) -> Tuple[Set[int], Set[int]]:
    """Classify each highlighted match with Jev.

    Returns ``(false_positive_ids, unsure_ids)``: a confident ``not_unit`` is a
    false positive, a confident ``unit`` is converted, and anything below the
    threshold (or missing from the answer) is unsure. Raises ``JevError``.
    """
    import jev_client
    threshold = _jev_unit_threshold()
    false_positives: Set[int] = set()
    unsure: Set[int] = set()
    ids = list(context)
    instructions = _jev_instructions(uset)
    lang = LANGUAGE_NAMES.get(uset.lang, uset.lang)
    for i in range(0, len(ids), JEV_UNIT_BATCH):
        batch = ids[i:i + JEV_UNIT_BATCH]
        questions = {
            f"m{mid}": {
                "type": "choice",
                "instructions": f"{instructions}\n\nSentence: {context[mid]}",
                "criteria": JEV_UNIT_CRITERIA,
            }
            for mid in batch
        }
        answers = jev_client.system_one({"task": f"{lang} measurement unit filter"}, questions)
        for mid in batch:
            ans = answers.get(f"m{mid}")
            try:
                confidence = float(ans.get("confidence"))
                label = ans.get("choice")
            except (AttributeError, TypeError, ValueError):
                unsure.add(int(mid))
                continue
            if label not in JEV_UNIT_CRITERIA or confidence < threshold:
                unsure.add(int(mid))
            elif label == "not_unit":
                false_positives.add(int(mid))
    logger.info(f"Jev unit filter: {len(context)} match(es), "
                f"{len(false_positives)} false positive(s), {len(unsure)} unsure")
    return false_positives, unsure


# ── LLM false-positive filter ───────────────────────────────────────

def _filter_false_positives(context: dict, cleaning_model: str,
                            uset: Optional[_UnitSet] = None) -> Set[int]:
    """Call cleaning model to identify false positive unit matches.

    Args:
        context: ``{str(match_id): highlighted sentence}`` (``_match_contexts``).
        cleaning_model: Model spec string (e.g. "gemini:gemini-2.0-flash").
        uset: The book's unit set. The prompt file is written for Chinese; for
            any language, a closing paragraph names it and its units.

    Returns:
        Set of match IDs that should NOT be converted (false positives).
    """
    if not context:
        return set()
    try:
        from providers import create_provider
        from config import TranslationConfig
        config = TranslationConfig()

        system_prompt = _load_cleaning_prompt()
        if uset is not None:
            lang = LANGUAGE_NAMES.get(uset.lang, uset.lang)
            system_prompt += (
                f"\n\nSOURCE LANGUAGE: this novel was translated from {lang}. The "
                f"highlighted words are candidates for its units: {_unit_examples(uset, 40)}."
            )
        user_prompt = json.dumps(context, ensure_ascii=False, indent=2)

        provider_name, model_name = config.parse_model_spec(cleaning_model)
        provider = create_provider(provider_name)

        logger.info(f"Filtering {len(context)} unit match(es) with {model_name}...")

        response = provider.chat_completion(
            model=model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.0,
        )

        raw = provider.get_response_content(response).strip()

        # Strip markdown fences if present
        if raw.startswith("```"):
            raw_lines = raw.split("\n")
            raw = "\n".join(raw_lines[1:-1]) if len(raw_lines) > 2 else raw
            if raw.startswith("json"):
                raw = raw[4:].strip()

        false_positive_list = json.loads(raw)

        if not isinstance(false_positive_list, list):
            logger.warning("Cleaning model returned non-list, ignoring")
            return set()

        result = {int(x) for x in false_positive_list if str(x) in context}
        if result:
            logger.info(f"Cleaning model flagged {len(result)} false positive(s)")
        return result

    except Exception as e:
        logger.error(f"Unit cleaning model failed, converting all matches: {e}")
        return set()


def _find_false_positives(lines: List[str], matches: list,
                          cleaning_model: Optional[str], use_jev: bool,
                          uset: _UnitSet) -> Set[int]:
    """Jev first (if on), then the LLM for whatever Jev left undecided.

    With neither filter able to decide a match, it is converted -- the same
    default as running with no filter at all.
    """
    context = _match_contexts(lines, matches)
    pending = context
    false_positives: Set[int] = set()
    if use_jev:
        try:
            fps, unsure = _jev_false_positives(context, uset)
            false_positives |= fps
            pending = {mid: context[mid] for mid in context if int(mid) in unsure}
        except Exception as e:  # JevError, or anything unexpected: fall back
            logger.warning(f"Jev unit filter failed, "
                           f"{'falling back to ' + cleaning_model if cleaning_model else 'converting all matches'}: {e}")
            pending = context
    if pending and cleaning_model:
        false_positives |= _filter_false_positives(pending, cleaning_model, uset=uset)
    return false_positives


_ARTICLE_COUNTS = ("a", "an", "a single", "single", "another")


def _strict_rejects(match: re.Match, uset: _UnitSet) -> bool:
    """True when a strict unit's match is the English word, not the unit.

    "two ping-pong balls", "a ping sounded", "pings": a strict unit needs a
    real count, no plural and no hyphenated continuation (see ``Unit.strict``).
    """
    gd = match.groupdict()
    info = uset.lookup(gd["unit"])
    if info is None or not info.strict:
        return False
    if match.end() != match.end("unit"):            # took the plural "s"
        return True
    if match.string[match.end():match.end() + 1] == "-":
        return True
    if gd.get("vague") or gd.get("fracunit") or gd.get("numfill"):
        return True
    count = re.sub(r"[\s\-]+", " ", (gd.get("num") or "").strip().lower())
    return count in _ARTICLE_COUNTS


def convert_units(lines: List[str], cleaning_model: Optional[str] = None,
                  use_jev: Optional[bool] = None,
                  source_language: Optional[str] = DEFAULT_LANGUAGE) -> List[str]:
    """Convert East Asian units in translated text to include metric equivalents.

    Args:
        lines: List of translated text lines.
        cleaning_model: Optional model spec (provider:model) for LLM false
            positive filtering. With Jev on, it only sees the matches Jev was
            unsure of (or all of them, if the Jev call failed).
        use_jev: Filter with Jev first. None follows the ``jev_unit_filter``
            setting; either way Jev needs ``TYPESAFE_KEY``. With no cleaning
            model and Jev off, all regex matches are converted.
        source_language: The book's source language. Picks the unit table;
            None/empty means zh, and a language units.json has no table for
            (ru, en, …) returns the lines unchanged.

    Returns:
        Lines with metric annotations appended where units were found.
    """
    uset = _unit_set(source_language)
    if uset is None:
        return list(lines)

    # Pass 1: Collect all matches across all lines. Precedence (highest first):
    #   1. point-in-time expressions (_POINT_RE) — "third ke of the wu hour"
    #   2. quantity-bearing spans (uset.pattern)  — "two shichen"
    #   3. bare units (uset.bare_pattern)         — "the appointed shichen"
    # Lower-precedence matches are dropped where they overlap a higher one, so a
    # phrase is handled exactly once.
    all_matches = []  # List of (line_idx, match_obj, match_id)
    match_kind: dict = {}  # match_id -> "point" | "main" | "bare"
    for line_idx, line in enumerate(lines):
        point_spans = []
        for match in (_POINT_RE.finditer(line) if uset.chinese_time else ()):
            # Skip no-ops (bare "noon", already-canonical hours) so they neither
            # clutter the plan nor needlessly suppress overlapping span matches.
            if _convert_point_match(match) == match.group(0):
                continue
            point_spans.append((match.start(), match.end()))
            match_kind[len(all_matches)] = "point"
            all_matches.append((line_idx, match, len(all_matches)))

        main_spans = []
        for match in uset.pattern.finditer(line):
            if any(s < match.end() and match.start() < e for s, e in point_spans):
                continue  # part of a point-in-time expression; skip
            if _strict_rejects(match, uset):
                continue  # the English word ("a ping", "ping-pong"), not the unit
            main_spans.append((match.start(), match.end()))
            match_kind[len(all_matches)] = "main"
            all_matches.append((line_idx, match, len(all_matches)))
        for match in (uset.bare_pattern.finditer(line) if uset.bare_pattern else ()):
            if any(s < match.end() and match.start() < e
                   for s, e in point_spans + main_spans):
                continue  # overlaps a higher-precedence match; skip
            match_kind[len(all_matches)] = "bare"
            all_matches.append((line_idx, match, len(all_matches)))
        for match in (_KE_QUALIFIED_RE.finditer(line) if uset.chinese_time else ()):
            if any(s < match.end() and match.start() < e
                   for s, e in point_spans + main_spans):
                continue
            match_kind[len(all_matches)] = "keq"
            all_matches.append((line_idx, match, len(all_matches)))

    if not all_matches:
        return list(lines)

    # Pass 2: Optionally filter false positives via Jev and/or the cleaning model.
    # Point-in-time matches are deterministic and don't fit the unit classifier's
    # prompt, so they bypass it (they're always applied).
    if use_jev is None:
        use_jev = _jev_unit_filter_enabled()
    elif use_jev:
        import jev_client
        use_jev = jev_client.is_configured()
    false_positive_ids: Set[int] = set()
    if cleaning_model or use_jev:
        # Lowercase time units skip the classifier too. "shichen", "ke" and
        # "double-hour" have no English or surname reading in lowercase (the
        # surname Ke is capitalised and caught by _convert_match's name check),
        # so there is nothing for the model to filter — yet it vetoed them,
        # leaving ~30 durations as pinyin across book 106 even after
        # unit_cleaning_prompt.txt was told 'ke' is genuine.
        def _needs_classifier(m):
            if match_kind.get(m[2]) in ("point", "keq"):
                return False
            unit_text = m[1].groupdict().get("unit") or ""
            info = uset.lookup(unit_text)
            if unit_text.islower() and info and info.type == "time":
                return False
            return True
        cleanable = [m for m in all_matches if _needs_classifier(m)]
        if cleanable:
            false_positive_ids = _find_false_positives(
                lines, cleanable, cleaning_model, use_jev, uset
            )

    # Pass 3: Apply conversions, skipping false positives
    # Group by line index, process in reverse offset order to preserve positions
    matches_by_line: dict = {}
    for line_idx, match, match_id in all_matches:
        matches_by_line.setdefault(line_idx, []).append((match, match_id))

    result = list(lines)
    for line_idx, match_list in matches_by_line.items():
        line = result[line_idx]
        for match, match_id in sorted(match_list, key=lambda x: x[0].start(), reverse=True):
            if match_id in false_positive_ids:
                continue
            kind = match_kind.get(match_id)
            prefix = line[:match.start()]
            if kind == "point":
                replacement = _convert_point_match(match)
                # Only text before this match changes, and matches are applied
                # right to left, so earlier offsets stay valid.
                prefix, replacement = _drop_indefinite_before_point(prefix, replacement)
            elif kind == "bare":
                replacement = _convert_bare_match(match, uset)
            elif kind == "keq":
                replacement = match.group("q") + "quarter hour"
            else:
                replacement = _convert_match(match, uset)
            line = prefix + replacement + line[match.end():]
        result[line_idx] = line

    return result
