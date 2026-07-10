"""Shared FastAPI dependencies / lookup helpers for the admin API."""
import threading
from collections import defaultdict

from fastapi import HTTPException

_db = None

# Per-chapter save mutexes. The optimistic-lock check in the chapter save /
# revision restore endpoints is read-compare-write; serializing per chapter
# turns it into an actual compare-and-swap within this process (the only
# writer for editor saves).
_chapter_locks = defaultdict(threading.Lock)
_chapter_locks_guard = threading.Lock()


def chapter_save_lock(book_id: int, chapter_number: int) -> threading.Lock:
    with _chapter_locks_guard:
        return _chapter_locks[(int(book_id), int(chapter_number))]


def init(db_manager):
    global _db
    _db = db_manager


def get_book_or_404(book_id: int) -> dict:
    """Resolve a book by id or raise 404.

    Usable both as a FastAPI dependency (`book: dict = Depends(get_book_or_404)`)
    and as a plain call inside handlers (`book = get_book_or_404(book_id)`).
    """
    book = _db.get_book(book_id=book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found.")
    return book
