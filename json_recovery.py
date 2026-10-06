"""Recovery of a model response that is not valid JSON.

A survey of ``api_calls`` (2026-08-01 to 2026-09-22, 11,888 chunk responses)
found 116 that would not parse -- 1.0% of chunks -- in two populations that
need opposite treatment:

* **Truncated streams** (44): brackets or a string still open at end of text.
  The median one was 63% the length of the retry that succeeded. Closing the
  brackets would produce valid JSON that silently saves two thirds of a
  chapter, so a truncated response is *never* repaired; the caller retries.
* **Complete but broken** (72): mostly an unescaped ``"`` inside a string
  (46) or a stray ``"`` before ``]`` (10). ``json_repair`` turns 52 of these
  into a well-shaped dict whose text is character-identical to the raw
  response. It also produced 7 lossy repairs (20-30% of the text gone) and 13
  of the wrong shape, which is why a repair is accepted only when it passes
  the fidelity gate in :func:`try_repair`.

The functions here are pure: no I/O, no logging, no engine imports. The
library is loaded lazily and its absence is a reason string, never an error,
so a broken vendor tree cannot take the app down at import time.
"""
import json
import re
from typing import Any, Dict, Optional, Tuple

TRUNCATED = "truncated"
COMPLETE = "complete"

# Word characters plus the CJK Unified Ideographs (incl. Extension A) and
# Hangul syllable ranges: what a repair must preserve exactly. Punctuation and
# whitespace are what a repair legitimately moves around.
_NORM = re.compile(r'[^\w㐀-鿿가-힯]')


def _load_json_repair():
    """The vendored copy first, then a system install, else None."""
    try:
        from vendor import json_repair
        return json_repair
    except ImportError:
        pass
    try:
        import json_repair
        return json_repair
    except ImportError:
        return None


def strip_fences(text: str) -> str:
    """Drop a ```json ... ``` wrapper; mirrors ModelProvider._strip_markdown_fences."""
    text = text.strip()
    if text.startswith("```"):
        text = text[text.index("\n") + 1:] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
    return text.strip()


def classify(text: str) -> str:
    """``truncated`` if a bracket or string is still open at end of text, else ``complete``.

    The scan honours string literals and backslash escapes, so a bracket
    inside prose does not count and an escaped quote does not close a string.
    """
    depth = 0
    in_str = esc = False
    for ch in strip_fences(text):
        if in_str:
            if esc:
                esc = False
            elif ch == '\\':
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in '{[':
            depth += 1
        elif ch in '}]':
            depth -= 1
    return TRUNCATED if (depth > 0 or in_str) else COMPLETE


def normalize(text: str) -> str:
    """The text with everything but word/CJK/Hangul characters removed."""
    return _NORM.sub('', text)


def describe_error(text: str) -> str:
    """What json.loads objects to, for a log line."""
    try:
        json.loads(strip_fences(text))
        return "parses"
    except json.JSONDecodeError as e:
        return f"{e.msg} at {e.pos}"


def try_repair(text: str, required_list_key: str = "content") -> Tuple[Optional[Dict[str, Any]], str]:
    """Repair *text* with json_repair and accept the result only if it is lossless.

    Returns ``(dict, "faithful")`` on success, else ``(None, reason)`` where
    reason is one of ``library_missing``, ``repair_raised: ...``,
    ``wrong_shape``, ``lossy (0.73)`` -- the number being the share of
    text that survived. Never raises.
    """
    lib = _load_json_repair()
    if lib is None:
        return None, "library_missing"
    stripped = strip_fences(text)
    try:
        repaired = lib.loads(stripped)
    except Exception as e:  # the library is third-party; nothing it does may propagate
        return None, f"repair_raised: {e!r}"
    if not isinstance(repaired, dict):
        return None, "wrong_shape"
    lines = repaired.get(required_list_key)
    if not isinstance(lines, list) or not all(isinstance(x, str) for x in lines):
        return None, "wrong_shape"
    if not all(isinstance(k, str) and _KEY.fullmatch(k) for k in repaired):
        return None, "wrong_shape"
    if any(line and (_RESIDUE.fullmatch(line) or _KEY_FRAGMENT.search(line)) for line in lines):
        return None, "structure (residue in a line)"
    expected = _expected_items(stripped, required_list_key)
    if expected is not None and expected != len(lines):
        return None, f"structure ({len(lines)} lines, raw has {expected})"
    try:
        raw = normalize(stripped)
        got = normalize(json.dumps(repaired, ensure_ascii=False))
    except Exception as e:
        return None, f"repair_raised: {e!r}"
    if raw != got:
        return None, f"lossy ({len(got) / max(1, len(raw)):.2f})"
    return repaired, "faithful"


# -- structural gates ---------------------------------------------------------
# The text gate above cannot see a line boundary move: json_repair answers a
# stray quote by turning `],"entities":{...` into two more "lines", and an
# unescaped quote around a comma by splitting the line into pieces. Every
# character survives, so the text compares equal; the structure is wrong.

_KEY = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')
_RESIDUE = re.compile(r'[\s\[\]{},:"]+')          # a "line" made only of JSON punctuation
_KEY_FRAGMENT = re.compile(r'"[a-z_]+"\s*:')       # a `"key":` swallowed into prose
_SEPARATOR = re.compile(r'"\s*,\s*"')              # between two array items


def _expected_items(raw: str, key: str) -> Optional[int]:
    """How many items the raw text's *key* array has, judged by its separators.

    Items are string literals separated by `","` (with any whitespace). The
    region runs from the array's opening bracket to the `]` that precedes the
    next top-level key, or to the last `]` in the text. None when the array
    cannot be located.
    """
    m = re.search(r'"' + re.escape(key) + r'"\s*:\s*\[', raw)
    if not m:
        return None
    start = m.end()
    nxt = re.compile(r'\]\s*,?\s*"[A-Za-z_][A-Za-z0-9_]*"\s*:').search(raw, start)
    end = nxt.start() if nxt else raw.rfind(']', start)
    if end < start:
        return None
    region = raw[start:end]
    if not region.strip():
        return 0
    return len(_SEPARATOR.findall(region)) + 1
