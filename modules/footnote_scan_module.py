"""Footnote candidate scanner — collects footnote SUGGESTIONS on chapter ingest.

When enabled for a book, every NEWLY saved chapter's source is scanned in the
background for Chinese cultural referents worth a translator's footnote
(footnote_scan_core). Candidates land in the footnote_candidates table for
human review in the Footnotes GUI page (or `footnote_scan.py --review`).

Each scan carries the book's own conventions: if the book has a custom system
prompt, its BOOK-SPECIFIC NOTES section is quoted into the scan prompt as
background (scan_single_chapter -> footnote_scan_core.book_notes), so the model
judges referents against the English this translation actually uses.

The scanner's OWN system prompt is per-book too (the "Scan system prompt"
setting, blank = the built-in one, which is the text of
prompts/footnote_scan_prompt.txt). It is a full replacement of the RULES, so a
book whose referents aren't purely Chinese — a novel set in the Japanese game
industry, say — can rewrite the scope rules instead of appending to them. The
output paragraph is not part of it: footnote_scan_core appends that from code,
because the parser and the hallucination filter depend on its shape and it
differs between a standalone scan and the inline one. The same stored rules are
used by the bulk CLI.

By default a chapter is scanned DURING TRANSLATION (scan_mode "translation"),
so this module's on-ingest worker only runs for a book that has explicitly
chosen "ingest" — a book wanting the scan on a model of its own.

This module NEVER places footnotes and never modifies chapters or entities —
it is a collector feeding a human-review queue; applying approved candidates
stays a separate, manual step (add_footnotes.py). Bulk/backlog scanning also
stays with the CLI (`footnote_scan.py -b N --chapters ...`): enabling the
module deliberately triggers no backfill.

Scans run on a single lazily-started daemon worker thread, one chapter at a
time, so a burst of ingests never fans out into parallel model calls. The
scan-row guard (footnote_scans content hash) makes everything safe: a job
that dies mid-scan leaves no scan row and is picked up by the next CLI sweep,
and a duplicate enqueue is skipped as already-scanned.
"""
import queue
import threading

# Cheap import — footnote_scan_core's own imports are stdlib only, and the
# provider SDKs it uses are imported lazily inside the scan call.
from footnote_scan_core import (MODE_INLINE, MODE_SETTING, PROMPT_SETTING,
                                SCAN_MODES, stock_scan_rules)
from footnote_scan_core import DEFAULT_MODEL as DEFAULT_SCAN_MODEL

from .activity import log_module_activity
from .base import TranslationModule


class FootnoteScanWorker:
    """Process-wide single-thread scan queue (lazy daemon thread)."""

    def __init__(self):
        self._q = queue.Queue()
        self._lock = threading.Lock()
        self._thread = None

    def enqueue(self, job):
        self._q.put(job)
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._loop, name="footnote-scan-worker", daemon=True)
                self._thread.start()

    def _loop(self):
        from footnote_scan_core import scan_single_chapter
        while True:
            job = self._q.get()
            db, logger = job["db"], job["logger"]
            book = job["book"]
            book_id, cn = book.get("id"), job["chapter_number"]
            try:
                result = scan_single_chapter(db, job["config"], book, cn,
                                             model_spec=job["model_spec"],
                                             system_prompt=job["system_prompt"])
            except Exception as e:  # noqa: BLE001 - must never kill the worker
                logger.error(
                    f"Footnote scan failed for book {book_id} ch{cn}: {e}")
                log_module_activity(
                    db, "error",
                    f"Footnote scan failed for chapter {cn} of "
                    f"{book.get('title') or f'book {book_id}'}: {e}", book_id)
            else:
                # None = no source / already scanned (guard). Zero-find scans
                # stay quiet — the scan row records them; only real finds (and
                # failures) earn an activity line.
                if result is not None and result[0]:
                    kept, _ = result
                    terms = ", ".join(
                        f["term_en"] or f["term_zh"] for f in kept)
                    log_module_activity(
                        db, "info",
                        f"Footnote scan found {len(kept)} candidate(s) in "
                        f"chapter {cn} of "
                        f"{book.get('title') or f'book {book_id}'}: {terms}",
                        book_id)
            finally:
                self._q.task_done()


footnote_scan_worker = FootnoteScanWorker()


class FootnoteScanModule(TranslationModule):
    id = "footnote_scan"
    name = "Footnote Candidate Scanner"
    description = ("Scans newly ingested chapters for Chinese cultural referents "
                   "and collects footnote suggestions for review — never places "
                   "footnotes itself. Auto-on for Chinese-source books.")
    auto_url_patterns = []
    default_enabled = False

    has_auto = True  # custom source-language auto-rule (see auto_enabled below)

    def auto_enabled(self, book, ctx):
        """On for Chinese-source books — the scanner only looks for Chinese
        cultural referents, so it has nothing to find in any other source
        language. A per-book override still forces it on/off."""
        lang = (book.get("source_language") if (book and hasattr(book, "get")) else None)
        return (lang or "").strip().lower().startswith("zh")

    @property
    def auto_hint(self):
        return "on for books whose source language is Chinese (zh)"

    @property
    def settings_schema(self):
        # A property, not a class attribute, so the built-in prompt offered by
        # "Load built-in prompt" is the current contents of the prompt file
        # rather than whatever it held when this process started.
        return [
            {
                "key": MODE_SETTING,
                "type": "select",
                "label": "When to scan",
                "options": list(SCAN_MODES),
                "help": "'translation' (the default) folds the scan into the "
                        "translation call — no second pass, but the scan rules "
                        "ride along in every chunk of the translation prompt and "
                        "are charged at the translation model's rate. 'ingest' "
                        "scans each new chapter in a second model call of its own, "
                        "which is what the bulk CLI does and what a book wanting a "
                        "cheaper scan model should pick. Same rules, same "
                        "anchoring, same review queue either way.",
                "default": MODE_INLINE,
            },
            {
                "key": "model",
                "type": "text",
                "label": "Scan model",
                "help": f"provider:model spec for the on-ingest scan "
                        f"(default {DEFAULT_SCAN_MODEL}). Ignored when scanning "
                        f"during translation — that scan is the translation, and "
                        f"uses the translation model.",
                "default": DEFAULT_SCAN_MODEL,
                # Deliberately NOT hidden behind show_if when scanning inline: the
                # settings modal strips hidden fields on save, so hiding this would
                # delete a book's chosen scan model the moment it switched to
                # inline, and silently fall back to the default if it switched back.
            },
            {
                # Full replacement, not an addendum: the stock prompt states its
                # scope flatly ("DO NOT FLAG non-Chinese referents"), which a book
                # set in Japan needs to rewrite rather than argue with.
                "key": PROMPT_SETTING,
                "type": "textarea",
                "label": "Scan system prompt",
                "help": "Leave blank to use the built-in rules. 'Load built-in "
                        "prompt' fills the box with them so you can edit a copy — "
                        "e.g. widen the scope to Japanese referents as well. Keep "
                        "the ANCHORING rule: the hallucination filter depends on "
                        "it. The output format is not yours to set — it is "
                        "appended from code, so anything this box says about it is "
                        "ignored. Applies to the on-ingest scan, the inline scan "
                        "and footnote_scan.py.",
                "default": "",
                "rows": 16,
                "default_text": stock_scan_rules(),
            },
        ]

    def event_new_chapter_saved(self, ctx):
        book, db, config = ctx.get("book"), ctx.get("db"), ctx.get("config")
        chapter_number = ctx.get("chapter_number")
        if not (book and db and config) or chapter_number is None:
            return
        source_lines = ctx.get("source_lines") or []
        if not "".join(str(ln) for ln in source_lines).strip():
            return
        settings = self.resolve_settings(
            (ctx.get("module_settings") or {}).get(self.id))
        if (settings.get(MODE_SETTING) or MODE_INLINE) == MODE_INLINE:
            # The translation pass already collected this chapter's candidates
            # (or will, when it runs). Scanning here too would spend a second
            # model call to produce the same rows.
            return
        model_spec = (settings.get("model") or "").strip() or DEFAULT_SCAN_MODEL
        footnote_scan_worker.enqueue({
            "db": db, "config": config, "logger": ctx.get("logger"),
            "book": book, "chapter_number": chapter_number,
            "model_spec": model_spec,
            # "" (unset) → scan_single_chapter uses the stock prompt; the
            # setting is read here, not in the worker, so a settings change
            # can't retroactively rewrite a queued job.
            "system_prompt": (settings.get(PROMPT_SETTING) or "").strip(),
        })
