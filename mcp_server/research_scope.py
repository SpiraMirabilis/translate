"""Request-local limits for translation research, separate from review access.

HTTP callers of the read-only research service must supply scope. Review over
stdio or the full server retains its existing trusted-client behavior. Headers
are supplied by T9, not model tool arguments; this is not remote authentication.
"""
from contextvars import ContextVar
from dataclasses import dataclass
import re

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
    if (arguments or {}).get("category"):
        raise ToolError("Historic entity categories are unavailable for translation research")


def _witnessed_renderings(entities, rows):
    """Scan each bounded chapter once for candidate source keys.

    Literal alternatives prefer the longest key. Check its shorter prefixes and
    resume at start+1 so overlapping names retain the old substring semantics.
    A rendering must still occur in the SAME chapter as its source key. Chapter
    content and verification results are not cached across requests.
    """
    from footnotes import content_to_list

    remaining = {}
    for ent in entities:
        if ent["untranslated"] and ent["translation"]:
            remaining.setdefault(ent["untranslated"], set()).add(ent["translation"])
    if not remaining:
        return set()
    keys = set(remaining)
    initials = {}
    for key in sorted(keys, key=len, reverse=True):
        initials.setdefault(key[0], []).append(re.escape(key[1:]))
    pattern = re.compile("|".join(re.escape(first) + "(?:" + "|".join(tails) + ")"
                                  for first, tails in initials.items()))
    prefixes = {key: [key[:n] for n in range(1, len(key) + 1) if key[:n] in keys] for key in keys}
    witnessed = set()
    for row in rows:
        source = "\n".join(content_to_list(row["untranslated_content"]))
        translated = "\n".join(content_to_list(row["translated_content"]))
        chapter_keys, pos = set(), 0
        while match := pattern.search(source, pos):
            chapter_keys.update(prefixes[match.group()])
            pos = match.start() + 1
        for key in chapter_keys:
            renderings = remaining.get(key, set())
            found = {value for value in renderings if value in translated}
            witnessed.update((key, value) for value in found)
            renderings.difference_update(found)
            if key in remaining and not renderings:
                del remaining[key]
        if not remaining:
            break
    return witnessed


def verified_entities(db, *, chapter=None, matches=None):
    """Only book-local renderings witnessed in source AND translated prose.

    Origin metadata is a floor, not proof. Exclude unknown/global/future entities
    and unversioned gender, category, last-chapter and correction metadata. A
    current rendering must also occur in a stored chapter inside the boundary.
    Notes require a complete, dated, ordered revision chain; no legacy fallback.
    """
    current = scope.get()
    boundary = current.chapter if chapter is None else min(chapter, current.chapter)
    with db._conn(dict_rows=True) as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, untranslated, translation, origin_chapter, note "
                    "FROM entities WHERE book_id = ? AND origin_chapter >= 1 "
                    "AND origin_chapter <= ? ORDER BY untranslated, id",
                    (current.book_id, boundary))
        entities = [dict(row) for row in cur.fetchall()]
        if matches is not None:
            entities = [ent for ent in entities if matches(ent)]
        if not entities:
            return []
        cur.execute("SELECT untranslated_content, translated_content FROM chapters "
                    "WHERE book_id = ? AND chapter_number >= 1 AND chapter_number <= ?",
                    (current.book_id, boundary))
        witnessed = _witnessed_renderings(entities, iter(cur.fetchone, None))
        cur.execute("SELECT entity_id, chapter_number, previous_note, new_note "
                    "FROM entity_note_revisions WHERE book_id = ? ORDER BY id",
                    (current.book_id,))
        revisions = {}
        for row in cur.fetchall():
            revisions.setdefault(row["entity_id"], []).append(dict(row))

    out = []
    for ent in entities:
        key, rendering = ent["untranslated"], ent["translation"]
        if (key, rendering) not in witnessed:
            continue
        item = {"untranslated": key, "translation": rendering,
                "origin_chapter": ent["origin_chapter"]}
        history = revisions.get(ent["id"], [])
        note, previous, last_chapter = None, None, 0
        verified = bool(history)
        for revision in history:
            number = revision["chapter_number"]
            if (number is None or number < max(1, last_chapter)
                    or revision["previous_note"] != previous):
                verified = False
                break
            if number <= boundary:
                note = revision["new_note"]
            previous, last_chapter = revision["new_note"], number
        if verified and previous == ent["note"] and note:
            item["note"] = note
        out.append(item)
    return out
