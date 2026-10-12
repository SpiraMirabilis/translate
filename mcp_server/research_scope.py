"""Request-local limits for translation research, separate from review access.

HTTP callers of the read-only research service must supply scope. Review over
stdio or the full server retains its existing trusted-client behavior. Headers
are supplied by T9, not model tool arguments; this is not remote authentication.
"""
from contextvars import ContextVar
from dataclasses import dataclass

from mcp.server.fastmcp.exceptions import ToolError

from research_tools import RESEARCH_TOOLS, scope_number


@dataclass(frozen=True)
class ResearchScope:
    book_id: int
    chapter: int


scope = ContextVar("t9_research_scope", default=None)


def request_scope(ctx, *, read_only):
    try:
        request = ctx.request_context.request
    except (ValueError, LookupError, AttributeError):
        request = None  # stdio/direct library review calls
    headers = request.headers if request is not None else {}
    marked = any(key in headers for key in ("x-t9-caller", "x-t9-book", "x-t9-chapter"))
    if not marked and not (read_only and request is not None):
        return None
    if not read_only or headers.get("x-t9-caller") != "translation":
        raise ToolError("Translation research requires the read-only server and scope headers")
    try:
        return ResearchScope(scope_number(headers.get("x-t9-book")),
                             scope_number(headers.get("x-t9-chapter")))
    except ValueError as exc:
        raise ToolError(str(exc)) from exc


def verify_scope(current, db):
    """The boundary must identify a known book and stored or queued chapter."""
    if not db.get_book(current.book_id):
        raise ToolError("Research book scope could not be verified")
    with db._conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM chapters WHERE book_id = ? AND chapter_number = ? "
                    "UNION ALL SELECT 1 FROM queue WHERE book_id = ? AND chapter_number = ?",
                    (current.book_id, current.chapter, current.book_id, current.chapter))
        if cur.fetchone() is None:
            raise ToolError("Research chapter scope could not be verified")


def check_call(current, name, arguments, db):
    if current is None:
        return
    if name not in RESEARCH_TOOLS:
        raise ToolError("Tool is not available for translation research")
    try:
        book_id = scope_number((arguments or {}).get("book_id"))
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    if book_id != current.book_id:
        raise ToolError("Research book scope could not be verified")
    verify_scope(current, db)
    for key in ("chapter_number", "chapter", "as_of_chapter"):
        value = (arguments or {}).get(key)
        if value is not None:
            try:
                number = scope_number(value)
            except ValueError as exc:
                raise ToolError(str(exc)) from exc
            if number > current.chapter:
                raise ToolError("Research cannot read future chapters")
    if (arguments or {}).get("current_notes"):
        raise ToolError("Current notes are unavailable for translation research")
