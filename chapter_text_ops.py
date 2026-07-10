"""Pure text transforms extracted from web route handlers (B4).

No database access here — every function takes plain Python data
(lists of lines / strings) and returns new data plus a change count,
so the logic is unit-testable without a running app or DB.

Semantics are copied verbatim from the original handlers in
web/api/entities.py (`decase_entity` and the ``substitute`` branch of
`propagate_change`); any behavioral quirk documented below is intentional
parity with the previous inline code.
"""
import re
from itertools import zip_longest


# ------------------------------------------------------------------
# Decasing (lowercase mid-sentence occurrences of a capitalised term)
# ------------------------------------------------------------------

def decase_lines(lines, word, lowered=None, protected_terms=()):
    """Lowercase mid-sentence occurrences of ``word`` in ``lines``.

    Capitalisation is preserved when the match:
      * sits at the start of a line (only whitespace before it),
      * directly follows an opening quote mark (``"``, left double/single
        curly quotes, ``'``) or ``【``,
      * follows a sentence terminator (``.!?``) separated only by spaces, or
      * lies fully inside an occurrence of a *protected* compound term
        (another entity translation that contains ``word``).

    Only the first character is lowercased (``lowered`` defaults to
    ``word[0].lower() + word[1:]``), matching the original handler.

    When ``word`` ends with a letter the match requires a word boundary
    after it (so "Elder" does not hit "Elderly"); otherwise the raw string
    is matched.

    Returns ``(new_lines, replacements)`` where ``replacements`` counts
    *changed lines* (the original handler incremented its substitution
    counter once per line, not per match).
    """
    if not word:
        return list(lines), 0

    if lowered is None:
        lowered = word[0].lower() + word[1:]

    pattern = re.compile(re.escape(word) + r'\b' if word[-1].isalpha() else re.escape(word))

    replacements = 0
    new_lines = []
    for line in lines:
        # Character spans occupied by protected compound phrases in this
        # line — word matches fully inside one of these are left alone.
        protected_spans = []
        for phrase in protected_terms:
            i = line.find(phrase)
            while i != -1:
                protected_spans.append((i, i + len(phrase)))
                i = line.find(phrase, i + 1)

        def replacer(m, line=line, protected_spans=protected_spans):
            pos = m.start()
            for s, e in protected_spans:
                if s <= pos and m.end() <= e:
                    return m.group(0)
            if line[:pos].strip() == '':
                return m.group(0)
            if pos > 0 and line[pos - 1] in '"“‘\'【':
                return m.group(0)
            i = pos - 1
            while i >= 0 and line[i] == ' ':
                i -= 1
            if i >= 0 and line[i] in '.!?':
                return m.group(0)
            return lowered + m.group(0)[len(word):]

        new_line = pattern.sub(replacer, line)
        if new_line != line:
            replacements += 1
        new_lines.append(new_line)

    return new_lines, replacements


# ------------------------------------------------------------------
# Case-preserving substitution (entity translation propagation)
# ------------------------------------------------------------------

def build_case_preserving_replacer(old_translation, new_translation):
    """Return a ``re.Match -> str`` replacer that swaps ``old_translation``
    for ``new_translation`` while preserving *positional* casing.

    Positional casing is the capitalisation a word picks up from where it
    sits (sentence-start capital, ALL-CAPS headings): each matched word is
    compared against the canonical old word and only that shift is
    re-applied to the new word. The old translation's *own* casing is NOT
    preserved: a pure case correction (e.g. "azure sword" → "Azure Sword")
    is applied as written. Comparing the new word against the matched
    chapter text instead would make such a correction reproduce the old
    casing and silently no-op.
    """
    old_words = old_translation.split()
    new_words = new_translation.split()

    def match_case(match):
        chapter_words = match.group().split()
        transformed = []
        for idx, (old_w, new_w) in enumerate(
            zip_longest(old_words, new_words, fillvalue="")
        ):
            if not new_w:
                continue
            if not old_w:
                # New translation has more words than the old one; the extra
                # words have no positional reference, so use them verbatim.
                transformed.append(new_w)
                continue
            chapter_w = chapter_words[idx] if idx < len(chapter_words) else old_w
            # The guards ensure a branch fires only for a genuine case
            # *shift*; when the old word is already in that form the new
            # word is used verbatim (preserving internal caps, "HeavenNet").
            if chapter_w == old_w.upper() and not old_w.isupper():
                transformed.append(new_w.upper())
            elif chapter_w == old_w[0].upper() + old_w[1:] and not old_w[0].isupper():
                transformed.append(new_w[0].upper() + new_w[1:])
            elif chapter_w == old_w[0].lower() + old_w[1:] and not old_w[0].islower():
                transformed.append(new_w[0].lower() + new_w[1:])
            else:
                transformed.append(new_w)
        return " ".join(transformed).strip()

    return match_case


def build_substitution_pattern(old, word_boundary=False):
    """Compiled case-insensitive pattern matching ``old``.

    With ``word_boundary``, the match is fenced by ``(?<!\\w)``/``(?!\\w)``
    lookarounds so ``old`` is only matched as a whole word — e.g. "Dai" no
    longer matches inside "Daiyu". Lookarounds (rather than ``\\b``) keep the
    fence working when ``old`` begins or ends with a non-word character, and
    are zero-width so the matched text handed to the case-preserving replacer
    is unchanged.
    """
    escaped = re.escape(old)
    if word_boundary:
        escaped = r"(?<!\w)" + escaped + r"(?!\w)"
    return re.compile(escaped, re.IGNORECASE)


def substitute_in_lines(lines, old, new, word_boundary=False):
    """Case-insensitively replace ``old`` with ``new`` in every line,
    preserving positional casing (see :func:`build_case_preserving_replacer`).

    Returns ``(new_lines, count)`` where ``count`` is the number of lines
    that changed.
    """
    pattern = build_substitution_pattern(old, word_boundary)
    match_case = build_case_preserving_replacer(old, new)

    count = 0
    new_lines = []
    for line in lines:
        new_line = pattern.sub(match_case, line)
        if new_line != line:
            count += 1
        new_lines.append(new_line)
    return new_lines, count


def source_mentions(raw_source, needle):
    """True when a chapter's raw stored source text contains ``needle``.

    The source is JSON-encoded with ensure_ascii=False (or, for legacy rows,
    plain text), so a CJK substring appears literally either way. Used by
    the "safer substitute" sweep to restrict replacement to chapters whose
    source actually features the entity.
    """
    return bool(raw_source) and needle in raw_source
