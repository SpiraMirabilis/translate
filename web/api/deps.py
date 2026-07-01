"""Shared FastAPI dependencies / lookup helpers for the admin API."""
from fastapi import HTTPException

_db = None


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
