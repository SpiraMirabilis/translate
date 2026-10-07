"""API-log listing: metadata-only pages of sessions, texts on demand.

The list used to return every call's prompts and response — 59 MB for the
default 500 calls — and sorted on an unindexed created_at. It now pages by
session over the primary key and leaves the texts to get_api_call_session.
"""


def _log(db, session_id, book_id=None, chapter=1, chunk=1, attempt=0):
    return db.log_api_call(session_id, book_id, chapter, chunk, 2,
                           "SYSTEM " * 50, "SOURCE", "RESPONSE",
                           "model", "provider", attempt=attempt)


def _all_pages(db, **kw):
    pages, before = [], None
    while True:
        sessions, before = db.list_api_call_sessions(before=before, **kw)
        pages.append([s["session_id"] for s in sessions])
        if before is None:
            return pages


def test_list_carries_no_texts(db):
    _log(db, "s1")
    sessions, _ = db.list_api_call_sessions()
    call = sessions[0]["calls"][0]
    assert not {"system_prompt", "user_prompt", "response_text"} & set(call)


def test_session_detail_carries_texts_in_chunk_order(db):
    _log(db, "s1", chunk=2)
    _log(db, "s1", chunk=1, attempt=1)
    _log(db, "s1", chunk=1)
    calls = db.get_api_call_session("s1")
    assert [(c["chunk_index"], c["attempt"]) for c in calls] == [(1, 0), (1, 1), (2, 0)]
    assert calls[0]["response_text"] == "RESPONSE"


def test_pages_newest_first_without_overlap_or_gaps(db):
    for i in range(7):
        _log(db, f"s{i}")
    pages = _all_pages(db, limit=3)
    assert pages == [["s6", "s5", "s4"], ["s3", "s2", "s1"], ["s0"]]


def test_interleaved_sessions_appear_exactly_once(db):
    # Concurrent jobs interleave their calls: a, b, a, c, b, d, a
    for sid in ["a", "b", "a", "c", "b", "d", "a"]:
        _log(db, sid)
    pages = _all_pages(db, limit=2)
    flat = [sid for page in pages for sid in page]
    # Ordered by each session's newest call: a(7) d(6) b(5) c(4)
    assert flat == ["a", "d", "b", "c"]


def test_session_groups_all_its_calls_even_across_a_page_cut(db):
    _log(db, "old", chunk=1)
    _log(db, "new")
    _log(db, "old", chunk=2)
    sessions, _ = db.list_api_call_sessions(limit=1)
    assert sessions[0]["session_id"] == "old"
    assert len(sessions[0]["calls"]) == 2


def test_book_and_chapter_filters(db):
    book = db.create_book("Log Book")
    other = db.create_book("Other Book")
    _log(db, "b1c1", book_id=book, chapter=1)
    _log(db, "b1c2", book_id=book, chapter=2)
    _log(db, "b2", book_id=other)
    assert _all_pages(db, book_id=book) == [["b1c2", "b1c1"]]
    assert _all_pages(db, book_id=book, chapter_number=1) == [["b1c1"]]
    sessions, _ = db.list_api_call_sessions(book_id=book)
    assert sessions[0]["book_title"] == "Log Book"
