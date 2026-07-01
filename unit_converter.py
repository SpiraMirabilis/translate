"""
Post-translation Chinese unit → metric conversion.

Appends metric equivalents in parentheses, e.g. "1000 zhang (3.3 km)".
Uses regex to find matches, with optional AI-powered false positive filtering.
"""

import json
import logging
import os
import re
from typing import List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# ── Unit table ──────────────────────────────────────────────────────
# Loaded from units.json: each entry has value, unit, type

def _load_units() -> dict:
    """Load unit definitions from units.json."""
    json_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "units.json")
    with open(json_path, "r") as f:
        raw = json.load(f)
    return {
        name: (entry["value"], entry["unit"], entry["type"],
               entry.get("action", "annotate"), entry.get("numeral", "arabic"))
        for name, entry in raw.items()
    }

UNITS = _load_units()

# ── Smart scaling ───────────────────────────────────────────────────
# Each entry: (threshold, target_unit, factor). For SCALE_UP, target = value / factor
# when value >= threshold. For SCALE_DOWN, target = value * factor when value < threshold.
SCALE_UP = {
    "m":      [(1000.0, "km",   1000.0)],
    "minute": [(60.0,   "hour", 60.0)],
}

SCALE_DOWN = {
    "m":    [(1.0, "cm", 100.0)],
    "kg":   [(1.0, "g",  1000.0)],
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

    for word in words:
        if word in _ONES:
            current += _ONES[word]
        elif word in _TENS:
            current += _TENS[word]
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

_unit_names = "|".join(
    _escape_unit_name(name)
    for name in sorted(UNITS.keys(), key=len, reverse=True)
)

# Number words that can appear before a unit
_number_words = (
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

# Main pattern. The quantity prefix is one of three branches:
#   1. a vague quantifier ("several", "a few")
#   2. a number / word-number, optionally with a fractional prefix ("a quarter of a ke")
#   3. a hyphenated/bare fraction directly on the unit ("a quarter-shichen")
_PATTERN = re.compile(
    r"(?<!['\w])"                       # not preceded by word char or apostrophe
    r"(?:"
        r"(?P<vague>" + _vague_quantifier + r")[\s\-]+"               # branch 1
        r"|"
        r"(?:(?P<frac>" + _fraction_phrase + r")\s+of\s+)?"          # branch 2
        r"(?P<num>" + _numeric + r"|a\s+single|single|another|" + _number_words + r"|a\s+full|an\s+full|full|a|an)[\s\-]+"
        r"|"
        r"(?P<fracunit>" + _fraction_phrase + r")[\s\-]+"            # branch 3
    r")"
    r"(?:(?P<more>more|whole|full)[\s\-]+)?"  # optional filler ("two more/whole/full shichen")
    r"(?P<unit>" + _unit_names + r")"   # unit name
    r"s?"                               # optional plural
    r"(?!\s*\()"                        # negative lookahead: not already annotated
    r"(?!['\w])",                       # not followed by word char or apostrophe
    re.IGNORECASE
)

# Bare unit pattern: a time unit word with no quantity at all, used as a point
# in time ("at the appointed shichen"). Only hour-based replace units qualify —
# for those, the bare word maps to the English time word ("hour").
_BARE_UNIT_NAMES = [
    name for name, (value, base_unit, _type, action, numeral) in UNITS.items()
    if action == "replace" and base_unit == "hour"
]
_bare_unit_alt = "|".join(
    _escape_unit_name(name)
    for name in sorted(_BARE_UNIT_NAMES, key=len, reverse=True)
)
_BARE_PATTERN = re.compile(
    r"(?<!['\w])"
    r"(?P<unit>" + _bare_unit_alt + r")"
    r"s?"
    r"(?!\s*\()"
    r"(?!['\w])",
    re.IGNORECASE
)


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
        r"|(?P<pinj>" + _BRANCH_JOINED_ALT + r")\b"
        r"|(?:the\s+)?(?P<ani>" + _BRANCH_ANI_ALT + r"|noon|midnight)[\s\-]hour"
        r"|(?:the\s+)?hour\s+of\s+(?:the\s+)?(?P<ani2>" + _BRANCH_ANI_ALT + r")\b"
        r"|(?P<sp>noon|midnight)\b"
        # Bare branch names ("third quarter of Zi", "first ke of the Snake"):
        # only resolved when a position prefix precedes them (see resolver), since
        # bare "Zi"/"Snake" are far too collision-prone on their own.
        r"|(?:the\s+)?(?P<pinbare>" + _BRANCH_PIN_NOYOU + r")\b"
        r"|(?:the\s+)?(?P<anibare>" + _BRANCH_ANI_ALT + r")\b"
    r")"
)

_POINT_RE = re.compile(
    r"(?<![\w'])"
    + r"(?:" + _POINT_PREFIX + r")?"
    + _POINT_HOUR
    + r"(?![\w'])",
    re.IGNORECASE,
)


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
    if gd.get("pin"):
        idx = _BRANCH_PINYIN[gd["pin"].lower()]
    elif gd.get("pin2"):
        idx = _BRANCH_PINYIN[gd["pin2"].lower()]
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

    # Mirror the original phrase's leading casing (sentence-initial -> capital).
    return _match_case(full, out)


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


def _lookup_unit(matched_text: str) -> tuple:
    """Look up a unit by its matched text, normalizing spaces/hyphens."""
    # Normalize the matched text to try different key forms
    normalized = matched_text.lower()
    # Try exact match first
    if normalized in UNITS:
        return UNITS[normalized]
    # Try with hyphens replaced by spaces
    alt = normalized.replace("-", " ")
    if alt in UNITS:
        return UNITS[alt]
    # Try with spaces replaced by hyphens
    alt = normalized.replace(" ", "-")
    if alt in UNITS:
        return UNITS[alt]
    return None


def _convert_match(match: re.Match) -> str:
    """Replace callback for unit matches (from _PATTERN)."""
    full = match.group(0)
    gd = match.groupdict()
    fraction_str = gd.get("frac")
    fracunit_str = gd.get("fracunit")
    vague_str = gd.get("vague")
    num_str = (gd.get("num") or "").strip()
    more_str = gd.get("more")
    unit_text = gd["unit"]

    unit_info = _lookup_unit(unit_text)
    if unit_info is None:
        return full
    base_value, base_unit, _, action, numeral = unit_info

    # ── Vague quantifier path ("several shichen" -> "several hours") ──
    # Only for hour-based replace units; anything else is left untouched so we
    # don't misstate magnitude ("several jiazi", "several ke") or try to annotate
    # an uncountable phrase ("several zhang").
    if vague_str:
        if action != "replace" or base_unit != "hour":
            return full
        vague_norm = re.sub(r"\s+", " ", vague_str.strip())
        return _match_case(full, f"{vague_norm} {base_unit}s")

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

        # Handle "a"/"an"/"a full"/"an full"/"a single"/"single" as 1
        normalized = re.sub(r"\s+", " ", num_str.strip().lower())
        if normalized in ("a", "an", "a full", "an full", "full", "a single", "single", "another"):
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
        # Special case: "an hour" reads better than "one hour" — but only when
        # there's no filler word to preserve ("one more hour", not "an more hour").
        if (numeral == "english" and scaled == 1.0 and final_unit == "hour"
                and not is_another and not more_str):
            output = "about an hour" if was_rounded else "an hour"
        else:
            # Pluralize the unit if value != 1
            unit_label = final_unit
            if scaled != 1.0 and not unit_label.endswith("s"):
                unit_label += "s"
            # Keep the filler word between the number and unit, echoing whatever
            # matched: "four more hours", "four whole hours", "two full hours".
            filler = more_str.lower() if more_str else None
            body = f"{formatted} {filler} {unit_label}" if filler else f"{formatted} {unit_label}"
            if is_another:
                body = f"another {body}"
            output = f"about {body}" if was_rounded else body
        # Mirror the original phrase's casing onto the replacement so a
        # sentence-initial "Half a shichen" yields "An hour", not "an hour".
        return _match_case(full, output)
    else:
        # Default: annotate — leaves the original phrase untouched
        return f"{full} ({formatted} {final_unit})"


def _convert_bare_match(match: re.Match) -> str:
    """Replace callback for bare unit matches (from _BARE_PATTERN).

    A bare time unit with no quantity is a point in time ("the appointed
    shichen") rather than a span, so it maps to the English time word with no
    number: "shichen" -> "hour", "shichens" -> "hours".
    """
    full = match.group(0)
    unit_text = match.group("unit")

    unit_info = _lookup_unit(unit_text)
    if unit_info is None:
        return full
    _value, base_unit, _type, action, _numeral = unit_info
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

    context = line[sent_start:sent_end].strip()
    ctx_offset = sent_start

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


def _filter_false_positives(lines: List[str], all_matches: list,
                            cleaning_model: str) -> Set[int]:
    """Call cleaning model to identify false positive unit matches.

    Args:
        lines: Original text lines.
        all_matches: List of (line_idx, match_obj, match_id) tuples.
        cleaning_model: Model spec string (e.g. "gemini:gemini-2.0-flash").

    Returns:
        Set of match IDs that should NOT be converted (false positives).
    """
    try:
        from providers import create_provider
        from config import TranslationConfig
        config = TranslationConfig()

        # Build context dict with highlighted matches
        context = {}
        for line_idx, match, match_id in all_matches:
            line = lines[line_idx]
            sentence, ctx_offset = _extract_sentence_context(
                line, match.start(), match.end()
            )
            # Calculate match position within the context string
            rel_start = match.start() - ctx_offset
            rel_end = match.end() - ctx_offset
            # Highlight the match
            highlighted = (sentence[:rel_start] + ">>>" +
                          sentence[rel_start:rel_end] + "<<<" +
                          sentence[rel_end:])
            context[str(match_id)] = highlighted

        if not context:
            return set()

        system_prompt = _load_cleaning_prompt()
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


def convert_units(lines: List[str], cleaning_model: Optional[str] = None) -> List[str]:
    """Convert Chinese units in translated text to include metric equivalents.

    Args:
        lines: List of translated text lines.
        cleaning_model: Optional model spec (provider:model) for AI-powered
            false positive filtering. If None, all regex matches are converted.

    Returns:
        Lines with metric annotations appended where units were found.
    """
    # Pass 1: Collect all matches across all lines. Precedence (highest first):
    #   1. point-in-time expressions (_POINT_RE) — "third ke of the wu hour"
    #   2. quantity-bearing spans (_PATTERN)      — "two shichen"
    #   3. bare units (_BARE_PATTERN)             — "the appointed shichen"
    # Lower-precedence matches are dropped where they overlap a higher one, so a
    # phrase is handled exactly once.
    all_matches = []  # List of (line_idx, match_obj, match_id)
    match_kind: dict = {}  # match_id -> "point" | "main" | "bare"
    for line_idx, line in enumerate(lines):
        point_spans = []
        for match in _POINT_RE.finditer(line):
            # Skip no-ops (bare "noon", already-canonical hours) so they neither
            # clutter the plan nor needlessly suppress overlapping span matches.
            if _convert_point_match(match) == match.group(0):
                continue
            point_spans.append((match.start(), match.end()))
            match_kind[len(all_matches)] = "point"
            all_matches.append((line_idx, match, len(all_matches)))

        main_spans = []
        for match in _PATTERN.finditer(line):
            if any(s < match.end() and match.start() < e for s, e in point_spans):
                continue  # part of a point-in-time expression; skip
            main_spans.append((match.start(), match.end()))
            match_kind[len(all_matches)] = "main"
            all_matches.append((line_idx, match, len(all_matches)))
        for match in _BARE_PATTERN.finditer(line):
            if any(s < match.end() and match.start() < e
                   for s, e in point_spans + main_spans):
                continue  # overlaps a higher-precedence match; skip
            match_kind[len(all_matches)] = "bare"
            all_matches.append((line_idx, match, len(all_matches)))

    if not all_matches:
        return list(lines)

    # Pass 2: Optionally filter false positives via cleaning model. Point-in-time
    # matches are deterministic and don't fit the unit classifier's prompt, so
    # they bypass it (they're always applied).
    false_positive_ids: Set[int] = set()
    if cleaning_model:
        cleanable = [m for m in all_matches if match_kind.get(m[2]) != "point"]
        if cleanable:
            false_positive_ids = _filter_false_positives(
                lines, cleanable, cleaning_model
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
            if kind == "point":
                replacement = _convert_point_match(match)
            elif kind == "bare":
                replacement = _convert_bare_match(match)
            else:
                replacement = _convert_match(match)
            line = line[:match.start()] + replacement + line[match.end():]
        result[line_idx] = line

    return result
