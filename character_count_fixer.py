"""
Post-translation corrector for Chinese character-count prose.

Chinese prose often describes a written name or phrase by counting its 字
(written characters), e.g. 「招牌上的"妖怪包"三个字」 -> literally "the three
characters 'Demon Wraps'". English readers count WORDS, not characters, so this
reads wrong: "Demon Wraps" is three Chinese characters but two English words.

This module finds "<number> characters|words|symbols|glyphs" mentions and asks a
capable model to (a) filter out false positives ("the four characters in this
scene" = people; 八字 = BaZi astrology) and (b) recount in English words and
rewrite, unifying the noun to "words".

The correction is entirely model-driven — there is no regex-only mode. The public
entry point `correct_character_counts(lines, model)` mirrors
`unit_converter.convert_units(lines, cleaning_model)` so it can later be wired
into the live translation pipeline (ui.py) the same way unit conversion is.
"""

import hashlib
import json
import logging
import os
import re
from typing import List, Optional

logger = logging.getLogger(__name__)

# ── Match pattern ───────────────────────────────────────────────────
# A number (word or digit) immediately followed by a "written-text" noun.
# The leading determiner ("the", "those", ...) is intentionally NOT captured —
# only the "<number> <noun>" span is replaced, so a surrounding determiner and
# its casing are preserved automatically ("Those three characters" -> the span
# "three characters" becomes "two words", yielding "Those two words").
_NUM = (
    r"(?:\d{1,3}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|"
    r"twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty)"
)
_NOUN = r"(?:characters?|words?|symbols?|glyphs?)"

_PATTERN = re.compile(
    r"(?<!['\w])"                      # not mid-word
    r"(?P<num>" + _NUM + r")"
    r"[\s\-]+"
    r"(?P<noun>" + _NOUN + r")"
    r"(?!['\w])",                      # not the prefix of a longer word
    re.IGNORECASE,
)

# Cap neighbour-paragraph context to keep token use bounded. The referenced
# phrase, when not in the matched paragraph, is almost always adjacent to the
# boundary — so keep the tail of the previous line and the head of the next.
_NEIGHBOR_CHARS = 400


def _build_context(lines: List[str], line_idx: int, match: re.Match) -> str:
    """Return the matched paragraph (span wrapped in >>> <<<) plus a bounded
    window of the neighbouring paragraphs, so the model can locate a phrase that
    was quoted just before or after the match."""
    cur = lines[line_idx]
    highlighted = (cur[:match.start()] + ">>>" +
                   cur[match.start():match.end()] + "<<<" +
                   cur[match.end():])

    parts = []
    if line_idx > 0 and lines[line_idx - 1].strip():
        parts.append(lines[line_idx - 1][-_NEIGHBOR_CHARS:])
    parts.append(highlighted)
    if line_idx + 1 < len(lines) and lines[line_idx + 1].strip():
        parts.append(lines[line_idx + 1][:_NEIGHBOR_CHARS])
    return "\n".join(parts)


def _load_correction_prompt() -> str:
    """Load the character-count correction prompt from the prompts directory."""
    prompt_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "prompts", "character_count_prompt.txt")
    with open(prompt_path, "r", encoding="utf-8") as f:
        return f.read()


def _cache_key(model: str, prompt: str, context: str) -> str:
    """Stable hash of everything that determines the model's answer for a match.

    Including the model spec and the prompt text means a prompt tweak or model
    change automatically invalidates stale cached decisions; including the full
    context means edited text (or an edited neighbour paragraph) re-queries."""
    h = hashlib.sha256()
    h.update(model.encode("utf-8"))
    h.update(b"\x00")
    h.update(prompt.encode("utf-8"))
    h.update(b"\x00")
    h.update(context.encode("utf-8"))
    return h.hexdigest()


def _get_corrections(lines: List[str], all_matches: list, model: str,
                     cache=None) -> dict:
    """Classify + rewrite the highlighted matches, using the cache when given.

    Args:
        lines: Original text lines (paragraphs).
        all_matches: List of (line_idx, match_obj, match_id) tuples.
        model: Model spec string (e.g. "claude:claude-opus-4-8").
        cache: Optional decision cache with get(key)/set(key, value)/commit().
            Cached decisions (both "keep" and replacements) skip the model.

    Returns:
        Dict of match_id -> replacement string, for matches that need fixing.
        On model failure, any decisions already resolved from cache are still
        returned (content is never corrupted).
    """
    system_prompt = _load_correction_prompt()

    corrections: dict = {}
    to_query: dict = {}    # str(match_id) -> context (cache misses only)
    key_by_id: dict = {}   # match_id -> cache_key

    for line_idx, match, match_id in all_matches:
        context = _build_context(lines, line_idx, match)
        key = _cache_key(model, system_prompt, context)
        key_by_id[match_id] = key
        cached = cache.get(key) if cache else None
        if cached is not None:
            if cached:                       # non-empty => replacement; "" => keep
                corrections[match_id] = cached
        else:
            to_query[str(match_id)] = context

    if not to_query:
        return corrections

    try:
        from providers import create_provider
        from config import TranslationConfig

        provider_name, model_name = TranslationConfig().parse_model_spec(model)
        provider = create_provider(provider_name)
        user_prompt = json.dumps(to_query, ensure_ascii=False, indent=2)

        logger.info(f"Querying {len(to_query)} new character-count match(es) with {model_name}...")

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

        returned = json.loads(raw)
        if not isinstance(returned, dict):
            logger.warning("Character-count model returned non-object, ignoring")
            returned = {}

    except Exception as e:
        logger.error(f"Character-count model failed, using {len(corrections)} cached decision(s) only: {e}")
        return corrections

    # Resolve + cache a decision for every queried match (fixes AND keeps, so
    # keeps aren't re-queried next run).
    new_fixes = 0
    for sid in to_query:
        match_id = int(sid)
        repl = returned.get(sid)
        decision = repl.strip() if (isinstance(repl, str) and repl.strip()) else ""
        if decision:
            corrections[match_id] = decision
            new_fixes += 1
        if cache:
            cache.set(key_by_id[match_id], decision)
    if cache:
        cache.commit()

    logger.info(f"{new_fixes} new fix(es); {len(corrections)} correction(s) to apply")
    return corrections


def correct_character_counts(lines: List[str], model: Optional[str] = None,
                             cache=None) -> List[str]:
    """Correct Chinese character-count prose in translated text.

    Args:
        lines: List of translated text lines (paragraphs).
        model: Model spec (provider:model) used to classify and rewrite matches.
            Required — without it the function is a no-op (returns lines as-is),
            since the correction is entirely model-driven.
        cache: Optional decision cache (get/set/commit). When provided, matches
            whose (model + prompt + context) was decided on a previous run skip
            the model entirely. Pass None for an uncached, pure run.

    Returns:
        Lines with character-count phrases rewritten in English-word terms.
    """
    if not model:
        logger.warning("correct_character_counts called without a model; no changes made")
        return list(lines)

    # Pass 1: Collect all matches across all lines
    all_matches = []  # List of (line_idx, match_obj, match_id)
    for line_idx, line in enumerate(lines):
        for match in _PATTERN.finditer(line):
            all_matches.append((line_idx, match, len(all_matches)))

    if not all_matches:
        return list(lines)

    # Pass 2: Resolve which matches to rewrite (cache first, then model)
    corrections = _get_corrections(lines, all_matches, model, cache=cache)
    if not corrections:
        return list(lines)

    # Pass 3: Apply corrections, processing each line in reverse offset order
    matches_by_line: dict = {}
    for line_idx, match, match_id in all_matches:
        matches_by_line.setdefault(line_idx, []).append((match, match_id))

    result = list(lines)
    for line_idx, match_list in matches_by_line.items():
        line = result[line_idx]
        for match, match_id in sorted(match_list, key=lambda x: x[0].start(), reverse=True):
            if match_id not in corrections:
                continue
            replacement = corrections[match_id]
            line = line[:match.start()] + replacement + line[match.end():]
        result[line_idx] = line

    return result
