"""
Queue management endpoints.
"""
import os
import sys
import tempfile
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Query
from pydantic import BaseModel
from typing import Optional

from modules import module_activity
from translation_engine import TranslationCancelled

router = APIRouter(prefix="/api/queue")

_entity_manager = None
_registry = None
_make_web_interface = None


def init(entity_manager, job_registry, make_web_interface):
    global _entity_manager, _registry, _make_web_interface
    _entity_manager = entity_manager
    _registry = job_registry
    _make_web_interface = make_web_interface


def _begin_job_or_409(book_id):
    """Claim this book's translation slot, or fail with a reason the UI can show."""
    from web.services.job_manager import JobBusyError, JobCapacityError
    try:
        return _registry.begin(book_id)
    except (JobBusyError, JobCapacityError) as e:
        raise HTTPException(status_code=409, detail=str(e))


def _pick_book_to_process(requested_book_id):
    """Resolve which book this worker should drain.

    A request naming a book gets that book. A request that just says "next"
    picks the earliest-queued book that isn't already translating — the flat
    queue's next row would otherwise belong to a book whose worker is mid
    chapter, and one job per book is the invariant that keeps each book's
    entity glossary consistent.
    """
    if requested_book_id is not None:
        return requested_book_id

    queued = _entity_manager.get_next_queued_book_ids()
    if not queued:
        raise HTTPException(status_code=404, detail="No items in queue.")

    running = _registry.running_book_ids()
    for book_id in queued:
        if book_id not in running:
            return book_id

    raise HTTPException(
        status_code=409,
        detail="Every book with queued chapters is already translating.",
    )


def _ensure_project_root_on_path():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if root not in sys.path:
        sys.path.insert(0, root)


# ------------------------------------------------------------------
# Duplicate-chapter detection for bulk uploads
#
# A bulk import (multi-file batch, JSON, EPUB, FB2) can overlap chapters the
# book already has. Rather than silently re-queuing them, the endpoints report
# the collision (on_conflict="ask") and let the UI choose:
#   keep    -> queue everything anyway (re-translate/overwrite)
#   discard -> skip the duplicates, queue only the genuinely-new numbers
# ------------------------------------------------------------------

_ON_CONFLICT_VALUES = ("ask", "keep", "discard")


def _norm_on_conflict(val):
    v = (val or "ask").strip().lower()
    if v not in _ON_CONFLICT_VALUES:
        raise HTTPException(status_code=400, detail="on_conflict must be 'ask', 'keep', or 'discard'.")
    return v


def _existing_chapter_numbers(book_id):
    """Chapter numbers already present for a book — saved chapters OR queued rows."""
    nums = set()
    try:
        for ch in (_entity_manager.list_chapters(book_id) or []):
            n = ch.get("chapter")
            if isinstance(n, int):
                nums.add(n)
    except Exception:
        pass
    try:
        for it in (_entity_manager.list_queue(book_id=book_id, include_processing=True) or []):
            n = it.get("chapter_number")
            if isinstance(n, int):
                nums.add(n)
    except Exception:
        pass
    return nums


def _format_number_ranges(nums):
    """Collapse a set of ints into a compact label: [3,4,5,7,10,11] -> '3–5, 7, 10–11'."""
    nums = sorted({n for n in nums if isinstance(n, int)})
    if not nums:
        return ""
    parts = []
    start = prev = nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
            continue
        parts.append((start, prev))
        start = prev = n
    parts.append((start, prev))
    return ", ".join(str(a) if a == b else f"{a}–{b}" for a, b in parts)


def _conflict_response(book_id, conflicts):
    conflicts = sorted(conflicts)
    return {
        "status": "conflict",
        "book_id": book_id,
        "existing_numbers": conflicts,
        "existing_label": _format_number_ranges(conflicts),
    }


# ------------------------------------------------------------------
# Queue listing / management
# ------------------------------------------------------------------

@router.get("")
def list_queue(book_id: Optional[int] = Query(None)):
    # include_processing: show the in-flight chapter rather than letting it
    # disappear the moment it's claimed. `count` stays claimable-only, so the
    # "N to go" figures elsewhere keep their meaning.
    items = _entity_manager.list_queue(book_id=book_id, include_processing=True)
    count = _entity_manager.get_queue_count(book_id=book_id)
    book_ids = _entity_manager.get_queued_book_ids()
    return {"items": items or [], "count": count, "book_ids": book_ids}


@router.delete("/{item_id}")
def remove_queue_item(item_id: int):
    success = _entity_manager.remove_from_queue(item_id)
    if not success:
        raise HTTPException(status_code=404, detail="Queue item not found.")
    return {"status": "ok"}


@router.post("/{item_id}/release")
def release_queue_item(item_id: int):
    """Hand a claimed row back to the queue.

    Escape hatch for a claim whose worker is gone but which the startup sweep
    can't prove dead (claimed on another host, or by a pre-upgrade worker that
    left no PID). Releasing a row that a live worker still holds risks a double
    translation, so refuse while a job is actually running here.
    """
    # Only the book's own worker can be holding this row. A job on another
    # book is irrelevant — refusing on it would make the escape hatch useless
    # whenever anything at all was translating.
    owning_book = _entity_manager.get_queue_item_book_id(item_id)
    job = _registry.get(owning_book) if owning_book is not None else None
    if job is not None and job.is_running:
        raise HTTPException(
            status_code=409,
            detail=f"Book {owning_book} is translating. Stop it before returning "
                   f"its items to the queue.",
        )
    success = _entity_manager.release_queue_item(item_id)
    if not success:
        raise HTTPException(status_code=404, detail="No claimed queue item with that ID.")
    return {"status": "ok"}


@router.delete("")
def clear_queue(book_id: Optional[int] = Query(None)):
    try:
        count = _entity_manager.clear_queue(book_id=book_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to clear queue: {e}")
    return {"status": "ok", "removed": count}


# ------------------------------------------------------------------
# Add text to queue
# ------------------------------------------------------------------

class QueueAddRequest(BaseModel):
    text: str
    book_id: int
    chapter_number: Optional[int] = None
    title: Optional[str] = None
    priority: bool = False
    retranslation_reason: Optional[str] = None


@router.post("/add")
def add_to_queue(req: QueueAddRequest):
    book = _entity_manager.get_book(book_id=req.book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found.")

    lines = req.text.splitlines()
    queue_id = _entity_manager.add_to_queue(
        book_id=req.book_id,
        content=lines,
        title=req.title or book["title"],
        chapter_number=req.chapter_number,
        source="web",
        priority=req.priority,
        retranslation_reason=req.retranslation_reason,
    )
    if not queue_id:
        raise HTTPException(status_code=500, detail="Failed to add to queue.")
    # Single-item boundary: log any module transform now, not on the timer.
    module_activity.flush(req.book_id)
    return {"queue_id": queue_id, "count": _entity_manager.get_queue_count()}


# ------------------------------------------------------------------
# Upload file to queue
# ------------------------------------------------------------------

@router.post("/upload")
def upload_file_to_queue(
    file: UploadFile = File(...),
    book_id: int = Form(...),
    chapter_number: Optional[int] = Form(None),
):
    # Sync handler on purpose: FastAPI runs it in a threadpool, so parsing and
    # DB work can't starve the event loop (and the /api/health watchdog).
    book = _entity_manager.get_book(book_id=book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found.")

    content = file.file.read()
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        text = content.decode("gbk", errors="replace")

    lines = text.splitlines()
    queue_id = _entity_manager.add_to_queue(
        book_id=book_id,
        content=lines,
        title=file.filename,
        chapter_number=chapter_number,
        source=f"upload:{file.filename}",
    )
    if not queue_id:
        raise HTTPException(status_code=500, detail="Failed to add to queue.")
    # Single-item boundary: log any module transform now, not on the timer.
    module_activity.flush(book_id)
    return {"queue_id": queue_id, "filename": file.filename, "count": _entity_manager.get_queue_count()}


# ------------------------------------------------------------------
# Upload multiple text files to queue (directory-style batch upload)
# ------------------------------------------------------------------

@router.post("/upload-batch")
def upload_batch_to_queue(
    files: list[UploadFile] = File(...),
    book_id: int = Form(...),
    start_chapter: Optional[int] = Form(None),
    sort: str = Form("auto"),
    on_conflict: str = Form("ask"),
):
    """Upload multiple text files at once, sorted and numbered like a directory import."""
    import re

    book = _entity_manager.get_book(book_id=book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found.")

    if sort not in ("auto", "name", "none"):
        raise HTTPException(status_code=400, detail="sort must be 'auto', 'name', or 'none'.")

    on_conflict = _norm_on_conflict(on_conflict)

    # Read all files and extract metadata
    file_entries = []
    for f in files:
        raw = f.file.read()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("gbk", errors="replace")

        filename = f.filename or "unknown.txt"
        chapter_num = None
        chapter_match = re.search(
            r'(?:chapter|ch|第)[\s_-]*(\d+)|^(\d+)',
            filename, re.IGNORECASE
        )
        if chapter_match:
            chapter_num = int(next(g for g in chapter_match.groups() if g is not None))

        file_entries.append({
            "filename": filename,
            "text": text,
            "chapter_number": chapter_num,
        })

    # Sort
    if sort == "auto":
        has_numbers = any(e["chapter_number"] is not None for e in file_entries)
        if has_numbers:
            file_entries.sort(key=lambda e: e["chapter_number"] if e["chapter_number"] is not None else float('inf'))
        else:
            file_entries.sort(key=lambda e: e["filename"])
    elif sort == "name":
        file_entries.sort(key=lambda e: e["filename"])

    # Assign the chapter number each file would take (detected, else sequential).
    base_chapter = start_chapter or 1
    for i, entry in enumerate(file_entries):
        entry["assigned_number"] = (
            entry["chapter_number"] if entry["chapter_number"] is not None else base_chapter + i
        )

    # Duplicate detection against chapters already in the book/queue.
    existing = _existing_chapter_numbers(book_id)
    conflicts = sorted({e["assigned_number"] for e in file_entries} & existing)
    if conflicts and on_conflict == "ask":
        return _conflict_response(book_id, conflicts)
    skip = set(conflicts) if on_conflict == "discard" else set()

    # Add to queue with sequential chapter numbers
    added = 0
    skipped = 0
    for entry in file_entries:
        if entry["assigned_number"] in skip:
            skipped += 1
            continue
        lines = entry["text"].splitlines()
        title = os.path.splitext(entry["filename"])[0]
        queue_id = _entity_manager.add_to_queue(
            book_id=book_id,
            content=lines,
            title=title,
            chapter_number=entry["assigned_number"],
            source=f"upload:{entry['filename']}",
        )
        if queue_id:
            added += 1

    # Batch boundary: emit any pending module-transform summaries as one line.
    module_activity.flush(book_id)

    return {
        "status": "ok",
        "files_added": added,
        "skipped": skipped,
        "total_files": len(file_entries),
        "count": _entity_manager.get_queue_count(),
    }


# ------------------------------------------------------------------
# Upload EPUB to queue
# ------------------------------------------------------------------

@router.post("/upload-epub")
def upload_epub(
    file: UploadFile = File(...),
    book_id: Optional[int] = Form(None),
    create_book: bool = Form(False),
    genre: Optional[str] = Form(None),
    on_conflict: str = Form("ask"),
):
    _ensure_project_root_on_path()
    from epub_processor import EPUBProcessor
    from config import TranslationConfig

    if not book_id and not create_book:
        raise HTTPException(status_code=400, detail="Provide book_id or set create_book=true.")

    on_conflict = _norm_on_conflict(on_conflict)

    content = file.file.read()
    with tempfile.NamedTemporaryFile(suffix=".epub", delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        config = _entity_manager.config
        from logger import Logger
        logger = Logger(config)
        processor = EPUBProcessor(config, logger, _entity_manager)

        if create_book:
            meta = processor.get_epub_metadata(tmp_path)

            # Determine source language from genre if provided
            source_lang = "zh"
            genre_obj = None
            if genre and genre != "custom":
                from genres import get_genre as _get_genre
                genre_obj = _get_genre(_entity_manager.config.script_dir, genre)
                if genre_obj and genre_obj.get("source_language"):
                    source_lang = genre_obj["source_language"]

            book_id = _entity_manager.create_book(
                title=meta.get("title", file.filename),
                author=meta.get("author", "Unknown"),
                language="en",
                source_language=source_lang,
                description=f"Imported from {file.filename}",
            )
            if not book_id:
                raise HTTPException(status_code=500, detail="Failed to create book from EPUB.")

            # Apply genre preset: prompt template, plus the genre's declared categories
            if genre_obj:
                from genres import read_genre_prompt as _read_prompt, genre_categories as _genre_cats
                prompt = _read_prompt(_entity_manager.config.script_dir, genre_obj)
                if prompt:
                    _entity_manager.set_book_prompt_template(book_id, prompt)
                    cats = _genre_cats(genre_obj, prompt)
                    if cats:
                        _entity_manager.set_book_categories(book_id, cats)

            # Extract cover image from EPUB
            try:
                epub_book = processor.load_epub(tmp_path)
                if epub_book:
                    cover_bytes, cover_ext = processor.extract_cover_image(epub_book)
                    if cover_bytes:
                        cover_rel = processor.save_cover_image(cover_bytes, cover_ext, book_id)
                        _entity_manager.update_book(book_id, cover_image=cover_rel)
            except Exception:
                pass  # Non-fatal

        # Adding to an existing book can collide with chapters it already has.
        # (A freshly-created book never collides.)
        skip_numbers = None
        if not create_book:
            existing = _existing_chapter_numbers(book_id)
            conflicts = sorted(set(processor.extract_chapter_numbers(tmp_path)) & existing)
            if conflicts and on_conflict == "ask":
                return _conflict_response(book_id, conflicts)
            if on_conflict == "discard":
                skip_numbers = set(conflicts)

        success, num_chapters, message = processor.process_epub(tmp_path, book_id, skip_numbers=skip_numbers)
        if not success:
            raise HTTPException(status_code=500, detail=message)

        # Batch boundary: emit pending module-transform summaries as one line.
        module_activity.flush(book_id)

        return {
            "status": "ok",
            "book_id": book_id,
            "chapters_added": num_chapters,
            "message": message,
        }
    finally:
        os.unlink(tmp_path)


# ------------------------------------------------------------------
# Upload FB2 (FictionBook 2.0) to queue
# ------------------------------------------------------------------

@router.post("/upload-fb2")
def upload_fb2(
    file: UploadFile = File(...),
    book_id: Optional[int] = Form(None),
    create_book: bool = Form(False),
    genre: Optional[str] = Form(None),
    on_conflict: str = Form("ask"),
):
    _ensure_project_root_on_path()
    from fb2_processor import FB2Processor

    if not book_id and not create_book:
        raise HTTPException(status_code=400, detail="Provide book_id or set create_book=true.")

    on_conflict = _norm_on_conflict(on_conflict)

    content = file.file.read()
    # Preserve the .zip suffix so the processor unzips .fb2.zip archives.
    suffix = ".fb2.zip" if file.filename and file.filename.lower().endswith(".zip") else ".fb2"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        config = _entity_manager.config
        from logger import Logger
        logger = Logger(config)
        processor = FB2Processor(config, logger, _entity_manager)

        if create_book:
            meta = processor.get_fb2_metadata(tmp_path)

            # Determine source language from genre if provided; FB2 is most
            # often Russian, so default to "ru" (vs EPUB's "zh").
            source_lang = "ru"
            genre_obj = None
            if genre and genre != "custom":
                from genres import get_genre as _get_genre
                genre_obj = _get_genre(_entity_manager.config.script_dir, genre)
                if genre_obj and genre_obj.get("source_language"):
                    source_lang = genre_obj["source_language"]

            book_id = _entity_manager.create_book(
                title=meta.get("title", file.filename),
                author=meta.get("author", "Unknown"),
                language="en",
                source_language=source_lang,
                description=f"Imported from {file.filename}",
            )
            if not book_id:
                raise HTTPException(status_code=500, detail="Failed to create book from FB2.")

            # Apply genre preset: prompt template, plus the genre's declared categories
            if genre_obj:
                from genres import read_genre_prompt as _read_prompt, genre_categories as _genre_cats
                prompt = _read_prompt(_entity_manager.config.script_dir, genre_obj)
                if prompt:
                    _entity_manager.set_book_prompt_template(book_id, prompt)
                    cats = _genre_cats(genre_obj, prompt)
                    if cats:
                        _entity_manager.set_book_categories(book_id, cats)

            # Extract cover image from FB2
            try:
                fb2_root = processor.load_fb2(tmp_path)
                if fb2_root is not None:
                    cover_bytes, cover_ext = processor.extract_cover_image(fb2_root)
                    if cover_bytes:
                        cover_rel = processor.save_cover_image(cover_bytes, cover_ext, book_id)
                        _entity_manager.update_book(book_id, cover_image=cover_rel)
            except Exception:
                pass  # Non-fatal

        # Adding to an existing book can collide with chapters it already has.
        skip_numbers = None
        if not create_book:
            existing = _existing_chapter_numbers(book_id)
            conflicts = sorted(set(processor.extract_chapter_numbers(tmp_path)) & existing)
            if conflicts and on_conflict == "ask":
                return _conflict_response(book_id, conflicts)
            if on_conflict == "discard":
                skip_numbers = set(conflicts)

        success, num_chapters, message = processor.process_fb2(tmp_path, book_id, skip_numbers=skip_numbers)
        if not success:
            raise HTTPException(status_code=500, detail=message)

        # Batch boundary: emit pending module-transform summaries as one line.
        module_activity.flush(book_id)

        return {
            "status": "ok",
            "book_id": book_id,
            "chapters_added": num_chapters,
            "message": message,
        }
    finally:
        os.unlink(tmp_path)


# ------------------------------------------------------------------
# Upload structured JSON capture to queue
#
# Accepts a single JSON file produced by an external scraper, shaped like:
#   {
#     "book": "<foreign book id>",
#     "source": "<foreign TOC/details URL>",
#     "capturedAt": "...", "chapterCount": N, "pageCount": N,
#     "chapters": [
#       {"index": 0, "number": 1, "title": "...",
#        "sourceUrls": [...],           # per-chapter pagination — ignored
#        "text": "line1\n\nline2..."}   # \n-separated source text
#     ]
#   }
# Each chapter is queued for translation like an EPUB/FB2 import.
# ------------------------------------------------------------------

@router.post("/upload-json")
def upload_json(
    file: UploadFile = File(...),
    book_id: Optional[int] = Form(None),
    create_book: bool = Form(False),
    genre: Optional[str] = Form(None),
    on_conflict: str = Form("ask"),
):
    import json as _json

    if not book_id and not create_book:
        raise HTTPException(status_code=400, detail="Provide book_id or set create_book=true.")

    on_conflict = _norm_on_conflict(on_conflict)

    content = file.file.read()
    try:
        # utf-8-sig transparently strips a BOM if the scraper wrote one.
        raw_text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raw_text = content.decode("utf-8", errors="replace")

    try:
        data = _json.loads(raw_text)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON file: {e}")

    if not isinstance(data, dict) or not isinstance(data.get("chapters"), list):
        raise HTTPException(status_code=400, detail="JSON must be an object with a 'chapters' array.")

    chapters = data["chapters"]
    if not chapters:
        raise HTTPException(status_code=400, detail="No chapters found in JSON.")

    foreign_source = str(data.get("source") or "").strip()

    if create_book:
        # No title/author metadata in the schema — seed a placeholder the user
        # can rename later, using the foreign book id or the filename.
        source_lang = "zh"
        genre_obj = None
        if genre and genre != "custom":
            from genres import get_genre as _get_genre
            genre_obj = _get_genre(_entity_manager.config.script_dir, genre)
            if genre_obj and genre_obj.get("source_language"):
                source_lang = genre_obj["source_language"]

        foreign_book = str(data.get("book") or "").strip()
        title = foreign_book or os.path.splitext(file.filename or "Imported book")[0]

        book_id = _entity_manager.create_book(
            title=title,
            author="Unknown",
            language="en",
            source_language=source_lang,
            description=f"Imported from {foreign_source or file.filename}",
        )
        if not book_id:
            raise HTTPException(status_code=500, detail="Failed to create book from JSON.")

        # Apply genre preset: prompt template, plus the genre's declared categories
        if genre_obj:
            from genres import read_genre_prompt as _read_prompt, genre_categories as _genre_cats
            prompt = _read_prompt(_entity_manager.config.script_dir, genre_obj)
            if prompt:
                _entity_manager.set_book_prompt_template(book_id, prompt)
                cats = _genre_cats(genre_obj, prompt)
                if cats:
                    _entity_manager.set_book_categories(book_id, cats)

        # Record the source URL so the per-book module system can key on it.
        if foreign_source:
            try:
                _entity_manager.update_book(book_id, source_url=foreign_source)
            except Exception:
                pass  # Non-fatal
    else:
        book = _entity_manager.get_book(book_id=book_id)
        if not book:
            raise HTTPException(status_code=404, detail="Book not found.")

    # First pass: collect the valid chapters and the number each would take.
    valid = []
    skipped = 0
    for i, ch in enumerate(chapters):
        if not isinstance(ch, dict):
            skipped += 1
            continue

        text = ch.get("text")
        if not isinstance(text, str) or not text.strip():
            skipped += 1
            continue

        # Prefer the 1-based chapter number; fall back to 0-based index+1.
        number = ch.get("number")
        if not isinstance(number, int):
            idx = ch.get("index")
            number = (idx + 1) if isinstance(idx, int) else (i + 1)

        valid.append({
            "number": number,
            "title": ch.get("title") or f"Chapter {number}",
            "lines": text.splitlines(),
        })

    # Duplicate detection (only when adding to an existing book — a book we
    # just created above is empty and cannot collide).
    skip = set()
    if not create_book:
        existing = _existing_chapter_numbers(book_id)
        conflicts = sorted({e["number"] for e in valid} & existing)
        if conflicts and on_conflict == "ask":
            return _conflict_response(book_id, conflicts)
        if on_conflict == "discard":
            skip = set(conflicts)

    added = 0
    for entry in valid:
        if entry["number"] in skip:
            skipped += 1
            continue
        queue_id = _entity_manager.add_to_queue(
            book_id=book_id,
            content=entry["lines"],
            title=entry["title"],
            chapter_number=entry["number"],
            source=f"upload:{file.filename}",
        )
        if queue_id:
            added += 1
        else:
            skipped += 1

    # Batch boundary: emit pending module-transform summaries as one line.
    module_activity.flush(book_id)

    return {
        "status": "ok",
        "book_id": book_id,
        "chapters_added": added,
        "skipped": skipped,
        "total_chapters": len(chapters),
        "count": _entity_manager.get_queue_count(),
    }


# ------------------------------------------------------------------
# Process next queue item
# ------------------------------------------------------------------

class ProcessNextRequest(BaseModel):
    book_id: Optional[int] = None
    translation_model: Optional[str] = None
    advice_model: Optional[str] = None
    cleaning_model: Optional[str] = None
    no_review: bool = False
    two_pass: bool = False
    no_clean: bool = False
    no_stream: bool = False
    save_as_draft: bool = False  # save new chapters unpublished (publish manually later)
    auto_process: bool = False
    max_chapters: Optional[int] = None  # Stop after N chapters (None = unlimited)


def _setup_job(job, web_interface, queue_item, settings):
    """Point this run's job + WebInterface at a single queue item.

    Called once per item, including each iteration of the auto-process loop.
    The models are NOT applied here: they were baked into this run's own config
    clone when the WebInterface was built, so there is no shared state to
    overwrite and nothing to restore afterwards.
    """
    job.pending_text = queue_item["content"]
    job.book_id = queue_item["book_id"]
    job.chapter_number = queue_item.get("chapter_number")
    job.chapter_title = queue_item.get("title")
    job.status = "running"
    job.error = None
    job.last_result = None

    web_interface.cleaning_model = settings["cleaning_model"] or None
    web_interface.no_review = settings["no_review"]
    # Mutually exclusive with no_review (UI enforces this; backend defends in depth)
    web_interface.two_pass = settings.get("two_pass", False) and not settings["no_review"]
    web_interface.no_clean = settings["no_clean"]
    web_interface.stream = not settings["no_stream"]
    web_interface.save_as_draft = settings.get("save_as_draft", False)
    web_interface.retranslation_reason = queue_item.get("retranslation_reason") or None
    web_interface._current_queue_item = queue_item


def _translate_one(job, web_interface, queue_item):
    """Run a single translation. Logs start activity. Raises on error."""
    book_name = None
    if queue_item.get("book_id"):
        book = _entity_manager.get_book(queue_item["book_id"])
        if book:
            book_name = book.get("title")

    ch = queue_item.get("chapter_number")
    remaining = ""
    if job._auto_max:
        left = job._auto_max - job._auto_done
        remaining = f" ({left} to go)" if left > 0 else " (last)"
    job.log_activity(
        type='start',
        message=f'Translation started: {book_name or "No book"} — Chapter {ch or "auto"}…{remaining}',
        book_id=queue_item.get("book_id"), chapter=ch, book_name=book_name,
    )
    web_interface.run_translation()


@router.post("/process-next")
def process_next(req: ProcessNextRequest = ProcessNextRequest()):
    import threading

    book_id = _pick_book_to_process(req.book_id)

    job = _begin_job_or_409(book_id)

    # Atomic claim so CLI --resume / another worker cannot take the same row.
    queue_item = _entity_manager.claim_next_queue_item(book_id=book_id)
    if not queue_item:
        _registry.end(book_id)
        raise HTTPException(status_code=404, detail="No items in queue.")

    settings = {
        # The resolved book, not the request's: the auto-process loop reuses it
        # to claim only this book's rows, so a run started as "whatever is next"
        # still stays pinned to one book instead of roaming the whole queue.
        "book_id": book_id,
        "translation_model": req.translation_model,
        "advice_model": req.advice_model,
        "cleaning_model": req.cleaning_model,
        "no_review": req.no_review,
        "two_pass": req.two_pass,
        "no_clean": req.no_clean,
        "no_stream": req.no_stream,
        "save_as_draft": req.save_as_draft,
    }

    job.clear_cancel()

    # This run owns its WebInterface, engine and config clone (models baked in
    # at construction), so the per-run overrides can neither leak into the
    # process default nor be swapped out by another run mid-chapter.
    web_interface = _make_web_interface(
        job,
        translation_model=settings["translation_model"],
        advice_model=settings["advice_model"],
    )

    try:
        _setup_job(job, web_interface, queue_item, settings)

        if req.auto_process:
            job.start_auto_process(max_chapters=req.max_chapters)
        # Everything needed to restart this run the same way after a pause.
        job.run_options = {
            **{k: v for k, v in settings.items() if k != "book_id"},
            "auto_process": req.auto_process,
            "max_chapters": req.max_chapters,
        }

        # Log the first item before the worker thread starts
        book_name = None
        if queue_item.get("book_id"):
            book = _entity_manager.get_book(queue_item["book_id"])
            if book:
                book_name = book.get("title")
        ch = queue_item.get("chapter_number")
        remaining = ""
        if job._auto_max:
            left = job._auto_max - job._auto_done
            remaining = f" ({left} to go)" if left > 0 else " (last)"
        job.log_activity(
            type='start',
            message=f'Translation started: {book_name or "No book"} — Chapter {ch or "auto"}…{remaining}',
            book_id=queue_item.get("book_id"), chapter=ch, book_name=book_name,
        )
    except BaseException:
        # Anything failing before the worker thread owns the flag would
        # otherwise leave is_running stuck True (every request 409s).
        try:
            _entity_manager.release_queue_item(queue_item["id"])
        except Exception:
            pass
        _registry.end(book_id)
        job.auto_process = False
        raise

    def run():
        current = queue_item
        try:
            # Translate the first item
            web_interface.run_translation()

            # Auto-process loop: keep going while enabled and queue has items
            while job.should_continue_auto():
                next_item = _entity_manager.claim_next_queue_item(book_id=settings["book_id"])
                if not next_item:
                    job.send_message_sync({"type": "auto_process_done", "reason": "queue_empty"})
                    job.log_activity(type='info', message='Auto-process complete — queue is empty.')
                    break

                current = next_item
                _setup_job(job, web_interface, next_item, settings)
                _translate_one(job, web_interface, next_item)
            else:
                # Loop ended because should_continue_auto() returned False
                if job._auto_max and job._auto_done > job._auto_max:
                    done = job._auto_max
                    job.send_message_sync({"type": "auto_process_done", "reason": "limit_reached", "chapters_done": done})
                    job.log_activity(type='info', message=f'Auto-process complete — {done} chapter limit reached.')
        except TranslationCancelled:
            # Release the in-flight claim so the chapter stays on the queue.
            try:
                if current and current.get("id"):
                    _entity_manager.release_queue_item(current["id"])
            except Exception:
                pass
            job.status = "idle"
            job.send_message_sync({"type": "translation_cancelled"})
        except Exception as e:
            try:
                if current and current.get("id"):
                    _entity_manager.release_queue_item(current["id"])
            except Exception:
                pass
            job.status = "error"
            job.error = str(e)
            job.log_activity(type='error', message=f'Error: {e}')
            job.send_message_sync({"type": "error", "message": str(e)})
        finally:
            _registry.end(book_id)
            job.auto_process = False
            if job.status not in ("error", "idle", "awaiting_review", "awaiting_json_fix", "awaiting_chapter_conflict"):
                job.status = "complete"
            # Run boundary: summarize this run's module transforms (translated-side
            # ingests, plus any source re-ingests at save). Scoped to this book so a
            # concurrent job's pending summaries aren't flushed early against it.
            module_activity.flush(book_id=job.book_id)
            _registry.prune()

    try:
        thread = threading.Thread(
            target=run, daemon=True, name=f"translate-book{book_id}")
        thread.start()
    except BaseException:
        try:
            _entity_manager.release_queue_item(queue_item["id"])
        except Exception:
            pass
        _registry.end(book_id)
        job.auto_process = False
        raise

    return {
        "status": "started",
        "auto_process": req.auto_process,
        "item": {"title": queue_item.get("title"), "book_id": queue_item["book_id"]},
    }


class StopAutoRequest(BaseModel):
    # Omitted = stop every auto-processing job (what a pre-multi-job client means).
    book_id: Optional[int] = None


@router.post("/stop-auto")
def stop_auto_process(req: StopAutoRequest = StopAutoRequest()):
    """Stop auto-processing after the current chapter, for one book or all."""
    if req.book_id is not None:
        job = _registry.get(req.book_id)
        targets = [job] if job is not None and job.auto_process else []
    else:
        targets = [j for j in _registry.active() if j.auto_process]

    if not targets:
        return {"status": "not_running"}

    for job in targets:
        job.stop_auto_process()
        job.log_activity(type='info', message='Auto-process will stop after current chapter.')
        job.send_message_sync({"type": "auto_process_stopping"})
    return {"status": "stopping", "stopped": [j.book_id for j in targets]}


class ProcessAllRequest(ProcessNextRequest):
    """Same run options as process-next, minus the single-book target."""
    book_id: Optional[int] = None  # ignored; kept so a stray field isn't an error


@router.post("/process-all")
def process_all(req: ProcessAllRequest = ProcessAllRequest()):
    """Start one worker per queued book, up to the concurrency limit.

    Books already translating are skipped rather than erroring — the point of
    the button is "get everything moving", and a half-started sweep is more
    confusing than one that reports what it did.
    """
    started, skipped = [], []
    running = _registry.running_book_ids()

    for book_id in _entity_manager.get_next_queued_book_ids():
        if book_id in running:
            skipped.append({"book_id": book_id, "reason": "already_running"})
            continue
        if not _registry.has_capacity():
            skipped.append({"book_id": book_id, "reason": "at_capacity"})
            continue

        single = ProcessNextRequest(**{**req.model_dump(), "book_id": book_id})
        try:
            result = process_next(single)
        except HTTPException as e:
            skipped.append({"book_id": book_id, "reason": e.detail})
            continue
        started.append({"book_id": book_id, "item": result.get("item")})

    return {
        "status": "started" if started else "noop",
        "started": started,
        "skipped": skipped,
        "running": len(_registry.active()),
        "max_concurrent": _registry.max_concurrent(),
    }
