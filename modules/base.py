"""Base class for per-book translation modules.

A *module* is a named bundle of per-book behavior (text transforms + lifecycle
events). Modules are applied to a book automatically when the book's
``source_url`` matches one of the module's ``auto_url_patterns``, and can be
force-enabled or force-disabled per book via the ``books.modules`` override map
(see ``modules/__init__.py``).

Hook contract:
  * Transform hooks (``transform_*``) MUST return the same type they received
    (list -> list, str -> str) and MUST be idempotent — they may run more than
    once on the same content (re-save, re-queue).
  * Event hooks (``event_*``) are fired on enable/disable transitions and may
    have side effects (e.g. flip another per-book setting). They receive a
    ``ctx`` carrying the DatabaseManager so they can persist changes.

All hooks default to no-ops; a concrete module overrides only what it needs.
``ctx`` is always a dict so new keys can be added without changing signatures.
"""


class TranslationModule:
    # --- identity / metadata (override in subclasses) ---
    id = ""                 # stable machine id, e.g. "novel543"
    name = ""               # human label
    description = ""        # one-line description for the UI
    auto_url_patterns = []  # substrings; if any is in book.source_url, auto-enable
    default_enabled = False  # if True, on for every book by default (no URL needed)

    # --- per-book settings (override in subclasses that need configuration) ---
    # A list of declarative field descriptors rendered by the frontend's
    # schema-driven settings modal and read back per book. Each descriptor:
    #   {
    #     "key":   str,                 # stable setting key (stored verbatim)
    #     "type":  "bool"|"text"|"textarea"|"number"|"select"|"multiselect",
    #     "label": str,                 # UI label
    #     "help":  str,                 # optional helper text
    #     "default": Any,               # value used when unset
    #     "options": [...],             # static options for select/multiselect
    #     "options_source": "book_categories",  # optional dynamic option source
    #     "show_if": {other_key: value, ...},   # optional; render/apply only when
    #                                           # every listed key == the given value
    #   }
    # Empty by default → the module has no settings and shows no gear in the UI.
    settings_schema = []

    # When True, changing this module's per-book settings while it is enabled is
    # applied as a remove→persist→add cycle, so a settings-dependent backfill
    # (see the event hooks) is re-derived from the new settings instead of going
    # stale. Leave False for modules whose settings don't affect a backfill.
    rebuild_on_settings_change = False

    @property
    def has_settings(self):
        """Whether this module exposes any per-book settings (drives the UI gear)."""
        return bool(self.settings_schema)

    def default_settings(self):
        """Map of ``{key: default}`` from ``settings_schema`` (missing default → None)."""
        return {f["key"]: f.get("default") for f in self.settings_schema}

    def resolve_settings(self, stored):
        """Merge a book's *stored* settings over the schema defaults.

        ``stored`` is a ``{key: value}`` dict (or ``None``). Returns a complete
        settings dict with every schema key present, stored values winning.
        """
        merged = self.default_settings()
        if stored:
            merged.update({k: v for k, v in stored.items() if k in merged})
        return merged

    # --- auto-enablement ---
    def auto_enabled(self, book, ctx):
        """Whether this module auto-enables (absent an explicit per-book override).

        Default: on for every book if ``default_enabled``, else on when the book's
        ``source_url`` matches one of ``auto_url_patterns``. Override for custom
        criteria (e.g. based on the translation model, available via ``ctx``).
        """
        if self.default_enabled:
            return True
        url = (book.get("source_url") or "") if (book and hasattr(book, "get")) else ""
        return any(p in url for p in self.auto_url_patterns)

    @property
    def has_auto(self):
        """Whether 'Auto' is a meaningful choice for this module in the UI.

        True when the module can auto-enable on its own — via ``default_enabled``
        or a URL-pattern match. Modules that override ``auto_enabled`` with custom
        criteria (e.g. model-based rules) must also override this to True so the
        UI keeps offering the 'Auto' option.
        """
        return bool(self.default_enabled or self.auto_url_patterns)

    @property
    def auto_hint(self):
        """Human-readable description of when 'Auto' enables this module (for the UI)."""
        if self.default_enabled:
            return "on for every book"
        if self.auto_url_patterns:
            return "on when Source URL matches " + ", ".join(self.auto_url_patterns)
        return "off unless turned on"

    # --- transform hooks ---
    def transform_source_lines(self, content, ctx):
        """Transform untranslated source content on ingest. Return same type."""
        return content

    def transform_translated_lines(self, content, ctx):
        """Transform translated content before it is persisted. Return same type."""
        return content

    def transform_system_prompt(self, prompt, ctx):
        """Transform the assembled system prompt before it is sent. Return str."""
        return prompt

    # --- lifecycle events (side effects allowed; ctx carries 'db') ---
    def event_add_to_book(self, ctx):
        """Fired when this module becomes enabled for a book."""
        return None

    def event_removed_from_book(self, ctx):
        """Fired when this module becomes disabled for a book."""
        return None
