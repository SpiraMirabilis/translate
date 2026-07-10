"""trad_to_simp module — convert traditional Chinese source text to simplified.

Wraps ``trad_simp.convert_text`` (OpenCC ``t2s`` plus 乾/著 guardrails) as a
source-text transform.
This logic was formerly hard-coded into the ingest/translate pipeline
(``database.py`` save_chapter + add_to_queue, ``translation_engine.py`` entity
extraction + translation); it now lives here as a normal per-book module.

Default **off**: a book only gets conversion when it explicitly opts in. For
backward compatibility the module's auto-enablement bridges to the pre-existing
controls — the per-book ``books.trad_to_simp`` column (tri-state NULL/0/1) and
the global ``TRAD_TO_SIMP`` setting — so existing deployments behave unchanged:

  * per-book column ``1``/``0`` forces the conversion on/off for that book;
  * column ``NULL`` (the default) inherits the global ``config.trad_to_simp``
    flag, which itself defaults to off.

The ``books.modules`` override map still wins over all of the above (an explicit
``{"trad_to_simp": true/false}`` force-enables/disables), per the module system's
normal precedence.

The OpenCC dependency is optional and imported lazily inside the hook; if it is
missing the source passes through unchanged (logged, never fatal).
"""
from .base import TranslationModule


class TradToSimpModule(TranslationModule):
    id = "trad_to_simp"
    name = "Traditional → Simplified"
    description = ("Convert traditional Chinese source text to simplified (OpenCC "
                   "t2s) before translation, so entity matching and prompts work "
                   "against mainland-standard text.")
    # Default off; opt-in per book (see auto_enabled / the Modules dialog).
    default_enabled = False

    def auto_enabled(self, book, ctx):
        """On when the legacy per-book ``trad_to_simp`` column says so, else the
        global ``config.trad_to_simp`` flag (default off)."""
        if book and hasattr(book, "get"):
            override = book.get("trad_to_simp")
            if override is not None:
                return bool(override)
        config = (ctx or {}).get("config")
        return bool(getattr(config, "trad_to_simp", False))

    @property
    def has_auto(self):
        # Can auto-enable via the per-book column / global flag, so 'Auto' is
        # meaningful in the UI even though default_enabled is False.
        return True

    @property
    def auto_hint(self):
        return "on when the book's Trad→Simp setting (or the global default) is on"

    def transform_source_lines(self, content, ctx):
        try:
            from trad_simp import convert_text
        except ImportError as e:
            logger = (ctx or {}).get("logger")
            if logger:
                logger.error(f"Trad→simp conversion skipped: {e}")
            return content
        return convert_text(content)
