"""unit_converter module — append metric equivalents to Chinese measurement units.

Wraps the existing ``unit_converter.convert_units`` post-translation transform.
``convert_units`` is idempotent (the annotate path uses a negative lookahead so it
won't re-annotate, and the replace path leaves no matchable unit behind), so
re-running is safe. The translated-lines hook is fired at the post-translation
point (ui.py) rather than at every save: that keeps behavior identical to the old
inline call (runs once per fresh translation, with the per-run cleaning model) and
avoids invoking the optional AI false-positive filter on every manual chapter edit.

Enabled for every book by default (``default_enabled = True``); turn it off
per book via the book's Modules dialog.
"""
from .base import TranslationModule


class UnitConverterModule(TranslationModule):
    id = "unit_converter"
    name = "Unit Converter"
    description = ("Append metric equivalents to Chinese measurement units in the "
                   "translation, e.g. \"1000 zhang (3.3 km)\".")
    default_enabled = True

    def transform_translated_lines(self, content, ctx):
        if not isinstance(content, list):
            return content
        from unit_converter import convert_units
        return convert_units(content, cleaning_model=ctx.get("cleaning_model"))
