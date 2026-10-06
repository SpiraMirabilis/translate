"""LLM footnote-candidate scan (footnote_scan.py collect mode). Calls a model."""
from typing import Annotated, Optional

import anyio
from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import Field

from ..deps import app
from ..deps import db as get_db
from ..formatting import json_out, truncate
from ._common import CHAPTERS_DESC, chapter_predicate, report, require_book, tool

MAX_CHAPTERS_PER_CALL = 200


def register(mcp) -> None:

    @tool(mcp, "t9_scan_footnotes", "Scan chapters for footnote candidates", read_only=False,
          idempotent=False, open_world=True)
    async def t9_scan_footnotes(
        book_id: int,
        chapters: Annotated[str, Field(min_length=1, description=CHAPTERS_DESC + " (required)")],
        model: Annotated[Optional[str], Field(description="provider:model; default is the scanner's own default")] = None,
        force: Annotated[bool, Field(description="Rescan chapters already scanned with unchanged source")] = False,
        max_chars: Annotated[int, Field(ge=2000, le=100000, description="Source characters per model call")] = 20000,
        stock_prompt: Annotated[bool, Field(description="Ignore the book's custom scan prompt")] = False,
        dry_run: bool = True,
        ctx: Context = None,
    ) -> str:
        """Ask a model to propose footnote candidates for chapters' SOURCE text
        and store them in the review queue (paid calls; one or more per chapter).
        Chapters already scanned with unchanged source are skipped unless force.
        The dry run (default) reports what would be scanned. At most 200
        chapters per call. Afterwards: t9_footnote_candidate_report →
        t9_decide_footnote_candidates → t9_export_footnote_candidates →
        t9_add_footnotes."""
        from footnote_scan import build_scan_jobs, plan_scan
        from footnote_scan_core import (DEFAULT_MODEL, book_notes, covered_registry_for_book,
                                        dedup_candidates, scan_single_chapter)
        db = get_db()
        book = require_book(db, book_id)
        pred = chapter_predicate(chapters)
        jobs, no_source = await anyio.to_thread.run_sync(
            lambda: build_scan_jobs(db, book_id, pred))
        to_scan, skipped, stale = plan_scan(jobs, db.get_footnote_scans(book_id), force)
        model_spec = model or DEFAULT_MODEL
        stale_set = {j["chapter"] for j in stale}
        summary = {"model": model_spec, "to_scan": [j["chapter"] for j in to_scan],
                   "source_changed": sorted(stale_set),
                   "already_scanned": len(skipped), "no_source": no_source}
        if len(to_scan) > MAX_CHAPTERS_PER_CALL:
            raise ToolError(f"{len(to_scan)} chapters to scan; the limit is "
                            f"{MAX_CHAPTERS_PER_CALL} per call — narrow `chapters`.")
        if dry_run or not to_scan:
            return truncate(json_out({"dry_run": dry_run, **summary}))

        config = app().ensure_config()
        token = anyio.lowlevel.current_token()

        def run():
            registry = covered_registry_for_book(db, book_id)
            notes = book_notes(db, book_id)
            results, failed = [], []
            for i, job in enumerate(to_scan, 1):
                cn = job["chapter"]
                try:
                    res = scan_single_chapter(db, config, book, cn, model_spec=model_spec,
                                              max_chars=max_chars, force=True,
                                              registry=registry, notes=notes,
                                              system_prompt="" if stock_prompt else None)
                except Exception as exc:          # one bad chapter must not end the run
                    failed.append({"chapter": cn, "error": str(exc)[:300]})
                    res = None
                if res is not None:
                    kept, dropped = res
                    results.append({"chapter": cn, "kept": len(kept), "dropped": len(dropped)})
                anyio.from_thread.run(report, ctx, i, len(to_scan), f"ch{cn}", token=token)
            return results, failed, dedup_candidates(db, book_id)

        results, failed, deduped = await anyio.to_thread.run_sync(run)
        return truncate(json_out({
            "dry_run": False, **summary, "scanned": len(results),
            "candidates_kept": sum(r["kept"] for r in results),
            "dropped_unverified": sum(r["dropped"] for r in results),
            "later_repeats_removed": deduped, "failed": failed, "per_chapter": results}))
