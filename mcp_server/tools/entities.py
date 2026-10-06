"""Entity glossary: list, search, context (read-only); note and gender writes (guarded)."""

from typing import Annotated, Literal, Optional

import anyio
from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import Field

from ..deps import app
from ..deps import db as get_db
from ..formatting import json_out, page_footer, paginate, truncate
from ..guard import ensure_book_idle
from ._common import (CHAPTERS_DESC, Format, chapter_predicate, report, require_book,
                      resolve_entity, tool)

def register(mcp) -> None:
    register_read_tools(mcp)

    @tool(mcp, "t9_reindex_chapter_entities", "Reindex chapter terms", read_only=False)
    async def t9_reindex_chapter_entities(
        book_id: int,
        only_missing: Annotated[bool, Field(description="Only chapters with no index rows yet")] = False,
        ctx: Context = None,
    ) -> str:
        """Rebuild the reader's "Terms this chapter" index for a book. Membership is
        cached at save time, so run this after adding or renaming entities (a term
        added at ch300 is otherwise missing from ch5's panel). Not guarded: it
        reads the glossary and writes only the index."""
        db = get_db()
        require_book(db, book_id)
        total = len(db.list_chapters(book_id))
        token = anyio.lowlevel.current_token()
        seen = [0]

        def progress(chapter_number, n_rows):
            seen[0] += 1
            if seen[0] % 25 == 0:
                anyio.from_thread.run(report, ctx, seen[0], total,
                                      f"ch{chapter_number}", token=token)

        done, rows = await anyio.to_thread.run_sync(
            lambda: db.reindex_book_chapter_entities(book_id, only_missing=only_missing,
                                                     progress=progress))
        return json_out({"book_id": book_id, "chapters_indexed": done, "rows_written": rows})

    @tool(mcp, "t9_set_entity_note", "Set an entity's note", read_only=False)
    def t9_set_entity_note(
        book_id: int,
        untranslated: Annotated[str, Field(min_length=1, description="The entity's exact Chinese key")],
        note: Annotated[str, Field(max_length=500, description="Complete replacement note; empty string clears it")],
        category: Annotated[Optional[str], Field(description="Needed only when the key exists in several categories")] = None,
        reason: Annotated[Optional[str], Field(max_length=300)] = None,
        chapter_number: Annotated[Optional[int], Field(description="Chapter this fact belongs to; omit for a present-day correction (not rewound on retranslation)")] = None,
        force: Annotated[bool, Field(description="Skip the idle-book guard")] = False,
    ) -> str:
        """Replace an entity's standing note (injected into every prompt that
        mentions it). Recorded as a `human` revision and revertable. Refused
        while the book is translating unless force=true."""
        db = get_db()
        require_book(db, book_id)
        ent = resolve_entity(db, book_id, untranslated, category)
        ensure_book_idle(app(), book_id, force)
        new = note.strip() or None
        rev = db.set_entity_note(ent["id"], new, author="human", chapter_number=chapter_number,
                                 reason=reason)
        return json_out({"entity_id": ent["id"], "category": ent["category"],
                         "untranslated": untranslated, "changed": rev is not None,
                         "revision_id": rev, "previous_note": ent.get("note"), "note": new})

    @tool(mcp, "t9_set_entity_gender", "Set an entity's gender", read_only=False)
    def t9_set_entity_gender(
        book_id: int,
        untranslated: Annotated[str, Field(min_length=1)],
        gender: Literal["male", "female", "neutral", ""],
        category: Optional[str] = None,
        reason: Annotated[Optional[str], Field(max_length=300)] = None,
        force: bool = False,
    ) -> str:
        """Correct an entity's gender (decides pronouns in every later chapter).
        Only for categories the book tracks gender on; "" clears it. Recorded as a
        `human` revision. Gender is not point-in-time: a character who changes
        gender in the story gets the current value here and the history in the
        note. Refused while the book is translating unless force=true."""
        db = get_db()
        require_book(db, book_id)
        ent = resolve_entity(db, book_id, untranslated, category)
        if not db.is_gendered_category(book_id, ent["category"]):
            gendered = ", ".join(db.get_book_gendered_categories(book_id)) or "none"
            raise ToolError(f"Category '{ent['category']}' is not gender-tracked in book "
                            f"{book_id} (gendered: {gendered}).")
        ensure_book_idle(app(), book_id, force)
        rev = db.set_entity_gender(ent["id"], gender or None, author="human", reason=reason)
        return json_out({"entity_id": ent["id"], "category": ent["category"],
                         "untranslated": untranslated, "changed": rev is not None,
                         "revision_id": rev, "previous_gender": ent.get("gender"),
                         "gender": gender or None})


def _flatten(grouped):
    return [(cat, e) for cat in sorted(grouped) for e in grouped[cat]]


def register_read_tools(mcp) -> None:

    @tool(mcp, "t9_list_entities", "List a book's entities", read_only=True)
    def t9_list_entities(
        book_id: int,
        origin_chapter: Annotated[Optional[str], Field(description="Filter on the chapter an entity first appeared: N, N-M, >N, >=N, <N, <=N (one term). Also includes entities whose note changed in that range unless origin_only")] = None,
        as_of_chapter: Annotated[Optional[int], Field(description="Show notes as they read at the end of this chapter (default: the filter's upper bound)")] = None,
        current_notes: Annotated[bool, Field(description="Show today's notes even with a chapter filter")] = False,
        origin_only: bool = False,
        category: Optional[str] = None,
        limit: Annotated[int, Field(ge=1, le=2000)] = 300,
        offset: Annotated[int, Field(ge=0)] = 0,
        format: Format = "text",
    ) -> str:
        """The book's glossary (get_entities.py): origin chapter, Chinese → English,
        gender, global flag, 'note updated chN' tags, and the note — point-in-time
        by default when a chapter filter is given, so a ch1-20 review is not
        spoiled by ch300 facts. origin_chapter is only a floor until
        t9_backfill_origin_chapter has run on the book."""
        from get_entities import build_entities_payload, render_entities_text
        db = get_db()
        book = require_book(db, book_id)
        try:
            payload = build_entities_payload(db, book, origin_chapter,
                                             as_of_chapter=as_of_chapter,
                                             current_notes=current_notes,
                                             origin_only=origin_only)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        items = _flatten(payload["entities"])
        if category:
            items = [(c, e) for c, e in items if c == category]
        page, meta = paginate(items, limit, offset)
        grouped = {}
        for c, e in page:
            grouped.setdefault(c, []).append(e)
        payload = {**payload, "entities": grouped}
        if format == "json":
            return truncate(json_out({**meta, **payload}), hint="page with limit/offset")
        return truncate(render_entities_text(payload) + page_footer(meta),
                        hint="page with limit/offset")

    @tool(mcp, "t9_search_entities", "Search entities", read_only=True)
    def t9_search_entities(
        book_id: int,
        pattern: Annotated[str, Field(min_length=1, description="Substring (or regex) to find")],
        field: Literal["untranslated", "translated", "both"] = "both",
        regex: bool = False,
        case_sensitive: bool = False,
        category: Optional[str] = None,
        origin_chapter: Annotated[Optional[str], Field(description="N, N-M, >N … (one term)")] = None,
        limit: Annotated[int, Field(ge=1, le=1000)] = 200,
        offset: Annotated[int, Field(ge=0)] = 0,
    ) -> str:
        """Find entity records by Chinese key or English rendering
        (search_entities.py) — e.g. every record whose translation contains
        "Sect", to check a convention is applied consistently."""
        from get_entities import parse_chapter_filter
        from search_entities import build_matcher, fetch_entities, filter_rows, render
        db = get_db()
        require_book(db, book_id)
        try:
            clause, params = parse_chapter_filter(origin_chapter)
            matcher = build_matcher(pattern, regex, not case_sensitive)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        rows = filter_rows(fetch_entities(db, book_id, category, clause, params), matcher, field)
        page, meta = paginate(rows, limit, offset)
        return truncate(render(page, category) + page_footer(meta))

    @tool(mcp, "t9_entity_context", "Entity usage in context", read_only=True)
    def t9_entity_context(
        book_id: int,
        entities: Annotated[list[str], Field(min_length=1, max_length=50, description="Chinese terms to look up")],
        mentions: Annotated[str, Field(description="Which occurrences: '1' (first), '1,$' (first and last), '2,3,$'")] = "1",
        chapters: Annotated[Optional[str], Field(description=CHAPTERS_DESC)] = None,
    ) -> str:
        """Show the Chinese source paragraphs around each occurrence of a term
        (get_entity_context.py), with chapter and paragraph position — check what a
        term really refers to before correcting its record. Source only: read the
        translation of the same spot with t9_get_chapter or t9_grep_book field="en"."""
        from get_entity_context import contexts_for_entity, parse_mentions
        db = get_db()
        book = require_book(db, book_id)
        try:
            occ = parse_mentions(mentions)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        pred = chapter_predicate(chapters)
        blocks = []
        for ent in dict.fromkeys(e.strip() for e in entities if e.strip()):
            for r in contexts_for_entity(db, book, ent, "\n\n", occ, chapter_filter=pred):
                blocks.append(f"{r['header']}\n{r['body']}")
        return truncate("\n\n".join(blocks) or "(no occurrences)",
                        hint="ask for fewer entities or mentions")
