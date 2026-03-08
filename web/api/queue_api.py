"""
Queue management endpoints.
"""
import os
import sys
import tempfile
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Query
from pydantic import BaseModel
from typing import Optional

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
async def list_queue(book_id: Optional[int] = Query(None)):
    items = _entity_manager.list_queue(book_id=book_id)
    count = _entity_manager.get_queue_count(book_id=book_id)
    return {"items": items or [], "count": count}


@router.delete("/{item_id}")
async def remove_queue_item(item_id: int):
    success = _entity_manager.remove_from_queue(item_id)
    if not success:
        raise HTTPException(status_code=404, detail="Queue item not found.")
    return {"status": "ok"}


@router.delete("")
async def clear_queue(book_id: Optional[int] = Query(None)):
    _entity_manager.clear_queue(book_id=book_id)
    return {"status": "ok"}


# ------------------------------------------------------------------
# Add text to queue
# ------------------------------------------------------------------

class QueueAddRequest(BaseModel):
    text: str
    book_id: int
    chapter_number: Optional[int] = None
    title: Optional[str] = None
    priority: bool = False


@router.post("/add")
async def add_to_queue(req: QueueAddRequest):
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
    )
    if not queue_id:
        raise HTTPException(status_code=500, detail="Failed to add to queue.")
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
            book_id = _entity_manager.create_book(
                title=meta.get("title", file.filename),
                author=meta.get("author", "Unknown"),
                language="en",
                source_language="zh",
                description=f"Imported from {file.filename}",
            )
            if not book_id:
                raise HTTPException(status_code=500, detail="Failed to create book from EPUB.")

        success, num_chapters, message = processor.process_epub(tmp_path, book_id)
        if not success:
            raise HTTPException(status_code=500, detail=message)

        return {
            "status": "ok",
            "book_id": book_id,
            "chapters_added": num_chapters,
            "message": message,
        }
    finally:
        os.unlink(tmp_path)


# ------------------------------------------------------------------
# Process next queue item
# ------------------------------------------------------------------

class ProcessNextRequest(BaseModel):
    book_id: Optional[int] = None
    translation_model: Optional[str] = None
    advice_model: Optional[str] = None
    cleaning_model: Optional[str] = None
    no_review: bool = False
    no_clean: bool = False
    no_repair: bool = False


@router.post("/process-next")
async def process_next(req: ProcessNextRequest = ProcessNextRequest()):
    import threading

    if _job_manager.is_running:
        raise HTTPException(status_code=409, detail="A translation is already running.")

    queue_item = _entity_manager.get_next_queue_item(book_id=req.book_id)
    if not queue_item:
        raise HTTPException(status_code=404, detail="No items in queue.")

    _job_manager.pending_text = queue_item["content"]
    _job_manager.book_id = queue_item["book_id"]
    _job_manager.chapter_number = queue_item.get("chapter_number")
    _job_manager.is_running = True
    _job_manager.status = "running"
    _job_manager.error = None
    _job_manager.last_result = None

    # Apply model overrides
    if req.translation_model:
        _web_interface.translator.config.translation_model = req.translation_model
    if req.advice_model:
        _web_interface.translator.config.advice_model = req.advice_model
    _web_interface.cleaning_model = req.cleaning_model or None
    _web_interface.no_review = req.no_review
    _web_interface.no_clean = req.no_clean
    _web_interface.no_repair = req.no_repair

    # Set queue item on the interface so ui.py removes it after completion
    _web_interface._current_queue_item = queue_item

    def run():
        try:
            _web_interface.run_translation()
        except Exception as e:
            _job_manager.status = "error"
            _job_manager.error = str(e)
            _job_manager.send_message_sync({"type": "error", "message": str(e)})
        finally:
            _job_manager.is_running = False
            if _job_manager.status not in ("error", "awaiting_review"):
                _job_manager.status = "complete"

    thread = threading.Thread(target=run, daemon=True)
    thread.start()

    return {
        "status": "started",
        "item": {"title": queue_item.get("title"), "book_id": queue_item["book_id"]},
    }
