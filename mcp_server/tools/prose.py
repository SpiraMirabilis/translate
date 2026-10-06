"""Prose: grep, search, replace, undo. Never guarded — the translator only appends chapters."""
import json
import re
from typing import Annotated, Literal, Optional

from mcp.server.fastmcp.exceptions import ToolError
from pydantic import Field

from ..deps import db as get_db
from ..formatting import json_out, truncate
from ._common import CHAPTERS_DESC, chapter_numbers, chapter_predicate, require_book, tool


def _compile(query: str, regex: bool):
    if not regex:
        return None
    try:
        return re.compile(query, re.IGNORECASE)
    except re.error as exc:
        raise ToolError(f"Invalid regex {query!r}: {exc}") from exc


def preview_replace(db, book_id, query, replacement, numbers, regex, include_titles,
                    max_samples=30):
    """What replace_in_chapters would do, without writing: same matcher
    (db._replace_once), same row selection."""
    pattern = _compile(query, regex)
    sql = ("SELECT chapter_number, translated_content, title FROM chapters "
           "WHERE book_id = ? ORDER BY chapter_number")
    with db._conn() as conn:
        cur = conn.cursor()
        cur.execute(sql, (book_id,))
        rows = cur.fetchall()
    wanted = set(numbers) if numbers is not None else None
    per_chapter, samples, total, title_total = [], [], 0, 0
    for num, raw, title in rows:
        if wanted is not None and num not in wanted:
            continue
        try:
            lines = json.loads(raw) if raw else []
        except (json.JSONDecodeError, TypeError):
            lines = raw.split("\n") if raw else []
        n = 0
        for i, line in enumerate(lines):
            new, c = db._replace_once(line, query, replacement, pattern)
            if c:
                n += c
                if len(samples) < max_samples:
                    samples.append({"chapter": num, "line": i, "before": line, "after": new})
        t = 0
        if include_titles:
            new_title, t = db._replace_once(title, query, replacement, pattern)
            if t and len(samples) < max_samples:
                samples.append({"chapter": num, "line": "title", "before": title,
                                "after": new_title})
        if n or t:
            per_chapter.append({"chapter": num, "replacements": n, "title_replacements": t})
            total += n
            title_total += t
    return {"affected_chapters": len(per_chapter), "total_replacements": total,
            "title_replacements": title_total, "chapters": per_chapter, "samples": samples}


def register(mcp) -> None:
    register_grep(mcp)

    @tool(mcp, "t9_search_chapters", "Search chapters", read_only=True)
    def t9_search_chapters(
        book_id: int,
        query: Annotated[str, Field(min_length=1)],
        scope: Literal["translated", "untranslated", "both"] = "both",
        regex: bool = False,
        max_chapters: Annotated[int, Field(ge=1, le=500)] = 50,
    ) -> str:
        """The web GUI's book-wide search: per-chapter match counts with matched
        lines. Case-INSENSITIVE, including regex mode. For line-level grep with
        context, labels and queued chapters use t9_grep_book."""
        db = get_db()
        require_book(db, book_id)
        _compile(query, regex)
        hits = db.search_book_chapters(book_id, query, scope=scope, is_regex=regex)
        total = sum(h.get("match_count", 0) for h in hits)
        lines = [f"{total} match(es) in {len(hits)} chapter(s)"]
        for h in hits[:max_chapters]:
            lines.append(f"\nch{h['chapter_number']} {h.get('title') or ''} — {h['match_count']}")
            for m in h.get("matches", [])[:10]:
                lines.append(f"  [{m.get('field')} {m.get('line')}] {m.get('text')}")
            if h.get("match_count", 0) > 10:
                lines.append(f"  … {h['match_count'] - 10} more")
        if len(hits) > max_chapters:
            lines.append(f"\n… {len(hits) - max_chapters} more chapters (raise max_chapters)")
        return truncate("\n".join(lines))

    @tool(mcp, "t9_replace_in_chapters", "Replace in chapters", read_only=False,
          destructive=True, idempotent=False)
    def t9_replace_in_chapters(
        book_id: int,
        query: Annotated[str, Field(min_length=1)],
        replacement: str,
        chapters: Annotated[Optional[str], Field(description=CHAPTERS_DESC)] = None,
        regex: bool = False,
        include_titles: bool = True,
        dry_run: bool = True,
    ) -> str:
        """Replace text in the TRANSLATED prose (and chapter titles) of a book.
        Matching is case-INSENSITIVE in both modes — for a case-sensitive regex
        wrap it as `(?-i:...)`. Plain mode is a literal substring, so it also hits
        plurals and longer words containing it. Does not touch entity records,
        footnote rows (renaming an anchor orphans its footnote at the next
        re-render) or the chapter-terms index. dry_run (default) reports
        counts and sample before/after lines. One-level undo per book, held in
        this server process: t9_undo_replace."""
        db = get_db()
        require_book(db, book_id)
        numbers = chapter_numbers(db, book_id, chapters)
        if numbers is not None and not numbers:
            raise ToolError(f"No stored chapters match {chapters!r}.")
        if dry_run:
            out = preview_replace(db, book_id, query, replacement, numbers, regex, include_titles)
            out["dry_run"] = True
            return truncate(json_out(out))
        _compile(query, regex)
        res = db.replace_in_chapters(book_id, query, replacement, chapter_numbers=numbers,
                                     is_regex=regex, include_titles=include_titles)
        return json_out({"dry_run": False, **res})

    @tool(mcp, "t9_undo_replace", "Undo the last replace", read_only=False,
          destructive=True, idempotent=False)
    def t9_undo_replace(book_id: int) -> str:
        """Restore the chapters (content and titles) changed by this server's last
        t9_replace_in_chapters on the book. One level; lost if the server restarts."""
        db = get_db()
        if not db.has_replace_undo(book_id):
            raise ToolError(f"No replace to undo for book {book_id} in this server process.")
        res = db.undo_replace(book_id)
        return json_out(res or {"restored_chapters": 0})


def register_grep(mcp) -> None:

    @tool(mcp, "t9_grep_book", "Grep a book", read_only=True)
    def t9_grep_book(
        book_id: int,
        pattern: Annotated[str, Field(min_length=1, description="Regex (or literal with fixed=true)")],
        field: Annotated[Literal["src", "en", "both"], Field(description="src = Chinese source, en = translation")] = "src",
        fixed: bool = False,
        ignore_case: bool = False,
        chapters: Annotated[Optional[str], Field(description=CHAPTERS_DESC)] = None,
        context: Annotated[int, Field(ge=0, le=5, description="Lines of context around each hit")] = 0,
        titles: Annotated[bool, Field(description="Also search chapter titles")] = False,
        include_queue: Annotated[bool, Field(description="Also search queued (untranslated) chapters' source")] = True,
        mode: Annotated[Literal["lines", "count", "chapters"], Field(description="lines = every hit; count = hits per chapter; chapters = chapter numbers only")] = "lines",
        max_hits: Annotated[int, Field(ge=1, le=5000)] = 500,
    ) -> str:
        """Line-level grep across a book (grep_book.py). Each hit is
        `chN[ (queued)][ field] [matched labels] [line] text`. Searches queued
        chapters' source too, so a term can be checked ahead of translation."""
        from grep_book import build_matcher, collect_chapters, format_hits, grep_chapters
        db = get_db()
        require_book(db, book_id)
        fields = ["src", "en"] if field == "both" else [field]
        try:
            matcher = build_matcher(pattern, fixed, ignore_case)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        pred = chapter_predicate(chapters)
        chs = collect_chapters(db, book_id, fields, include_queue)
        hits = grep_chapters(chs, matcher, fields, chapter_filter=pred, titles=titles)
        total, n_chapters = len(hits), len({h["chapter_number"] for h in hits})
        shown = hits[:max_hits] if mode == "lines" else hits
        text = format_hits(shown, chs, context=context, count=mode == "count",
                           files_only=mode == "chapters")
        summary = f"{total} match(es) in {n_chapters} chapter(s)"
        if mode == "lines" and total > max_hits:
            summary += f" — showing the first {max_hits}; raise max_hits or narrow chapters"
        return truncate((text.rstrip("\n") + "\n\n" if text else "") + summary)
