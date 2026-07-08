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
_job_manager = None
_web_interface = None


def init(entity_manager, job_manager, web_interface):
    global _entity_manager, _job_manager, _web_interface
    _entity_manager = entity_manager
    _job_manager = job_manager
    _web_interface = web_interface


# ------------------------------------------------------------------
# Queue listing / management
# ------------------------------------------------------------------

@router.get("")
def list_queue(book_id: Optional[int] = Query(None)):
    items = _entity_manager.list_queue(book_id=book_id)
    count = _entity_manager.get_queue_count(book_id=book_id)
    book_ids = _entity_manager.get_queued_book_ids()
    return {"items": items or [], "count": count, "book_ids": book_ids}


@router.delete("/{item_id}")
def remove_queue_item(item_id: int):
    success = _entity_manager.remove_from_queue(item_id)
    if not success:
        raise HTTPException(status_code=404, detail="Queue item not found.")
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
async def upload_file_to_queue(
    file: UploadFile = File(...),
    book_id: int = Form(...),
    chapter_number: Optional[int] = Form(None),
):
    book = _entity_manager.get_book(book_id=book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found.")

    content = await file.read()
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
async def upload_batch_to_queue(
    files: list[UploadFile] = File(...),
    book_id: int = Form(...),
    start_chapter: Optional[int] = Form(None),
    sort: str = Form("auto"),
):
    """Upload multiple text files at once, sorted and numbered like a directory import."""
    import re

    book = _entity_manager.get_book(book_id=book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found.")

    if sort not in ("auto", "name", "none"):
        raise HTTPException(status_code=400, detail="sort must be 'auto', 'name', or 'none'.")

    # Read all files and extract metadata
    file_entries = []
    for f in files:
        raw = await f.read()
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

    # Add to queue with sequential chapter numbers
    added = 0
    base_chapter = start_chapter or 1
    for i, entry in enumerate(file_entries):
        chapter_num = entry["chapter_number"] if entry["chapter_number"] is not None else base_chapter + i
        lines = entry["text"].splitlines()
        title = os.path.splitext(entry["filename"])[0]
        queue_id = _entity_manager.add_to_queue(
            book_id=book_id,
            content=lines,
            title=title,
            chapter_number=chapter_num,
            source=f"upload:{entry['filename']}",
        )
        if queue_id:
            added += 1

    # Batch boundary: emit any pending module-transform summaries as one line.
    module_activity.flush(book_id)

    return {
        "status": "ok",
        "files_added": added,
        "total_files": len(file_entries),
        "count": _entity_manager.get_queue_count(),
    }


# ------------------------------------------------------------------
# Upload EPUB to queue
# ------------------------------------------------------------------

@router.post("/upload-epub")
async def upload_epub(
    file: UploadFile = File(...),
    book_id: Optional[int] = Form(None),
    create_book: bool = Form(False),
    genre: Optional[str] = Form(None),
):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from epub_processor import EPUBProcessor
    from config import TranslationConfig

    if not book_id and not create_book:
        raise HTTPException(status_code=400, detail="Provide book_id or set create_book=true.")

    content = await file.read()
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

            # Apply genre preset: prompt template and categories (derived from prompt)
            if genre_obj:
                from genres import read_genre_prompt as _read_prompt, extract_categories_meta_from_prompt as _extract_cats
                prompt = _read_prompt(_entity_manager.config.script_dir, genre_obj)
                if prompt:
                    _entity_manager.set_book_prompt_template(book_id, prompt)
                    cats = _extract_cats(prompt)
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

        success, num_chapters, message = processor.process_epub(tmp_path, book_id)
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
async def upload_fb2(
    file: UploadFile = File(...),
    book_id: Optional[int] = Form(None),
    create_book: bool = Form(False),
    genre: Optional[str] = Form(None),
):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from fb2_processor import FB2Processor

    if not book_id and not create_book:
        raise HTTPException(status_code=400, detail="Provide book_id or set create_book=true.")

    content = await file.read()
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

            # Apply genre preset: prompt template and categories (derived from prompt)
            if genre_obj:
                from genres import read_genre_prompt as _read_prompt, extract_categories_meta_from_prompt as _extract_cats
                prompt = _read_prompt(_entity_manager.config.script_dir, genre_obj)
                if prompt:
                    _entity_manager.set_book_prompt_template(book_id, prompt)
                    cats = _extract_cats(prompt)
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

        success, num_chapters, message = processor.process_fb2(tmp_path, book_id)
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
async def upload_json(
    file: UploadFile = File(...),
    book_id: Optional[int] = Form(None),
    create_book: bool = Form(False),
    genre: Optional[str] = Form(None),
):
    import json as _json

    if not book_id and not create_book:
        raise HTTPException(status_code=400, detail="Provide book_id or set create_book=true.")

    content = await file.read()
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

        # Apply genre preset: prompt template and categories (derived from prompt)
        if genre_obj:
            from genres import read_genre_prompt as _read_prompt, extract_categories_meta_from_prompt as _extract_cats
            prompt = _read_prompt(_entity_manager.config.script_dir, genre_obj)
            if prompt:
                _entity_manager.set_book_prompt_template(book_id, prompt)
                cats = _extract_cats(prompt)
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

    added = 0
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

        title = ch.get("title") or f"Chapter {number}"
        lines = text.splitlines()

        queue_id = _entity_manager.add_to_queue(
            book_id=book_id,
            content=lines,
            title=title,
            chapter_number=number,
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


def _setup_job(queue_item, settings):
    """Configure job_manager and web_interface for a single queue item."""
    _job_manager.pending_text = queue_item["content"]
    _job_manager.book_id = queue_item["book_id"]
    _job_manager.chapter_number = queue_item.get("chapter_number")
    _job_manager.chapter_title = queue_item.get("title")
    _job_manager.status = "running"
    _job_manager.error = None
    _job_manager.last_result = None

    if settings["translation_model"]:
        _web_interface.translator.config.translation_model = settings["translation_model"]
    if settings["advice_model"]:
        _web_interface.translator.config.advice_model = settings["advice_model"]
    _web_interface.cleaning_model = settings["cleaning_model"] or None
    _web_interface.no_review = settings["no_review"]
    # Mutually exclusive with no_review (UI enforces this; backend defends in depth)
    _web_interface.two_pass = settings.get("two_pass", False) and not settings["no_review"]
    _web_interface.no_clean = settings["no_clean"]
    _web_interface.stream = not settings["no_stream"]
    _web_interface.save_as_draft = settings.get("save_as_draft", False)
    _web_interface.retranslation_reason = queue_item.get("retranslation_reason") or None
    _web_interface._current_queue_item = queue_item


def _translate_one(queue_item):
    """Run a single translation. Logs start activity. Raises on error."""
    book_name = None
    if queue_item.get("book_id"):
        book = _entity_manager.get_book(queue_item["book_id"])
        if book:
            book_name = book.get("title")

    ch = queue_item.get("chapter_number")
    remaining = ""
    if _job_manager._auto_max:
        left = _job_manager._auto_max - _job_manager._auto_done
        remaining = f" ({left} to go)" if left > 0 else " (last)"
    _job_manager.log_activity(
        type='start',
        message=f'Translation started: {book_name or "No book"} — Chapter {ch or "auto"}…{remaining}',
        book_id=queue_item.get("book_id"), chapter=ch, book_name=book_name,
    )
    _web_interface.run_translation()


@router.post("/process-next")
async def process_next(req: ProcessNextRequest = ProcessNextRequest()):
    import threading

    if _job_manager.is_running:
        raise HTTPException(status_code=409, detail="A translation is already running.")

    queue_item = _entity_manager.get_next_queue_item(book_id=req.book_id)
    if not queue_item:
        raise HTTPException(status_code=404, detail="No items in queue.")

    settings = {
        "book_id": req.book_id,
        "translation_model": req.translation_model,
        "advice_model": req.advice_model,
        "cleaning_model": req.cleaning_model,
        "no_review": req.no_review,
        "two_pass": req.two_pass,
        "no_clean": req.no_clean,
        "no_stream": req.no_stream,
        "save_as_draft": req.save_as_draft,
    }

    _job_manager.clear_cancel()
    _job_manager.is_running = True
    _setup_job(queue_item, settings)

    if req.auto_process:
        _job_manager.start_auto_process(max_chapters=req.max_chapters)

    # Log the first item from the async context (thread not started yet)
    book_name = None
    if queue_item.get("book_id"):
        book = _entity_manager.get_book(queue_item["book_id"])
        if book:
            book_name = book.get("title")
    ch = queue_item.get("chapter_number")
    remaining = ""
    if _job_manager._auto_max:
        left = _job_manager._auto_max - _job_manager._auto_done
        remaining = f" ({left} to go)" if left > 0 else " (last)"
    await _job_manager.log_activity_async(
        type='start',
        message=f'Translation started: {book_name or "No book"} — Chapter {ch or "auto"}…{remaining}',
        book_id=queue_item.get("book_id"), chapter=ch, book_name=book_name,
    )

    def run():
        try:
            # Translate the first item
            _web_interface.run_translation()

            # Auto-process loop: keep going while enabled and queue has items
            while _job_manager.should_continue_auto():
                next_item = _entity_manager.get_next_queue_item(book_id=settings["book_id"])
                if not next_item:
                    _job_manager.send_message_sync({"type": "auto_process_done", "reason": "queue_empty"})
                    _job_manager.log_activity(type='info', message='Auto-process complete — queue is empty.')
                    break

                _setup_job(next_item, settings)
                _translate_one(next_item)
            else:
                # Loop ended because should_continue_auto() returned False
                if _job_manager._auto_max and _job_manager._auto_done > _job_manager._auto_max:
                    done = _job_manager._auto_max
                    _job_manager.send_message_sync({"type": "auto_process_done", "reason": "limit_reached", "chapters_done": done})
                    _job_manager.log_activity(type='info', message=f'Auto-process complete — {done} chapter limit reached.')
        except TranslationCancelled:
            _job_manager.status = "idle"
            _job_manager.send_message_sync({"type": "translation_cancelled"})
        except Exception as e:
            _job_manager.status = "error"
            _job_manager.error = str(e)
            _job_manager.log_activity(type='error', message=f'Error: {e}')
            _job_manager.send_message_sync({"type": "error", "message": str(e)})
        finally:
            _job_manager.is_running = False
            _job_manager.auto_process = False
            if _job_manager.status not in ("error", "idle", "awaiting_review", "awaiting_json_fix", "awaiting_chapter_conflict"):
                _job_manager.status = "complete"
            # Run boundary: summarize any module transforms from this run
            # (translated-side ingests, plus any source re-ingests at save).
            module_activity.flush()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()

    return {
        "status": "started",
        "auto_process": req.auto_process,
        "item": {"title": queue_item.get("title"), "book_id": queue_item["book_id"]},
    }


@router.post("/stop-auto")
async def stop_auto_process():
    if not _job_manager.auto_process:
        return {"status": "not_running"}
    _job_manager.stop_auto_process()
    await _job_manager.log_activity_async(type='info', message='Auto-process will stop after current chapter.')
    await _job_manager.send_message_async({"type": "auto_process_stopping"})
    return {"status": "stopping"}
