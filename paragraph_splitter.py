"""Split oversized source paragraphs into several readable paragraphs.

A chapter's source is stored as a list of "lines", where each element is one
source paragraph. Some authors (notably the Russian raws) write enormous
single-paragraph walls — interior monologues and narration that run for
thousands of characters. This module breaks those walls at sentence boundaries
so the translated chapter reads with normal paragraphing.

Design notes
------------
* Only elements longer than ``min_split`` are touched. ``⟦IMG:id⟧`` markers,
  blank separators, and already-reasonable lines pass through unchanged, so the
  transform is idempotent.
* ``split_sentences`` preserves trailing whitespace, so ``''.join(sentences)``
  reconstructs the input exactly — no character is ever added, dropped, or
  reworded.
* Dialogue handling is tuned for Russian. A leading em/en-dash followed by an
  *uppercase* letter is a new speech turn (break before it); a dash followed by
  a *lowercase* word is an attribution ("— сказал он") and stays attached to the
  speech it follows.
* The optional model pass (``smart_fn``) only ever chooses break points among
  pre-segmented sentences — it never sees or returns prose — so it cannot mutate
  the text either.
"""

import re

from illustrations import parse_marker

# --- Sentence segmentation -------------------------------------------------

# Tokens that carry a period but do not end a sentence (RU + a few EN).
_ABBREV = {
    "т.д", "т.е", "т.п", "т.к", "т.н", "и.о", "др", "пр", "см", "ср", "стр",
    "рис", "табл", "г", "гг", "в", "вв", "н.э", "ул", "пер", "просп", "д",
    "корп", "кв", "руб", "коп", "млн", "млрд", "тыс", "экз", "проф", "акад",
    "доц", "им", "св", "ст", "оз", "обл", "респ", "mr", "mrs", "ms", "dr",
    "prof", "st", "vs", "etc", "e.g", "i.e", "no",
}

# Something that can legitimately begin a new sentence.
_OPENER = r'[«»"“”\'(\[0-9A-ZА-ЯЁ]'

# A boundary: terminal punctuation (+ optional closing quote/bracket), then
# whitespace, then either a normal opener or a dialogue dash that introduces an
# uppercase turn (NOT a lowercase attribution like "— сказал").
_BOUNDARY = re.compile(
    r'[.!?…]+["»”’\')\]]?\s+'
    r'(?=' + _OPENER + r'|[—–]\s+' + _OPENER + r')'
)
_LASTWORD = re.compile(r'(\S+)$')

# A sentence that opens a new dialogue turn (dash + uppercase) or a quoted line.
_TURN = re.compile(r'^\s*(?:[—–]\s+[«"“]?[0-9A-ZА-ЯЁ]|[«"“][0-9A-ZА-ЯЁ])')


def split_sentences(text):
    """Split prose into sentences, preserving exact characters.

    ``''.join(split_sentences(text)) == text``. Guards Russian/English
    abbreviations and single-letter initials so "А. С. Пушкин" or "и т.д." do
    not produce spurious boundaries.
    """
    cuts = [0]
    for m in _BOUNDARY.finditer(text):
        pre = text[:m.start()]
        lw = _LASTWORD.search(pre)
        bare = lw.group(1).lower().strip('.»"”’\'([') if lw else ''
        bare = bare.rstrip('.')
        if len(bare) <= 1:          # initial: "А." / "A."
            continue
        if bare in _ABBREV:
            continue
        cuts.append(m.end())
    cuts.append(len(text))
    cuts = sorted(set(cuts))
    return [text[a:b] for a, b in zip(cuts, cuts[1:]) if a < b]


def _is_turn(sentence):
    return bool(_TURN.match(sentence))


# --- Paragraph grouping ----------------------------------------------------


def _deterministic_paragraphs(sentences, target):
    """Group sentences: break before each dialogue turn and whenever the
    running paragraph reaches ``target`` characters (snapped to the boundary)."""
    paras, cur, cur_len = [], [], 0
    for s in sentences:
        if cur and _is_turn(s):
            paras.append("".join(cur))
            cur, cur_len = [], 0
        cur.append(s)
        cur_len += len(s)
        if cur_len >= target and not _is_turn(s):
            paras.append("".join(cur))
            cur, cur_len = [], 0
    if cur:
        paras.append("".join(cur))
    return [p.strip() for p in paras if p.strip()]


def _apply_breaks(sentences, break_after):
    """Build paragraphs from break-after sentence indices (0-based)."""
    breaks = sorted({i for i in break_after if 0 <= i < len(sentences) - 1})
    paras, start = [], 0
    for b in breaks:
        paras.append("".join(sentences[start:b + 1]))
        start = b + 1
    paras.append("".join(sentences[start:]))
    return [p.strip() for p in paras if p.strip()]


def split_line(line, target=400, min_split=500, smart_fn=None):
    """Split one source paragraph into a list of paragraphs.

    Returns ``[line]`` unchanged when it is short, marker-only, or has no
    interior boundary. ``smart_fn(sentences, target) -> list[int]`` (break-after
    indices) is consulted only for long narration walls with no dialogue; it
    falls back to the deterministic grouping if it returns nothing.
    """
    if not isinstance(line, str) or parse_marker(line) or len(line) <= min_split:
        return [line]
    sentences = split_sentences(line)
    if len(sentences) < 2:
        return [line]

    if (smart_fn is not None
            and len(line) > 2 * target
            and len(sentences) >= 4
            and not any(_is_turn(s) for s in sentences)):
        try:
            idxs = smart_fn(sentences, target)
        except Exception:
            idxs = None
        if idxs:
            return _apply_breaks(sentences, idxs)

    return _deterministic_paragraphs(sentences, target)


def split_content(lines, target=400, min_split=500, smart_fn=None):
    """Apply :func:`split_line` across a content array.

    Returns ``(new_lines, lines_split)``. Markers, blanks, and short lines are
    preserved in place; only oversized prose lines expand into several entries.
    """
    out, split_count = [], 0
    for line in lines or []:
        pieces = split_line(line, target=target, min_split=min_split, smart_fn=smart_fn)
        if len(pieces) > 1:
            split_count += 1
        out.extend(pieces)
    return out, split_count


# --- Model pass helpers (text never round-trips through the model) ---------

SMART_SYSTEM = (
    "You are a typesetter splitting one over-long paragraph of a novel into "
    "several natural paragraphs. You are given the paragraph's sentences as a "
    "numbered JSON object. Choose the sentence numbers AFTER WHICH a paragraph "
    "break should fall, picking natural seams — shifts in topic, time, place, "
    "or the move between narration and a new thought. Aim for paragraphs of "
    "roughly {target}-{double} characters; do not break after every sentence. "
    "Reply with ONLY a JSON array of integers, e.g. [3, 7, 12]. The integers "
    "are sentence numbers from the input. Never output any sentence text."
)


def build_smart_messages(sentences, target):
    """Build the (system, user) message contents for the model break-point pass."""
    payload = {str(i): s.strip() for i, s in enumerate(sentences)}
    import json
    system = SMART_SYSTEM.format(target=target, double=target * 2)
    user = json.dumps(payload, ensure_ascii=False, indent=1)
    return system, user


def parse_smart_response(raw, n):
    """Parse a model reply into validated break-after indices (0..n-2)."""
    import json
    raw = (raw or "").strip()
    if raw.startswith("```"):
        parts = raw.split("\n")
        raw = "\n".join(parts[1:-1]) if len(parts) > 2 else raw
        raw = raw.lstrip("json").strip()
    start, end = raw.find("["), raw.rfind("]")
    if start < 0 or end < 0:
        return []
    try:
        arr = json.loads(raw[start:end + 1])
    except (json.JSONDecodeError, ValueError):
        return []
    return sorted({int(x) for x in arr if isinstance(x, (int, float)) and 0 <= int(x) < n - 1})
