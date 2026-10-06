"""Real footnotes: list, add, delete, re-anchor. Never guarded (the translator only appends)."""
from typing import Annotated, Literal, Optional

from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel, ConfigDict, Field

from ..deps import db as get_db
from ..formatting import json_out, page_footer, paginate, truncate
from ._common import CHAPTERS_DESC, Format, chapter_predicate, require_book, tool


class FootnoteSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    term: str = Field(min_length=1, description="Anchor: exact, case-sensitive substring of the translated line")
    body: str = Field(min_length=1, description="Definition text")


def _chapter_id(db, book_id: int, chapter: int) -> int:
    for c in db.list_chapters(book_id):
        if c["chapter"] == chapter:
            return c["id"]
    raise ToolError(f"Book {book_id} has no stored chapter {chapter}.")


def register(mcp) -> None:

    @tool(mcp, "t9_list_footnotes", "List footnotes", read_only=True)
    def t9_list_footnotes(
        book_id: int,
        chapters: Annotated[Optional[str], Field(description=CHAPTERS_DESC)] = None,
        orphans_only: Annotated[bool, Field(description="Only footnotes whose anchor was not found at the last render")] = False,
        limit: Annotated[int, Field(ge=1, le=1000)] = 200,
        offset: Annotated[int, Field(ge=0)] = 0,
        format: Format = "text",
    ) -> str:
        """A book's footnotes: row id (for delete/reanchor), chapter, rendered [n],
        anchor, status, body, and the ↳ sentence the marker sits in (translated side)."""
        from list_footnotes import collect_footnotes
        db = get_db()
        require_book(db, book_id)
        pred = chapter_predicate(chapters)
        rows = db.get_book_footnotes(book_id, status="orphaned" if orphans_only else None) or []
        if pred:
            rows = [r for r in rows if pred(r["chapter_number"])]
        page, meta = paginate(rows, limit, offset)
        rendered = {}
        for cn in sorted({r["chapter_number"] for r in page if not r.get("is_source")}):
            ch = db.get_chapter(book_id=book_id, chapter_number=cn)
            if ch:
                rendered[cn] = collect_footnotes(ch)
        for r in page:
            match = next((f for f in rendered.get(r["chapter_number"], [])
                          if f["body"].strip() == (r.get("body") or "").strip()), None)
            r["number"] = match["number"] if match else None
            r["sentence"] = match["sentence"] if match else None
        if format == "json":
            return truncate(json_out({**meta, "footnotes": page}))
        if not page:
            return "No footnotes match."
        out = []
        for r in page:
            side = " (source)" if r.get("is_source") else ""
            num = f"[{r['number']}]" if r.get("number") else "[?]"
            out.append(f"id {r['id']}  ch{r['chapter_number']}{side} {num} "
                       f"{r['anchor']!r} ({r['status']})\n   {r['body']}")
            if r.get("sentence"):
                out.append(f"   ↳ {r['sentence']}")
        return truncate("\n".join(out + [page_footer(meta)]))

    @tool(mcp, "t9_add_footnotes", "Add footnotes", read_only=False, idempotent=True)
    def t9_add_footnotes(
        book_id: int,
        footnotes: Annotated[list[FootnoteSpec], Field(min_length=1, max_length=1000)],
        chapter: Annotated[Optional[int], Field(description="Force this chapter; default is each term's first occurrence book-wide")] = None,
        source: Annotated[bool, Field(description="Footnote the SOURCE side instead of the translation")] = False,
        dry_run: bool = True,
    ) -> str:
        """Place footnotes at each term's first mention (add_footnotes.py) and
        re-render the chapters. The dry run shows chapter, line, the [n] each gets,
        and warnings (e.g. an anchor landing in the heading, line 0 — pick a longer
        anchor or force a chapter). A term whose exact body is already in the book
        is skipped, so a re-run is safe. Anchors are exact and case-sensitive;
        plurals are not automatic."""
        from add_footnotes import apply_footnote_plan, load_book_text, plan_footnotes
        db = get_db()
        require_book(db, book_id)
        terms = [f.term for f in footnotes]
        dupes = sorted({t for t in terms if terms.count(t) > 1})
        if dupes:
            raise ToolError(f"Duplicate terms: {dupes}")
        text = load_book_text(db, book_id, source=source)
        try:
            plan, skipped, not_found = plan_footnotes(
                {f.term: f.body for f in footnotes}, text["chapters"], text["content"],
                text["full_text"], chapter=chapter)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        written = None
        if not dry_run and plan:
            try:
                written = apply_footnote_plan(db, book_id, plan, text["chapter_id_by_num"],
                                              1 if source else 0)
            except (ValueError, RuntimeError) as exc:
                raise ToolError(f"Footnote write failed: {exc}") from exc
        warnings = [f"ch{p['chapter']} {p['anchor']!r}: {p['warning']}"
                    for p in plan if p.get("warning")]
        return truncate(json_out({
            "dry_run": dry_run, "written": written, "placed": len(plan),
            "plan": [{k: p.get(k) for k in ("chapter", "anchor", "line_idx", "number",
                                            "renumbers_existing", "warning")} for p in plan],
            "skipped_existing_body": skipped, "not_found": not_found, "warnings": warnings}))

    @tool(mcp, "t9_delete_footnotes", "Delete footnotes", read_only=False, destructive=True)
    def t9_delete_footnotes(
        book_id: int,
        chapter: int,
        number: Annotated[Optional[int], Field(description="Rendered [n]")] = None,
        anchor: Optional[str] = None,
        body_contains: Annotated[Optional[str], Field(description="Case-insensitive substring of the body")] = None,
        footnote_id: Optional[int] = None,
        all: Annotated[bool, Field(description="Every footnote on this side of the chapter")] = False,
        source: bool = False,
        apply: bool = False,
    ) -> str:
        """Delete footnote(s) from ONE chapter by exactly one selector and
        re-render it (delete_footnote.py) — markers and definitions of the last
        remaining footnote are stripped too. Dry run unless apply=true."""
        from delete_footnote import rerender_side, resolve_targets
        db = get_db()
        require_book(db, book_id)
        chapter_id = _chapter_id(db, book_id, chapter)
        is_source = 1 if source else 0
        rows = db.get_chapter_footnotes(chapter_id, is_source=is_source) or []
        try:
            targets = resolve_targets(rows, db.get_chapter(chapter_id=chapter_id), is_source,
                                      number=number, anchor=anchor,
                                      body_contains=body_contains,
                                      footnote_id=footnote_id, all_=all)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        brief = [{"id": r["id"], "anchor": r.get("anchor"), "body": r.get("body")}
                 for r in targets]
        if not targets:
            return json_out({"matched": 0, "existing_on_side": [
                {"id": r["id"], "anchor": r.get("anchor")} for r in rows]})
        deleted = None
        if apply:
            deleted = sum(1 for r in targets if db.delete_footnote(r["id"]))
            rerender_side(db, chapter_id, book_id, is_source)
        return json_out({"dry_run": not apply, "matched": len(targets), "deleted": deleted,
                         "targets": brief})

    @tool(mcp, "t9_reanchor_footnote", "Re-anchor a footnote", read_only=False)
    def t9_reanchor_footnote(
        book_id: int,
        footnote_id: int,
        anchor: Annotated[str, Field(min_length=1, description="The term as it now appears in the translation")],
    ) -> str:
        """Point a footnote (typically an orphan left by a prose edit) at a new
        anchor and re-render its chapter. Reports whether it is anchored now."""
        from list_footnotes import reanchor_footnote
        db = get_db()
        res = reanchor_footnote(db, book_id, footnote_id, anchor)
        if not res["ok"]:
            raise ToolError(res["error"])
        return json_out(res)
