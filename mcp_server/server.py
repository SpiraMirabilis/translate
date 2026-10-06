"""build_server(): the FastMCP instance with every t9_* tool registered."""
from __future__ import annotations

from typing import Any, Optional

import time

from mcp.server.fastmcp import FastMCP

from . import usage
from .deps import DEFAULT_ADMIN_URL, AdminClient, AppContext, set_app


class T9MCP(FastMCP):
    """FastMCP that writes every tool call to the usage log."""

    t9_read_only = False

    async def call_tool(self, name, arguments):
        started = time.monotonic()
        entry = {"event": "call", "mode": "readonly" if self.t9_read_only else "full",
                 "tool": name,
                 "args": dict(arguments or {}),
                 "book_id": (arguments or {}).get("book_id")}
        try:
            entry.update(usage.caller_headers(self.get_context()))
        except Exception:
            pass
        try:
            result = await super().call_tool(name, arguments)
        except Exception as exc:
            usage.record({**entry, "ok": False, "error": str(exc)[:300],
                          "ms": round((time.monotonic() - started) * 1000)})
            raise
        size = 0
        try:
            size = sum(len(getattr(c, "text", "") or "") for c in result)
        except TypeError:
            pass
        usage.record({**entry, "ok": True, "result_chars": size,
                      "ms": round((time.monotonic() - started) * 1000)})
        return result

INSTRUCTIONS = """\
T9 — Chinese web-novel translation database: review, entity repair and footnotes.

Safety rules the server enforces or you must follow:
- Entity-DB writes (translation records, categories, deletes, notes, gender, origin
  backfill) are REFUSED while that book is translating. Per batch: find fixes with the
  read tools → t9_pause_translation → apply entity changes → t9_resume_translation →
  then prose/footnote edits (those are never blocked). force=true overrides the guard;
  use it only when you know nothing is translating the book.
- Every write tool defaults to a dry run (dry_run=true or apply=false). Read the
  preview, then call again to apply.
- Substitution is book-wide, case-INSENSITIVE and case-preserving. If two entities share
  the old English, or the old English is a common word, use mode="safer" (only chapters
  whose source mentions the term) or mode="none" (record only). In a cascade, fix the
  longer phrase first; later corrections reporting 0 substitutions is then normal.
- Footnotes: 0-4 per chapter, first mention book-wide; the anchor is a case-sensitive
  exact substring of the translated line (quote-internal punctuation included, plurals
  not automatic). Never anchor in the chapter heading (line 0). The [n] marker hops
  closing brackets like 》」) automatically — never put the bracket in the anchor.
  A referent the translation paraphrased away gets no footnote.
- origin_chapter filters are a floor until t9_backfill_origin_chapter runs; each moved
  origin opens a drift window [new, old) worth auditing.
- The note-revision log is only meaningful from books 89-90 on.
- t9_replace_in_chapters undo is one level, per book, and lives in THIS server process
  (the web GUI's undo is separate).
"""

READ_ONLY_INSTRUCTIONS = """\
T9 — Chinese web-novel translation database, read-only. Look up books, chapters, entity
records (translation, category, gender, note), note history, and footnotes. Nothing here
writes.
"""


def build_server(db: Any = None, admin: Any = None, config: Any = None, logger: Any = None,
                 host: str = "127.0.0.1", port: int = 8765,
                 admin_url: str = DEFAULT_ADMIN_URL, read_only: bool = False,
                 stateless_http: bool = False) -> FastMCP:
    """Assemble the server. Tests inject a SQLite `db` and a fake `admin`;
    production passes neither and the DB is built on the first tool call.

    read_only=True registers only the getters (readOnlyHint tools) — for
    handing to a model mid-translation, which may research the glossary but
    must never write to it.
    """
    set_app(AppContext(db=db, admin=admin or AdminClient(admin_url),
                       config=config, logger=logger))
    mcp = T9MCP("t9", instructions=READ_ONLY_INSTRUCTIONS if read_only else INSTRUCTIONS,
                host=host, port=port, stateless_http=stateless_http,
                json_response=stateless_http)
    mcp.t9_read_only = read_only

    @mcp.custom_route("/health", methods=["GET"])
    async def health(request):
        from starlette.responses import JSONResponse
        return JSONResponse({"ok": True, "read_only": read_only})

    from .tools import register_all
    register_all(mcp)
    return mcp
