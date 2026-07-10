"""
Chapter revision history endpoints (write editor).

Revisions are recorded by the chapter save path in web/api/books.py
(manual snapshots on explicit save, coalesced auto snapshots on autosave);
this router covers listing, fetching, and restoring them.
"""
from fastapi import APIRouter, HTTPException

from web.api.deps import get_book_or_404, chapter_save_lock

router = APIRouter(prefix="/api/books")

_entity_manager = None


def init(entity_manager):
    global _entity_manager
    _entity_manager = entity_manager


def _get_revision_or_404(book_id: int, chapter_number: int, revision_id: int):
    revision = _entity_manager.get_chapter_revision(revision_id)
    if not revision or revision["book_id"] != book_id or revision["chapter_number"] != chapter_number:
        raise HTTPException(status_code=404, detail="Revision not found.")
    return revision


@router.get("/{book_id}/chapters/{chapter_number}/revisions")
def list_revisions(book_id: int, chapter_number: int):
    get_book_or_404(book_id)
    return {"revisions": _entity_manager.list_chapter_revisions(book_id, chapter_number)}


@router.get("/{book_id}/chapters/{chapter_number}/revisions/{revision_id}")
def get_revision(book_id: int, chapter_number: int, revision_id: int):
    return _get_revision_or_404(book_id, chapter_number, revision_id)


@router.post("/{book_id}/chapters/{chapter_number}/revisions/{revision_id}/restore")
def restore_revision(book_id: int, chapter_number: int, revision_id: int):
    revision = _get_revision_or_404(book_id, chapter_number, revision_id)
    # Same per-chapter mutex as the save endpoint: a restore racing an
    # editor save could otherwise interleave its snapshot-then-write with
    # the save's compare-then-write.
    with chapter_save_lock(book_id, chapter_number):
        chapter = _entity_manager.get_chapter(book_id=book_id, chapter_number=chapter_number)
        if not chapter:
            raise HTTPException(status_code=404, detail="Chapter not found.")

        # Safety net: snapshot the current content before overwriting it.
        _entity_manager.add_chapter_revision(
            book_id, chapter_number, chapter.get("title"), chapter.get("content", []), kind='auto')

        chapter_id = _entity_manager.save_chapter(
            book_id=book_id,
            chapter_number=chapter_number,
            title=revision.get("title") or chapter.get("title", f"Chapter {chapter_number}"),
            untranslated_content=chapter.get("untranslated", []),
            translated_content=revision["content"],
            summary=chapter.get("summary"),
            translation_model=chapter.get("model"),
        )
        if not chapter_id:
            raise HTTPException(status_code=500, detail="Failed to restore revision.")

        saved = _entity_manager.get_chapter(book_id=book_id, chapter_number=chapter_number)
        return {"status": "ok", "translation_date": saved.get("translation_date") if saved else None}
