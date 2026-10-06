"""Entity-record corrections. Every apply is guarded: refused while the book translates."""
from typing import Annotated, Literal, Optional

import anyio
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel, ConfigDict, Field

from ..deps import app
from ..deps import db as get_db
from ..formatting import json_out, truncate
from ..guard import ensure_book_idle
from ._common import require_book, tool

FORCE_DESC = "Skip the idle-book guard — only if you know nothing is translating this book"


def register(mcp) -> None:
    register_entity_fixes(mcp)

    @tool(mcp, "t9_backfill_origin_chapter", "Backfill origin chapters", read_only=False)
    async def t9_backfill_origin_chapter(
        book_id: int,
        mode: Annotated[Literal["missing", "recompute", "all"], Field(description="missing = set only NULL origins; recompute = only move origins EARLIER (skips 1-char keys and excluded keys); all = re-derive every origin in either direction")] = "missing",
        category: Optional[str] = None,
        include_short: Annotated[bool, Field(description="recompute: also consider single-character keys (usually coincidental matches)")] = False,
        skip_keys: Annotated[Optional[list[str]], Field(description="recompute: extra keys to leave alone, on top of backfill_origin_exclusions.json")] = None,
        include_queue: Annotated[bool, Field(description="Also scan queued (untranslated) chapters")] = True,
        apply: bool = False,
        force: Annotated[bool, Field(description=FORCE_DESC)] = False,
        max_rows: Annotated[int, Field(ge=1, le=5000)] = 500,
    ) -> str:
        """Set each entity's origin_chapter to its first appearance in the source
        (backfill_origin_chapter.py). The dry run (default) returns the worklist:
        each moved origin opens a drift window [new, old) — chapters translated
        before the record existed — worth auditing for inconsistent renderings.
        apply=true writes and is refused while the book translates."""
        from backfill_origin_chapter import apply_origin_backfill, compute_origin_backfill
        db = get_db()
        require_book(db, book_id)
        plan = await anyio.to_thread.run_sync(lambda: compute_origin_backfill(
            db, book_id, category=category, recompute=mode == "recompute",
            all_=mode == "all", include_short=include_short, skip_keys=skip_keys,
            include_queue=include_queue))
        applied = None
        if apply and plan.changes:
            ensure_book_idle(app(), book_id, force)
            applied = apply_origin_backfill(db, plan)
        changes = [dict(c, window=(f"[{c['new']}, {c['old']})"
                                   if c["old"] is not None and c["new"] < c["old"] else None))
                   for c in plan.changes]
        out = {
            "dry_run": not apply, "applied": applied, "scope": plan.scope,
            "chapters_scanned": plan.chapters_scanned,
            "chapter_range": [plan.first_chapter, plan.last_chapter],
            "entities_considered": plan.entities_considered,
            "changes_total": len(changes), "overwrites": plan.overwrites,
            "changes": changes[:max_rows],
            "unmatched_total": len(plan.unmatched),
            "unmatched": plan.unmatched[:max_rows],
            "kept_later": len(plan.kept_later), "skipped_short": len(plan.skipped_short),
            "skipped_excluded": len(plan.skipped_excluded), "warnings": plan.warnings,
        }
        if len(changes) > max_rows or len(plan.unmatched) > max_rows:
            out["truncated"] = f"lists capped at max_rows={max_rows}"
        return truncate(json_out(out), hint="filter by category or lower max_rows")


Mode = Annotated[Literal["none", "substitute", "safer"], Field(description=(
    "none = update the record only; substitute = also rewrite the old English in every "
    "translated chapter, title and entity note of the book (case-insensitive, "
    "case-preserving); safer = substitute only in chapters whose SOURCE mentions the term "
    "— use it when the old English is shared with another entity or is a common word"))]


class Correction(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    untranslated: str = Field(min_length=1)
    translation: str = Field(min_length=1)
    category: Optional[str] = None


def _summarise(r: dict) -> dict:
    keys = ("status", "error", "untranslated", "category", "old_translation",
            "new_translation", "chapter_substitutions", "note_substitutions",
            "chapters_scanned", "chapters_affected", "matches")
    return {k: r.get(k) for k in keys if r.get(k) not in (None, [], "")}


def register_entity_fixes(mcp) -> None:

    @tool(mcp, "t9_correct_entity", "Correct an entity's translation", read_only=False,
          destructive=True, idempotent=False)
    def t9_correct_entity(
        book_id: int,
        untranslated: Annotated[str, Field(min_length=1, description="The entity's exact Chinese key")],
        translation: Annotated[str, Field(min_length=1, description="The corrected English rendering")],
        category: Optional[str] = None,
        mode: Mode = "none",
        word_boundary: Annotated[bool, Field(description="Substitute whole words only ('Dai' not inside 'Daiyu')")] = False,
        dry_run: bool = True,
        force: Annotated[bool, Field(description=FORCE_DESC)] = False,
    ) -> str:
        """Fix one entity's English rendering (correct_entity_translation.py) and
        optionally sweep the old rendering out of the translated prose. The dry run
        (default) reports how many chapters/notes would change. Applying is refused
        while the book translates. Check usage with t9_entity_context first."""
        from correct_entity_translation import correct_entity
        db = get_db()
        require_book(db, book_id)
        if not dry_run:
            ensure_book_idle(app(), book_id, force)
        r = correct_entity(db, book_id, untranslated, translation, category=category,
                           mode=mode, word_boundary=word_boundary, dry_run=dry_run)
        if not r.get("ok"):
            raise ToolError(r.get("error") or f"Correction failed ({r.get('status')}).")
        return json_out({"dry_run": dry_run, "mode": mode, **_summarise(r)})

    @tool(mcp, "t9_bulk_correct_entities", "Correct many entities", read_only=False,
          destructive=True, idempotent=False)
    def t9_bulk_correct_entities(
        book_id: int,
        corrections: Annotated[list[Correction], Field(min_length=1, max_length=500, description="Applied IN ORDER — put longer phrases before the shorter ones they contain (the cascade)")],
        mode: Mode = "none",
        word_boundary: bool = False,
        dry_run: bool = True,
        force: Annotated[bool, Field(description=FORCE_DESC)] = False,
    ) -> str:
        """Apply several entity corrections in order (bulk_correct_entities.py).
        When substituting, each correction sweeps the chapters as the previous one
        left them, so a later entry reporting 0 substitutions after a longer phrase
        was fixed first is normal. The dry run evaluates every entry against the
        CURRENT text, so its counts for overlapping entries overstate the cascade.
        Applying is refused while the book translates."""
        from bulk_correct_entities import bulk_correct
        db = get_db()
        require_book(db, book_id)
        keys = [c.untranslated for c in corrections]
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        if dupes:
            raise ToolError(f"Duplicate keys in corrections: {dupes}")
        if not dry_run:
            ensure_book_idle(app(), book_id, force)
        payload = {c.untranslated: ({"translation": c.translation, "category": c.category}
                                    if c.category else c.translation) for c in corrections}
        results = bulk_correct(db, book_id, payload, mode=mode, word_boundary=word_boundary,
                               dry_run=dry_run)
        tally = {}
        for r in results:
            tally[r.get("status")] = tally.get(r.get("status"), 0) + 1
        return truncate(json_out({"dry_run": dry_run, "mode": mode, "tally": tally,
                                  "results": [_summarise(r) for r in results]}))

    @tool(mcp, "t9_change_entity_category", "Move entities to another category",
          read_only=False)
    def t9_change_entity_category(
        book_id: int,
        untranslated: Annotated[list[str], Field(min_length=1, max_length=500)],
        new_category: Annotated[str, Field(min_length=1)],
        current_category: Annotated[Optional[str], Field(description="Only move entities currently in this category")] = None,
        allow_new_category: Annotated[bool, Field(description="Permit a category the book does not have yet")] = False,
        dry_run: bool = True,
        force: Annotated[bool, Field(description=FORCE_DESC)] = False,
    ) -> str:
        """Re-file entities under another category (change_entity_category.py). The
        category decides prompt grouping and whether gender is tracked. Applying is
        refused while the book translates."""
        from change_entity_category import change_entity_categories
        db = get_db()
        require_book(db, book_id)
        if not dry_run:
            ensure_book_idle(app(), book_id, force)
        r = change_entity_categories(db, book_id, untranslated, new_category,
                                     current_category=current_category,
                                     force=allow_new_category, dry_run=dry_run)
        if not r.get("ok"):
            raise ToolError(r.get("error") or "Category change failed.")
        return truncate(json_out({k: r[k] for k in ("dry_run", "new_category", "counts",
                                                    "results")}))

    @tool(mcp, "t9_delete_entities", "Delete entities", read_only=False, destructive=True)
    def t9_delete_entities(
        book_id: int,
        untranslated: Annotated[list[str], Field(min_length=1, max_length=500)],
        category: Annotated[Optional[str], Field(description="Narrow ambiguous keys to one category")] = None,
        apply: bool = False,
        force: Annotated[bool, Field(description=FORCE_DESC)] = False,
    ) -> str:
        """Delete junk / generic / phantom entity records (delete_entity.py) by id,
        never by a raw DELETE. Ambiguous or unknown keys are reported and never
        deleted. Dry run unless apply=true; applying is refused while the book
        translates. Deleted notes and revision history go with the record."""
        from delete_entity import apply_entity_deletes, plan_entity_deletes
        db = get_db()
        require_book(db, book_id)
        to_delete, errors = plan_entity_deletes(db, book_id, untranslated, category)
        deleted = None
        if apply and to_delete:
            ensure_book_idle(app(), book_id, force)
            deleted = apply_entity_deletes(db, to_delete)
        return json_out({"dry_run": not apply, "deleted": deleted,
                         "to_delete": to_delete, "errors": errors})
