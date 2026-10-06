"""Shared bits for tool modules: annotations, lookups, progress."""

from typing import Literal, Optional

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

Format = Literal["text", "json"]


def ann(title: str, *, read_only: bool, destructive: bool = False,
        idempotent: bool = True, open_world: bool = False) -> ToolAnnotations:
    return ToolAnnotations(title=title, readOnlyHint=read_only,
                           destructiveHint=destructive, idempotentHint=idempotent,
                           openWorldHint=open_world)


def tool(mcp, name: str, title: str, **flags):
    """@mcp.tool with our defaults: text output only (no duplicated structured
    copy of a string result) and annotations on every tool.

    On a read-only server (build_server(read_only=True) — what a translation
    run's model gets) tools that write are simply not registered, so they are
    neither listed nor callable.
    """
    if getattr(mcp, "t9_read_only", False) and not flags.get("read_only"):
        return lambda fn: fn
    return mcp.tool(name=name, annotations=ann(title, **flags), structured_output=False)


def require_book(db, book_id: int) -> dict:
    book = db.get_book(book_id)
    if not book:
        raise ToolError(f"No book with id {book_id}. Use t9_list_books to find it.")
    return book


def entity_rows(db, book_id: int, untranslated: str, category: Optional[str] = None) -> list[dict]:
    """Every entity row (book-scoped first, then global) with this exact key."""
    sql = ("SELECT id, category, untranslated, translation, gender, note, origin_chapter, "
           "book_id FROM entities WHERE untranslated = ? AND (book_id = ? OR book_id IS NULL)")
    params: list = [untranslated, book_id]
    if category:
        sql += " AND category = ?"
        params.append(category)
    sql += " ORDER BY CASE WHEN book_id IS NULL THEN 1 ELSE 0 END, id"
    with db._conn(dict_rows=True) as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def resolve_entity(db, book_id: int, untranslated: str, category: Optional[str] = None) -> dict:
    """Exactly one entity for this key, or a ToolError that says how to narrow it."""
    rows = entity_rows(db, book_id, untranslated, category)
    book_rows = [r for r in rows if r["book_id"] is not None]
    rows = book_rows or rows
    if not rows:
        where = f" in category '{category}'" if category else ""
        raise ToolError(f"No entity '{untranslated}'{where} in book {book_id}. "
                        "Use t9_search_entities to find the exact key.")
    if len(rows) > 1:
        cats = ", ".join(sorted({r["category"] for r in rows}))
        raise ToolError(f"'{untranslated}' is ambiguous in book {book_id} (categories: {cats}); "
                        "pass category.")
    return rows[0]


async def report(ctx: Optional[Context], progress: float, total: Optional[float] = None,
                 message: Optional[str] = None) -> None:
    """ctx.report_progress that is a no-op outside a client session (tests)."""
    if ctx is None:
        return
    try:
        await ctx.report_progress(progress, total, message)
    except (ValueError, LookupError, AttributeError):
        pass


CHAPTERS_DESC = ("Chapter spec: comma-separated N, N-M, >N, >=N, <N, <=N (e.g. '1-20,45,>900'); "
                 "omit for all chapters")


def chapter_predicate(spec: Optional[str]):
    """footnote_scan.parse_chapter_spec as a ToolError-raising predicate (None = all)."""
    from footnote_scan import parse_chapter_spec
    try:
        return parse_chapter_spec(spec)
    except ValueError as exc:
        raise ToolError(f"Bad chapters spec {spec!r}: {exc}") from exc


def chapter_numbers(db, book_id: int, spec: Optional[str]) -> Optional[list[int]]:
    """Stored chapter numbers matching `spec`, or None for all chapters."""
    pred = chapter_predicate(spec)
    if pred is None:
        return None
    return [c["chapter"] for c in db.list_chapters(book_id) if pred(c["chapter"])]
