"""Shared translation research configuration (no MCP SDK dependency)."""
import json
import re
import time
from urllib.parse import urlsplit, urlunsplit
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

MCP_SERVER_NAME = "t9"
RESEARCH_SCOPE_VERSION = 1
RESEARCH_TOOLS = (
    "t9_search_entities", "t9_list_entities", "t9_grep_book",
    "t9_get_chapter_summaries", "t9_get_chapter", "t9_entity_context",
    "t9_notes_as_of", "t9_search_chapters", "t9_get_book",
)
_reachable_cache = {}


class ResearchCompatibilityError(RuntimeError):
    """Reachable research service cannot enforce the required access limits."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def scope_number(value):
    """Positive decimal IDs only; reject bools, floats and ambiguous strings."""
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value)):
        raise ValueError("Research requires a positive book and chapter number")
    number = int(value)
    if number < 1:
        raise ValueError("Research requires a positive book and chapter number")
    return number


def valid_research_config(tools):
    try:
        scope_number(tools.get("book_id"))
        scope_number(tools.get("chapter_number"))
        url = urlsplit(tools.get("url", ""))
        return (url.scheme in ("http", "https") and bool(url.hostname)
                and not url.username and not url.password and not url.query and not url.fragment)
    except (AttributeError, TypeError, ValueError):
        return False


def _mcp_reachable(url):
    """Verify the configured server is read-only and enforces research scope.

    A TCP connection alone also succeeds against an old or full-access server.
    Probe its health contract, with a short timeout and a brief per-URL cache.
    Connection failures return False; incompatible responses raise so an upgrade
    cannot silently disable existing research. Never follow redirects.
    """
    now = time.monotonic()
    cached = _reachable_cache.get(url)
    if cached and now - cached[1] < 30:
        if isinstance(cached[0], ResearchCompatibilityError):
            raise cached[0]
        return cached[0]
    ok = False
    incompatibility = ResearchCompatibilityError(
        "Research MCP service is incompatible: expected read_only=true and "
        f"research_scope_version={RESEARCH_SCOPE_VERSION}. Restart the read-only "
        "MCP service after upgrading T9, then retry translation.")
    try:
        parsed = urlsplit(url)
        health = urlunsplit((parsed.scheme, parsed.netloc, "/health", "", ""))
        request = Request(health, headers={"Accept": "application/json"})
        with build_opener(_NoRedirect).open(request, timeout=0.5) as response:
            if response.geturl() == health:
                data = json.loads(response.read(4096))
                ok = (data.get("ok") is True and data.get("read_only") is True
                      and data.get("research_scope_version") == RESEARCH_SCOPE_VERSION)
        if not ok:
            ok = incompatibility
    except (HTTPError, ValueError, TypeError, AttributeError):
        ok = incompatibility
    except OSError:
        pass
    _reachable_cache[url] = (ok, now)
    if isinstance(ok, ResearchCompatibilityError):
        raise ok
    return ok


def mcp_tools_section(tools: dict) -> str:
    """System-prompt addendum telling the model which book it is on and that it
    is welcome to look things up. The per-book prompt carries only the chapter
    number, so without this the model could not pass book_id."""
    book_id = tools.get("book_id")
    title = tools.get("book_title") or ""
    ch = tools.get("chapter_number")
    where = f"chapter {ch}" if ch else "the current chapter"
    lookups = max(1, int(tools.get("max_turns") or 8) - 1)
    notes_hint = (f"pass chapter={ch} so they read as of this chapter" if ch
                  else "pass the current chapter so they read as of it")
    try:
        n = int(ch)
    except (TypeError, ValueError):
        n = 0
    summaries_hint = (f' (chapters="<{n}" for everything so far, or a range such as '
                      f'"{max(1, n - 20)}-{n - 1}")' if n > 1 else "")
    return (
        "\n\nRESEARCH TOOLS (read-only):\n"
        f'You are translating book_id={book_id} ("{title}"), {where}. You have tools '
        "that look up this book's own records, and you are encouraged to use them "
        "whenever they would make the translation more accurate or more consistent "
        "with earlier chapters. Lookups are quick and cheap; a wrong or inconsistent "
        "rendering is not.\n"
        "- t9_search_entities / t9_list_entities: the glossary within this "
        "chapter boundary. The PRE-TRANSLATED "
        "ENTITIES above list only terms matched in this text, so search here for a "
        "name, title, place, technique, rank or item that is not listed there. "
        "Gender and category reflect current truth; renderings stay consistent "
        "book-wide. Notes use T9's existing point-in-time history, including "
        "legacy notes. Filter by category when useful.\n"
        "- t9_get_book: this book's metadata and entity categories.\n"
        "- t9_search_chapters: search earlier source and translated chapter text.\n"
        "- t9_grep_book: how an earlier chapter rendered a recurring phrase, form of "
        "address, rank or title (field=\"both\" to see source and translation).\n"
        "- t9_get_chapter_summaries: short plot summaries of earlier chapters"
        f"{summaries_hint} — the fast way to find who a returning character is, "
        "where an earlier event happened, or which chapter to open with "
        "t9_get_chapter.\n"
        "- t9_get_chapter / t9_entity_context: what happened in an event the text "
        "refers back to, or how a term was used before.\n"
        f"- t9_notes_as_of: character and entity notes; {notes_hint}.\n"
        "Good reasons to look something up: a proper noun or term missing from the "
        "glossary above, a callback to an earlier scene, a recurring expression whose "
        "established wording you are unsure of, or ambiguity about who a pronoun or "
        "title refers to.\n"
        f"- Always pass book_id={book_id}. Do not read chapters after {where}.\n"
        f"- At most {lookups} lookups per response.\n"
        "- If research is unavailable, finish using the supplied text and glossary.\n"
        "- Make any lookups BEFORE writing your answer, and write nothing else: your "
        "final message must be the response in the required format and nothing more."
    )
