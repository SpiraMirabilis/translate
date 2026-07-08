"""Site Ad Stripper module — removes obfuscated source-site spam from raws.

Scraped raws (twkan.com and friends) splice the source site's own domain into
the chapter text, usually hidden behind Unicode lookalike / obfuscation tricks
(combining "zalgo" marks, full-width forms, confusable codepoints, zero-width
spaces). This module removes that spam, confusable-aware, via two strategies:

  * **Whole-line delete** — for ads that occupy a *dedicated* line (a banner the
    site injects between paragraphs), the entire line is dropped. This is the
    original twkan behavior:

        本书由𝕥𝕨𝕜𝕒𝕟.𝕔𝕠𝕞全网首发            → (line removed)
        （请记住 台湾小说网…twkan.com…）     → (line removed)

  * **Inline substring excise** — for ads that are *spliced into a real story
    line* (appended to a sentence, or injected mid-sentence before the next
    clause), only the marker span is cut out and the surrounding text is kept.
    Deleting the whole line here would destroy real content:

        …宜出行。𝟨𝟫𝓈𝒽𝓊𝓍.𝒸𝑜𝓂            → …宜出行。
        「没忘东西吧。𝟨𝟫𝘴𝘩𝘶𝘹.𝘤𝘰𝘮」陆阳… → 「没忘东西吧。」陆阳…

Both strategies match on the Unicode TR39 *skeleton* of the text (via
``strip_queue_confusable_lines``'s helpers, reused verbatim so matching stays in
lockstep with the ``strip_queue_confusable_lines.py`` CLI), so every homoglyph
font variant of a marker (𝟨𝟫𝓈𝒽𝓊𝓍, 𝟨𝟫ⓢⓗⓤⓧ, 𝟨𝟫ˢʰᵘˣ, …) collapses to the
same canonical form before matching. Patterns are confusable-aware globs
(``*``/``?``/``[...]`` supported).

Both defaults ship as *editable per-book settings*: the whole-line list defaults
to ``twkan`` and the substring list to ``69shux.com``. Users add site-specific
markers to either list (one glob per line) through the Modules settings dialog;
clearing a list disables that strategy. When a book has no stored settings, the
schema defaults apply, so the original twkan strip keeps working unchanged and
inline 69shux.com markers are now excised too.

It auto-enables for books whose ``source_url`` matches twkan.com (or when
force-enabled per book). Like all transform hooks it is idempotent: a
delete-matched line is gone (can't re-match), and an excised line no longer
contains the marker skeleton (can't re-excise). It runs at source ingest only —
enabling it does not retroactively rewrite already-stored chapters.
"""
import json
import re

from .base import TranslationModule

# Reuse the CLI's confusable-skeleton + glob helpers verbatim so this module's
# matching is, by construction, identical to running
# ``strip_queue_confusable_lines.py``. (Pinned by tests/test_twkan_import_chain.py.)
from strip_queue_confusable_lines import (
    build_matcher,
    glob_to_regex,
    load_confusables,
    make_skeletonizer,
)

# Built-in exemplar patterns, exposed as the editable settings defaults.
_DEFAULT_LINE_PATTERNS = "twkan"
_DEFAULT_SUBSTRING_PATTERNS = "69shux.com"


# --------------------------------------------------------------------------- #
# Lazily-built skeletonizer + compiled-matcher caches. Building the skeletonizer
# loads the (cached) confusables table, which we don't want at import time.
# --------------------------------------------------------------------------- #
_skeleton = None
_line_matcher_cache = {}   # pattern -> predicate(line)->bool
_substr_regex_cache = {}   # pattern -> compiled regex over the skeleton (or None)


def _get_skeleton():
    global _skeleton
    if _skeleton is None:
        _skeleton = make_skeletonizer(load_confusables())
    return _skeleton


def _line_matcher(pattern):
    """Confusable-aware substring predicate for the whole-line-delete strategy."""
    m = _line_matcher_cache.get(pattern)
    if m is None:
        # whole_line=False -> substring glob (a line "…twkan.com…" matches "twkan").
        m, _ = build_matcher(pattern, _get_skeleton(), whole_line=False)
        _line_matcher_cache[pattern] = m
    return m


def _substr_regex(pattern):
    """Regex (over the skeleton) locating a marker span for inline excision.

    Returns ``None`` for patterns too short to excise safely (guards against an
    over-broad user pattern nuking real text): the skeletonized pattern, minus
    glob metacharacters, must be at least two characters.
    """
    if pattern in _substr_regex_cache:
        return _substr_regex_cache[pattern]
    skel_pat = _get_skeleton()(pattern)
    literal = skel_pat.replace("*", "").replace("?", "")
    rx = None
    if len(literal) >= 2:
        rx = re.compile("(?s:" + glob_to_regex(skel_pat) + ")")
    _substr_regex_cache[pattern] = rx
    return rx


def _parse_patterns(raw):
    """Split a textarea setting into a list of patterns (one per non-blank line).

    Lines starting with ``#`` are treated as comments and ignored.
    """
    if not raw:
        return []
    out = []
    for line in str(raw).splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


# --------------------------------------------------------------------------- #
# Inline substring excision
# --------------------------------------------------------------------------- #

def _skeleton_alignment(raw, skel):
    """Map ``raw`` to its concatenated per-character skeleton.

    Returns ``(skel_str, idx_map)`` where ``idx_map[k]`` is the index in ``raw``
    of the character that produced ``skel_str[k]``. Per-character skeletonization
    reproduces the whole-string skeleton for the independent homoglyph runs these
    markers are made of, and gives us the raw span to cut for each match.
    """
    skel_chars = []
    idx_map = []
    for i, ch in enumerate(raw):
        for c in skel(ch):
            skel_chars.append(c)
            idx_map.append(i)
    return "".join(skel_chars), idx_map


def _excise_markers(line, regexes):
    """Remove every span matching any of ``regexes`` from ``line``. Idempotent."""
    if not isinstance(line, str) or not line or not regexes:
        return line
    skel = _get_skeleton()
    skel_str, idx_map = _skeleton_alignment(line, skel)
    remove = set()
    for rx in regexes:
        for m in rx.finditer(skel_str):
            a, b = m.span()
            if b > a:
                # Cut the raw characters spanning the matched skeleton range.
                remove.update(range(idx_map[a], idx_map[b - 1] + 1))
    if not remove:
        return line
    return "".join(c for i, c in enumerate(line) if i not in remove)


def _process_line(line, line_matchers, substr_regexes):
    """Return the cleaned line, or ``None`` to drop it (whole-line ad)."""
    if not isinstance(line, str):
        return line
    if any(m(line) for m in line_matchers):
        return None
    return _excise_markers(line, substr_regexes)


def _process_list(lines, line_matchers, substr_regexes):
    out = []
    for l in lines:
        r = _process_line(l, line_matchers, substr_regexes)
        if r is not None:
            out.append(r)
    return out


def strip_ad_lines(content, line_matchers, substr_regexes):
    """Apply both strategies to a content blob, preserving its shape.

    Accepts a list of lines, a JSON-serialized list string, or a newline-joined
    string, and returns the same shape. Returns the original object unchanged
    when nothing was rewritten (so callers can cheaply detect no-ops).
    """
    if isinstance(content, list):
        new = _process_list(content, line_matchers, substr_regexes)
        return new if new != content else content

    if isinstance(content, str):
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            data = None
        if isinstance(data, list):
            new = _process_list(data, line_matchers, substr_regexes)
            return json.dumps(new, ensure_ascii=False) if new != data else content
        parts = content.split("\n")
        new = _process_list(parts, line_matchers, substr_regexes)
        return "\n".join(new) if new != parts else content

    return content


class TwkanModule(TranslationModule):
    id = "twkan"
    name = "Site Ad Stripper"
    description = ("Strip obfuscated source-site ad spam from source text "
                  "(confusable-aware). Whole-line ads are deleted; inline ad "
                  "markers spliced into real sentences are excised in place. "
                  "Defaults cover twkan.com / 69shux.com; add site-specific "
                  "patterns per book.")
    auto_url_patterns = ["twkan.com"]

    settings_schema = [
        {"key": "line_delete_patterns", "type": "textarea",
         "default": _DEFAULT_LINE_PATTERNS,
         "label": "Whole-line ad patterns (one per line)",
         "help": ("Confusable-aware glob patterns. Any source line whose text "
                  "matches one is deleted entirely — use for ads on their own "
                  "dedicated line. Clear to disable. Default: twkan.")},
        {"key": "substring_patterns", "type": "textarea",
         "default": _DEFAULT_SUBSTRING_PATTERNS,
         "label": "Inline ad markers to excise (one per line)",
         "help": ("Confusable-aware glob patterns. Each match is cut out in "
                  "place, keeping the rest of the line — use for markers spliced "
                  "into real sentences. Clear to disable. Default: 69shux.com.")},
    ]

    def _matchers(self, ctx):
        """(line_matchers, substr_regexes) resolved from this book's settings."""
        settings = self.resolve_settings((ctx.get("module_settings") or {}).get(self.id))
        line_matchers = [_line_matcher(p)
                         for p in _parse_patterns(settings.get("line_delete_patterns"))]
        substr_regexes = [rx for rx in
                          (_substr_regex(p)
                           for p in _parse_patterns(settings.get("substring_patterns")))
                          if rx is not None]
        return line_matchers, substr_regexes

    def transform_source_lines(self, content, ctx):
        line_matchers, substr_regexes = self._matchers(ctx)
        if not line_matchers and not substr_regexes:
            return content
        return strip_ad_lines(content, line_matchers, substr_regexes)
