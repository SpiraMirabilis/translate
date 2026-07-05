"""Per-book module system: registry, resolution, and dispatchers.

A module is a :class:`~modules.base.TranslationModule` subclass registered here.
Modules are resolved per book from the book's ``source_url`` (auto-match against
each module's ``auto_url_patterns``) and the per-book ``modules`` override map
(``{module_id: bool}``): an explicit ``True`` forces a module on, ``False``
forces it off, and absence falls back to the URL auto-match.

The ingest / translation pipeline calls the ``apply_*`` dispatchers; book
enable/disable transitions call :func:`fire_book_module_events`.
"""
import json

from .base import TranslationModule
from .task_runner import ModuleTaskBusyError, module_task_runner
from .trad_to_simp_module import TradToSimpModule
from .chatgroup_transformer_module import ChatgroupTransformerModule
from .novel543 import Novel543Module
from .twkan import TwkanModule
from .partial_repair_module import PartialRepairModule
from .unit_converter_module import UnitConverterModule
from .chapter_spacing_module import ChapterSpacingModule
from .markdown_notifications_module import MarkdownNotificationsModule

# Registry. Insertion order == apply order when multiple modules are enabled.
# trad_to_simp runs first so every downstream source transform (e.g. novel543's
# boilerplate strip, chatgroup_transformer's line matching) sees canonical
# simplified text. partial_repair runs before
# unit_converter so leftover source-language lines are re-translated before metric
# annotations are added. markdown_notifications runs last (after chapter_spacing):
# the double-spacer would otherwise split a table's contiguous rows, so the table
# must be assembled after spacing settles.
REGISTRY = {m.id: m for m in [
    TradToSimpModule(), ChatgroupTransformerModule(), Novel543Module(),
    TwkanModule(), PartialRepairModule(),
    UnitConverterModule(), ChapterSpacingModule(), MarkdownNotificationsModule(),
]}


def all_modules():
    """All registered module instances, in registry order."""
    return list(REGISTRY.values())


def get_module(module_id):
    return REGISTRY.get(module_id)


def _overrides(book):
    """Parse a book's per-book override map; tolerate missing/malformed values."""
    if not book or not hasattr(book, "get"):
        return {}
    raw = book.get("modules")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def resolve_module_ids(book, ctx=None):
    """Set of enabled module ids for a book.

    Explicit per-book override (``{id: bool}``) wins; otherwise the module's
    ``auto_enabled(book, ctx)`` decides. ``ctx`` carries run context (e.g. the
    translation ``model``) so model-based auto-rules can resolve.
    """
    overrides = _overrides(book)
    ctx = ctx or {}
    enabled = set()
    for mod_id, mod in REGISTRY.items():
        ov = overrides.get(mod_id)
        on = ov if isinstance(ov, bool) else mod.auto_enabled(book, ctx)
        if on:
            enabled.add(mod_id)
    return enabled


def resolve_modules_for_book(book, ctx=None):
    """Ordered list of enabled module instances for a book."""
    enabled = resolve_module_ids(book, ctx)
    return [mod for mod_id, mod in REGISTRY.items() if mod_id in enabled]


# --- transform dispatchers -------------------------------------------------
# Each runs every enabled module's hook in registry order, threading the value
# through. A failing module is logged and skipped so it never aborts ingest.

def _load_module_settings(book, db):
    """Load all per-book module settings once: ``{module_id: {key: value}}``.

    Attached to ``ctx`` so hooks read their own settings in-memory (no per-line
    query). Returns ``{}`` when unavailable (no db/book) or on error — modules
    then fall back to schema defaults via ``resolve_settings``.
    """
    if db is None or not book or not hasattr(book, "get"):
        return {}
    book_id = book.get("id")
    if not book_id:
        return {}
    try:
        return db.get_module_settings(book_id)
    except Exception:  # noqa: BLE001 - settings must never break the pipeline
        return {}


def _ctx(book, config, logger, db=None, **extra):
    c = {"book": book, "config": config, "logger": logger, "db": db}
    c.update(extra)
    # Per-book module settings (loaded once here so hooks read in-memory).
    if "module_settings" not in c:
        c["module_settings"] = _load_module_settings(book, db)
    return c


def apply_source_ingest(book, content, config, logger, **extra):
    ctx = _ctx(book, config, logger, **extra)
    for mod in resolve_modules_for_book(book, ctx):
        try:
            content = mod.transform_source_lines(content, ctx)
        except Exception as e:  # noqa: BLE001 - never let a module break ingest
            logger.error(f"Module {mod.id}.transform_source_lines failed: {e}")
    return content


def apply_source_module(book, content, module_id, config, logger, **extra):
    """Apply a single source-transform module by id, if enabled for the book.

    For call sites (e.g. the translate-time normalization in translation_engine)
    that want one specific source transform without running the full ingest
    pipeline. No-op when the module is absent or not enabled for this book.
    """
    ctx = _ctx(book, config, logger, **extra)
    if module_id not in resolve_module_ids(book, ctx):
        return content
    mod = REGISTRY.get(module_id)
    if not mod:
        return content
    try:
        return mod.transform_source_lines(content, ctx)
    except Exception as e:  # noqa: BLE001
        logger.error(f"Module {module_id}.transform_source_lines failed: {e}")
        return content


def apply_translated_ingest(book, content, config, logger, **extra):
    ctx = _ctx(book, config, logger, **extra)
    for mod in resolve_modules_for_book(book, ctx):
        try:
            content = mod.transform_translated_lines(content, ctx)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Module {mod.id}.transform_translated_lines failed: {e}")
    return content


def apply_system_prompt(book, prompt, config, logger, **extra):
    ctx = _ctx(book, config, logger, **extra)
    for mod in resolve_modules_for_book(book, ctx):
        try:
            prompt = mod.transform_system_prompt(prompt, ctx)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Module {mod.id}.transform_system_prompt failed: {e}")
    return prompt


# --- lifecycle events ------------------------------------------------------

def apply_module_settings_change(db, book, module_id, new_settings, config, logger):
    """Persist a module's per-book settings, rebuilding its backfill if warranted.

    For a module that declares ``rebuild_on_settings_change`` and is currently
    enabled for the book, a settings change is applied as a remove→persist→add
    cycle so a settings-dependent backfill (e.g. chatgroup's 【…】 wrapping) is
    re-derived from the new settings. The settings are persisted synchronously
    (so the caller can report failure); the remove→add cycle itself runs as a
    background task on :data:`module_task_runner` — on a large book it rewrites
    every chapter twice. ``event_removed_from_book`` still sees the OLD settings:
    they are snapshotted before persisting and injected into its ctx.

    When no rebuild is warranted — module absent, currently disabled, no rebuild
    flag, or the resolved settings are unchanged — it just persists. Returns the
    persistence success bool.

    Guardrail: raises :class:`ModuleTaskBusyError` when the book already has a
    module task pending/running — settings must not change under a backfill.
    """
    mod = REGISTRY.get(module_id)
    if mod is None:
        return False
    book_id = book.get("id") if hasattr(book, "get") else None
    if not book_id:
        return False

    active = module_task_runner.active(book_id)
    if active:
        raise ModuleTaskBusyError(book_id, active)

    old_resolved = mod.resolve_settings(db.get_module_settings(book_id, module_id))
    new_resolved = mod.resolve_settings(new_settings)

    enabled = module_id in resolve_module_ids(book)
    rebuild = (getattr(mod, "rebuild_on_settings_change", False)
               and enabled and old_resolved != new_resolved)

    if not rebuild:
        return db.set_module_settings(book_id, module_id, new_settings)

    # Snapshot ALL stored settings before persisting so the reverse pass sees
    # the old values, then persist, then run remove→add in the background.
    old_settings_map = db.get_module_settings(book_id)
    module_task_runner.claim(book_id, f"rebuild {module_id} settings backfill")
    try:
        ok = db.set_module_settings(book_id, module_id, new_settings)
        if not ok:
            module_task_runner.release(book_id)
            return False

        def run():
            ctx_old = _ctx(book, config, logger, db=db,
                           module_settings=old_settings_map)
            try:
                mod.event_removed_from_book(ctx_old)
            except Exception as e:  # noqa: BLE001 - never let a rebuild break the save
                logger.error(f"Module {module_id}.event_removed_from_book (settings rebuild) failed: {e}")
            ctx_new = _ctx(book, config, logger, db=db)
            try:
                mod.event_add_to_book(ctx_new)
            except Exception as e:  # noqa: BLE001
                logger.error(f"Module {module_id}.event_add_to_book (settings rebuild) failed: {e}")

        module_task_runner.start(book_id, run, logger=logger)
    except Exception:
        module_task_runner.release(book_id)
        raise
    return ok


def start_book_module_events(db, book, before_ids, after_ids, config, logger):
    """Run :func:`fire_book_module_events` as a background task for this book.

    The book's runner slot must already be claimed (see
    ``books_repo.update_book``) — this just labels it with the actual diff and
    hands the event firing to the background thread. Module event backfills can
    rewrite every chapter of a large book, which is far too slow to run inside
    the HTTP request that toggled the module.
    """
    before_ids = set(before_ids or ())
    after_ids = set(after_ids or ())
    parts = []
    added = sorted(after_ids - before_ids)
    removed = sorted(before_ids - after_ids)
    if added:
        parts.append("enable " + ", ".join(added))
    if removed:
        parts.append("disable " + ", ".join(removed))
    label = "; ".join(parts) or "module change"

    def run():
        fire_book_module_events(db, book, before_ids, after_ids, config, logger)

    module_task_runner.start(book.get("id"), run, label=label, logger=logger)


def fire_book_module_events(db, book, before_ids, after_ids, config, logger):
    """Fire add/remove events for the modules whose enabled state changed.

    ``before_ids`` / ``after_ids`` are sets of module ids (from
    :func:`resolve_module_ids`) computed around a book update.

    Add events fire in registry order and remove events in *reverse* registry
    order, so stacked backfills (e.g. chatgroup_transformer's 【…】 wrapping
    feeding markdown_notifications' table conversion) apply and unwind
    LIFO-style when several modules toggle in one save.
    """
    before_ids = set(before_ids or ())
    after_ids = set(after_ids or ())
    ctx = _ctx(book, config, logger, db=db)
    for mod_id, mod in REGISTRY.items():
        if mod_id not in (after_ids - before_ids):
            continue
        try:
            mod.event_add_to_book(ctx)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Module {mod_id}.event_add_to_book failed: {e}")
    for mod_id, mod in reversed(REGISTRY.items()):
        if mod_id not in (before_ids - after_ids):
            continue
        try:
            mod.event_removed_from_book(ctx)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Module {mod_id}.event_removed_from_book failed: {e}")
