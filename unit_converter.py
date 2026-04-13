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
SCALE_UP = {
    "m":  [(1000.0, "km")],
    "kg": [],               # kg is already a good unit
    "ha": [],
}

SCALE_DOWN = {
    "m":  [(1.0, "cm")],    # below 1 m → cm
    "kg": [(1.0, "g")],     # below 1 kg → g
    "ha": [],
}


def _scale(value: float, base_unit: str) -> tuple:
    """Scale value to the most readable unit."""
    # Scale down: m → cm, kg → g
    for threshold, small_unit in SCALE_DOWN.get(base_unit, []):
        if value < threshold:
            if small_unit == "cm":
                return value * 100, "cm"
            elif small_unit == "g":
                return value * 1000, "g"

    # Scale up: m → km
    for threshold, big_unit in SCALE_UP.get(base_unit, []):
        if value >= threshold:
            if big_unit == "km":
                return value / 1000, "km"

    return value, base_unit


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

    # Handle .5 (halves) — common in time conversions
    if value % 1 == 0.5:
        whole = int(value)
        if whole == 0:
            return "half"
        return _int_to_words(whole) + " and a half"

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
    if words == ["a"] or (len(words) >= 2 and words[1] in ("few", "couple")):
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
_numeric = r"(?:\d[\d,]*\.?\d*)"

# Vague quantifier patterns to exclude
_vague_prefix = (
    r"(?:several|a\s+few|few|many|some|numerous|dozens\s+of|"
    r"hundreds\s+of|thousands\s+of|countless|myriad|various|multiple)\s+"
)

# Main pattern
_PATTERN = re.compile(
    r"(?<!['\w])"                       # not preceded by word char or apostrophe
    r"(?!" + _vague_prefix + r")"       # negative lookahead for vague quantifiers
    r"(" + _numeric + r"|" + _number_words + r"|a\s+full|an\s+full|a|an)"  # number capture
    r"[\s\-]+"                          # separator
    r"(" + _unit_names + r")"           # unit name
    r"s?"                               # optional plural
    r"(?!\s*\()"                        # negative lookahead: not already annotated
    r"(?!['\w])",                       # not followed by word char or apostrophe
    re.IGNORECASE
)


_VAGUE_BEFORE = re.compile(
    r"(?:several|few|many|some|numerous|dozens\s+of|hundreds\s+of|"
    r"thousands\s+of|countless|myriad|various|multiple)\s+$",
    re.IGNORECASE,
)


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
    """Replace callback for unit matches."""
    full = match.group(0)
    num_str = match.group(1).strip()
    unit_text = match.group(2)

    # Check text before match for vague quantifiers
    before = match.string[:match.start()]
    if _VAGUE_BEFORE.search(before):
        return full

    # Handle "a"/"an"/"a full"/"an full" as 1
    normalized = re.sub(r"\s+", " ", num_str.strip().lower())
    if normalized in ("a", "an", "a full", "an full"):
        number = 1.0
    else:
        number = _word_to_number(num_str)

    if number is None:
        return full  # vague quantifier, skip

    unit_info = _lookup_unit(unit_text)
    if unit_info is None:
        return full

    base_value, base_unit, _, action, numeral = unit_info
    raw = number * base_value
    scaled, final_unit = _scale(raw, base_unit)

    if numeral == "english":
        formatted = _number_to_words(scaled)
    else:
        formatted = _format_number(scaled)

    if action == "replace":
        # Special case: "an hour" reads better than "one hour"
        if numeral == "english" and scaled == 1.0 and final_unit == "hour":
            return "an hour"
        # Pluralize the unit if value != 1
        unit_label = final_unit
        if scaled != 1.0 and not unit_label.endswith("s"):
            unit_label += "s"
        return f"{formatted} {unit_label}"
    else:
        # Default: annotate
        return f"{full} ({formatted} {final_unit})"


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
    # Pass 1: Collect all matches across all lines
    all_matches = []  # List of (line_idx, match_obj, match_id)
    for line_idx, line in enumerate(lines):
        for match in _PATTERN.finditer(line):
            all_matches.append((line_idx, match, len(all_matches)))

    if not all_matches:
        return list(lines)

    # Pass 2: Optionally filter false positives via cleaning model
    false_positive_ids: Set[int] = set()
    if cleaning_model:
        false_positive_ids = _filter_false_positives(
            lines, all_matches, cleaning_model
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
            replacement = _convert_match(match)
            line = line[:match.start()] + replacement + line[match.end():]
        result[line_idx] = line

    return result
