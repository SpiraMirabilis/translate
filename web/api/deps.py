"""Shared FastAPI dependencies / lookup helpers for the admin API."""
import threading

from fastapi import HTTPException

_db = None

# Per-chapter save mutexes. The optimistic-lock check in the chapter save /
# revision restore endpoints is read-compare-write; serializing per chapter
# turns it into an actual compare-and-swap within this process (the only
# writer for editor saves).
_chapter_locks = {}
_chapter_locks_guard = threading.Lock()
_CHAPTER_LOCKS_MAX = 512  # prune oldest when this many live keys accumulate


def chapter_save_lock(book_id: int, chapter_number: int) -> threading.Lock:
    key = (int(book_id), int(chapter_number))
    with _chapter_locks_guard:
        lock = _chapter_locks.get(key)
        if lock is None:
            # Bound growth: drop idle locks when the map is large. A lock
            # currently held by another thread is fine to drop from the map —
            # that thread still holds the Lock object; the next save for the
            # same chapter will just allocate a new one (serialisation only
            # matters for concurrent savers of the *same* chapter).
            if len(_chapter_locks) >= _CHAPTER_LOCKS_MAX:
                # Drop roughly the oldest half (insertion order, Py3.7+).
                for old_key in list(_chapter_locks.keys())[: _CHAPTER_LOCKS_MAX // 2]:
                    del _chapter_locks[old_key]
            lock = threading.Lock()
            _chapter_locks[key] = lock
        return lock


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
