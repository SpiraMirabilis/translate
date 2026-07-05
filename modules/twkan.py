"""twkan module — strips obfuscated twkan.com source-site spam from raws.

twkan.com raws splice the source site's own domain into the chapter text,
often hidden behind Unicode lookalike / obfuscation tricks (combining "zalgo"
marks, full-width forms, confusable codepoints, zero-width spaces). This module
drops any source line that mentions "twkan" once de-obfuscated.

The matching is *exactly* the transformation performed by
``strip_queue_confusable_lines.py`` with ``--match "twkan"`` (the default
substring-glob mode): each line is reduced to its Unicode TR39 "skeleton"
before a confusable-immune substring match against the skeletonized pattern.
This module reuses that script's helpers verbatim so the two stay in lockstep.

It only runs for books whose source_url matches twkan.com (or where the user
force-enables it). Like all transform hooks it is idempotent.
"""
from .base import TranslationModule

# Reuse the script's confusable-skeleton + filtering logic verbatim so this
# module's transformation is, by construction, identical to running
# ``strip_queue_confusable_lines.py --match twkan``.
from strip_queue_confusable_lines import (
    build_matcher,
    filter_lines,
    load_confusables,
    make_skeletonizer,
)

_MATCH = "twkan"

# Lazily-built matcher: building it loads the (cached) confusables table, which
# we don't want to do at import time.
_matcher = None


def _get_matcher():
    global _matcher
    if _matcher is None:
        skeleton = make_skeletonizer(load_confusables())
        # whole_line=False -> substring glob, the script's default mode.
        matches, _ = build_matcher(_MATCH, skeleton, whole_line=False)
        _matcher = matches
    return _matcher


def strip_twkan_lines(content):
    """Drop confusable-obfuscated 'twkan' source lines. Idempotent.

    Accepts a list of lines, a JSON-serialized list string, or a newline-joined
    string and returns the same shape with matching lines removed.
    """
    filtered, _removed = filter_lines(content, _get_matcher())
    return filtered


class TwkanModule(TranslationModule):
    id = "twkan"
    name = "Twkan"
    description = ("Strip twkan.com source-site spam lines (confusable-aware, "
                   "matches obfuscated 'twkan') from source text.")
    auto_url_patterns = ["twkan.com"]

    def transform_source_lines(self, content, ctx):
        return strip_twkan_lines(content)
