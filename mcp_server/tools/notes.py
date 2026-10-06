"""Entity-note history (read-only)."""
from typing import Annotated, Literal, Optional

from mcp.server.fastmcp.exceptions import ToolError
from pydantic import Field

from ..deps import db as get_db
from ..formatting import json_out, page_footer, paginate, truncate
from ._common import Format, require_book, resolve_entity, tool


def register(mcp) -> None:
    register_revisions(mcp)

    @tool(mcp, "t9_notes_as_of", "Notes as of a chapter", read_only=True)
    def t9_notes_as_of(
        book_id: int,
        chapter: Annotated[int, Field(ge=0, description="Notes as they read at the END of this chapter")],
        entity: Annotated[Optional[str], Field(description="One entity's Chinese key; omit for every entity that had a note")] = None,
        category: Optional[str] = None,
        limit: Annotated[int, Field(ge=1, le=1000)] = 200,
        offset: Annotated[int, Field(ge=0)] = 0,
        format: Format = "text",
    ) -> str:
        """Point-in-time glossary notes — what a reader or a retranslation of that
        chapter sees (no spoilers from later chapters). Undated revisions (hand
        edits, script sweeps) count as present-day and are not rewound."""
        db = get_db()
        require_book(db, book_id)
        if entity:
            ent = resolve_entity(db, book_id, entity, category)
            note = db.notes_as_of(book_id, chapter, entity_ids=[ent["id"]]).get(ent["id"])
            out = {"untranslated": entity, "translation": ent["translation"],
                   "chapter": chapter, "note": note, "current_note": ent.get("note")}
            return json_out(out)
        notes = db.notes_as_of(book_id, chapter, key_by="untranslated")
        items = sorted((k, v) for k, v in notes.items() if v)
        page, meta = paginate(items, limit, offset)
        if format == "json":
            return json_out({**meta, "chapter": chapter, "notes": dict(page)})
        if not page:
            return f"No entity carried a note at the end of chapter {chapter}."
        return "\n".join([f"{k}: {v}" for k, v in page] + [page_footer(meta)])


def register_revisions(mcp) -> None:

    @tool(mcp, "t9_note_revisions", "Entity note history", read_only=True)
    def t9_note_revisions(
        book_id: int,
        entity: Annotated[Optional[str], Field(description="Substring of the Chinese key OR the English rendering, case-insensitive")] = None,
        entity_id: Optional[int] = None,
        chapters: Annotated[Optional[str], Field(description="Revision chapter: N, N-M, >N … (one term)")] = None,
        no_chapter: Annotated[bool, Field(description="Only undated revisions (hand edits, script sweeps)")] = False,
        author: Optional[Literal["model", "human", "script"]] = None,
        shrink_only: Annotated[bool, Field(description="Only notes that lost >50% of their length")] = False,
        grep: Annotated[Optional[str], Field(description="New note contains this")] = None,
        introduced: Annotated[Optional[str], Field(description="New note contains this and the previous note did not")] = None,
        dropped: Annotated[Optional[str], Field(description="Previous note contained this and the new note does not")] = None,
        regex: bool = False,
        case_sensitive: bool = False,
        diff: Annotated[bool, Field(description="Word diff against the previous note")] = False,
        show_prev: bool = False,
        limit: Annotated[int, Field(ge=1, le=1000, description="Keep the most recent N")] = 100,
        format: Format = "text",
    ) -> str:
        """Every change to entity notes, oldest first (note_revisions.py): who
        (model/human/script), at which chapter, why. `introduced`/`dropped` find
        when a fact entered or left a note. Only meaningful from books 89-90 on;
        earlier books have little or no revision history."""
        from note_revisions import fetch_revisions, filter_revisions, find_entities, render_revision
        db = get_db()
        require_book(db, book_id)
        ids = None
        if entity or entity_id:
            found = find_entities(db, book_id, entity, entity_id)
            if not found:
                raise ToolError(f"No entity matches {entity or entity_id!r} in book {book_id}.")
            ids = [r["id"] for r in found]
        try:
            rows = fetch_revisions(db, book_id, entity_ids=ids, chapter_expr=chapters,
                                   no_chapter=no_chapter, author=author,
                                   shrink_only=shrink_only)
            rows = filter_revisions(rows, grep, introduced, dropped, regex=regex,
                                    ignore_case=not case_sensitive, limit=limit)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        if format == "json":
            return truncate(json_out({"count": len(rows), "revisions": rows}))
        if not rows:
            return ("No note revisions match. (Books before 89 carry little or no "
                    "revision history.)")
        show_entity = not ids or len(ids) > 1
        text = "\n".join(render_revision(r, diff=diff, show_prev=show_prev,
                                         show_entity=show_entity) for r in rows)
        return truncate(text + f"\n\n{len(rows)} revision(s)", hint="narrow or lower limit")
