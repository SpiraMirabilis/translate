"""Footnote candidates: the scanner's review queue."""
from typing import Annotated, Literal, Optional

from mcp.server.fastmcp.exceptions import ToolError
from pydantic import Field

from ..deps import db as get_db
from ..formatting import json_out, page_footer, paginate, truncate
from ._common import CHAPTERS_DESC, Format, chapter_predicate, require_book, tool

Status = Literal["pending", "accepted", "rejected"]


def register(mcp) -> None:
    register_reports(mcp)

    @tool(mcp, "t9_list_footnote_candidates", "List footnote candidates", read_only=True)
    def t9_list_footnote_candidates(
        book_id: int,
        status: Optional[Status] = "pending",
        chapters: Annotated[Optional[str], Field(description=CHAPTERS_DESC)] = None,
        first_mentions_only: Annotated[bool, Field(description="Drop repeats of a term already proposed at an earlier chapter")] = False,
        limit: Annotated[int, Field(ge=1, le=500)] = 100,
        offset: Annotated[int, Field(ge=0)] = 0,
        format: Format = "text",
    ) -> str:
        """Candidate footnotes the scanner proposed: id, chapter, term (zh → en),
        proposed body, and the source sentence. Accepting a candidate places
        nothing — export accepted ones and add them with t9_add_footnotes."""
        db = get_db()
        require_book(db, book_id)
        pred = chapter_predicate(chapters)
        rows = db.list_footnote_candidates(book_id, status=status)
        if pred:
            rows = [r for r in rows if pred(r["chapter_number"])]
        if first_mentions_only:
            from footnote_scan_core import dedupe_first_mention
            rows, _ = dedupe_first_mention(rows)
        page, meta = paginate(rows, limit, offset)
        if format == "json":
            return json_out({**meta, "candidates": page})
        if not page:
            return "No candidates match."
        out = []
        for r in page:
            out.append(f"#{r['id']} ch{r['chapter_number']} [{r['status']}] "
                       f"{r['term_zh']} → {r.get('term_en') or '?'}\n"
                       f"   body: {r.get('body') or ''}\n   src:  {r.get('sentence') or ''}")
        return truncate("\n".join(out + [page_footer(meta)]))

    @tool(mcp, "t9_decide_footnote_candidates", "Accept/reject candidates", read_only=False)
    def t9_decide_footnote_candidates(
        book_id: int,
        ids: Annotated[list[int], Field(min_length=1, max_length=500)],
        decision: Status,
    ) -> str:
        """Set candidates to accepted / rejected / pending. Every id must belong to
        book_id. A decision survives retranslation (reviewed rows are preserved
        when a chapter is rescanned)."""
        db = get_db()
        require_book(db, book_id)
        wrong = []
        for cid in ids:
            row = db.get_footnote_candidate(cid)
            if not row or row["book_id"] != book_id:
                wrong.append(cid)
        if wrong:
            raise ToolError(f"Candidate id(s) not in book {book_id}: {wrong}. Nothing changed.")
        n = db.set_footnote_candidates_status(list(dict.fromkeys(ids)), decision)
        return json_out({"updated": n, "decision": decision})


def register_reports(mcp) -> None:

    @tool(mcp, "t9_footnote_candidate_report", "Footnote candidate report", read_only=True)
    def t9_footnote_candidate_report(
        book_id: int,
        chapters: Annotated[Optional[str], Field(description=CHAPTERS_DESC)] = None,
        all_rows: Annotated[bool, Field(description="Include repeat mentions, not just first mentions")] = False,
    ) -> str:
        """The review report (footnote_scan.py --report): first mentions with
        status, whether the term is already footnoted, and the other chapters that
        proposed it."""
        from footnote_scan import build_candidate_report
        db = get_db()
        require_book(db, book_id)
        try:
            rep = build_candidate_report(db, book_id, chapters, all_=all_rows)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        return truncate(rep["text"], hint="narrow chapters")

    @tool(mcp, "t9_prune_footnote_candidates", "Prune unverified candidates",
          read_only=False, destructive=True)
    def t9_prune_footnote_candidates(
        book_id: int,
        chapters: Annotated[Optional[str], Field(description=CHAPTERS_DESC)] = None,
        apply: bool = False,
    ) -> str:
        """Delete candidates whose Chinese term does not occur in their chapter's
        source (hallucinations), any status. Dry run unless apply=true."""
        from footnote_scan import find_unverified_candidates, prune_candidates
        db = get_db()
        require_book(db, book_id)
        try:
            found = find_unverified_candidates(db, book_id, chapters)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        deleted = prune_candidates(db, book_id, found["doomed"]) if apply else None
        return truncate(json_out({
            "dry_run": not apply, "deleted": deleted, "checked": found["checked"],
            "by_status": found["by_status"], "no_source": found["no_source"],
            "doomed": [{k: r.get(k) for k in ("id", "chapter_number", "term_zh", "term_en",
                                              "status")} for r in found["doomed"]]}))

    @tool(mcp, "t9_export_footnote_candidates", "Export candidates for placement",
          read_only=True)
    def t9_export_footnote_candidates(
        book_id: int,
        chapters: Annotated[Optional[str], Field(description=CHAPTERS_DESC)] = None,
    ) -> str:
        """Non-rejected first-mention candidates as a `footnotes` list ready for
        t9_add_footnotes, minus terms already footnoted. `not_in_translation`
        lists terms whose English (the scanner's rendering) does not occur in the
        translation — fix the term to the translation's wording before adding."""
        from footnote_scan import build_export_map
        db = get_db()
        require_book(db, book_id)
        try:
            mapping, warnings = build_export_map(db, book_id, chapters)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        return truncate(json_out({"footnotes": [{"term": t, "body": b}
                                                for t, b in mapping.items()],
                                  **warnings}))
