"""Footnote candidates: the scanner's review queue."""
from typing import Annotated, Literal, Optional

from mcp.server.fastmcp.exceptions import ToolError
from pydantic import Field

from ..deps import db as get_db
from ..formatting import json_out, page_footer, paginate, truncate
from ._common import CHAPTERS_DESC, Format, chapter_predicate, require_book, tool

Status = Literal["pending", "accepted", "rejected"]
# "applied" = auto-placed by the inline scan, awaiting curation. Listable, but
# never a decision: it is set only by the auto-apply code.
ListStatus = Literal["pending", "applied", "accepted", "rejected"]


def register(mcp) -> None:
    register_reports(mcp)

    @tool(mcp, "t9_list_footnote_candidates", "List footnote candidates", read_only=True)
    def t9_list_footnote_candidates(
        book_id: int,
        status: Optional[ListStatus] = "pending",
        chapters: Annotated[Optional[str], Field(description=CHAPTERS_DESC)] = None,
        first_mentions_only: Annotated[bool, Field(description="Drop repeats of a term already proposed at an earlier chapter")] = False,
        limit: Annotated[int, Field(ge=1, le=500)] = 100,
        offset: Annotated[int, Field(ge=0)] = 0,
        format: Format = "text",
    ) -> str:
        """Candidate footnotes the scanner proposed: id, chapter, term (zh → en),
        proposed body, and the source sentence. status="applied" lists the ones
        the inline scan already placed in the chapter (footnote id shown) — curate
        those with t9_decide_footnote_candidates. Accepting an UNPLACED candidate
        places nothing: place it with t9_place_footnote_candidate, or export and
        add with t9_add_footnotes."""
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
            placed = f" (footnote #{r['footnote_id']})" if r.get("footnote_id") else ""
            out.append(f"#{r['id']} ch{r['chapter_number']} [{r['status']}]{placed} "
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
        when a chapter is rescanned).

        On a candidate the inline scan already placed (status "applied"):
        accepted keeps its footnote, rejected DELETES the footnote from the
        chapter (re-rendered), pending returns it to "applied"."""
        db = get_db()
        require_book(db, book_id)
        wrong = []
        for cid in ids:
            row = db.get_footnote_candidate(cid)
            if not row or row["book_id"] != book_id:
                wrong.append(cid)
        if wrong:
            raise ToolError(f"Candidate id(s) not in book {book_id}: {wrong}. Nothing changed.")
        from footnote_scan_core import decide_candidates
        n = decide_candidates(db, ids, decision)
        return json_out({"updated": n, "decision": decision})

    @tool(mcp, "t9_place_footnote_candidate", "Place a candidate as a footnote", read_only=False)
    def t9_place_footnote_candidate(book_id: int, candidate_id: int) -> str:
        """Place one unplaced candidate as a real footnote in its own chapter,
        anchored on its term_en (exact, case-sensitive, first match; line 0 is
        skipped only when it reads as a chapter heading) — the same checks auto-apply runs, without the per-chapter cap.
        The candidate becomes "accepted" and links the footnote. Refuses (with
        the reason) when the term isn't in the translation or is already
        footnoted in the book."""
        from footnote_scan_core import CandidateActionError, place_candidate
        db = get_db()
        require_book(db, book_id)
        row = db.get_footnote_candidate(candidate_id)
        if not row or row["book_id"] != book_id:
            raise ToolError(f"Candidate {candidate_id} is not in book {book_id}.")
        try:
            row = place_candidate(db, candidate_id)
        except CandidateActionError as e:
            raise ToolError(str(e))
        return json_out({"placed": True, "candidate": row})


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
