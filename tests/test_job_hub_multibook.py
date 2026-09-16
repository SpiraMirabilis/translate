"""Per-book routing on a shared JobHub.

Several books translate at once through one WebSocket, so every message has to
say which job produced it, and per-book state in the replay buffer must not
clobber another book's.
"""
import asyncio

from web.services.job_manager import Job, JobHub


class FakeWS:
    def __init__(self):
        self.sent = []

    async def send_json(self, message):
        self.sent.append(message)


def test_messages_are_stamped_with_book_and_job():
    hub = JobHub()
    job = Job(hub, book_id=14)

    job.send_message_sync({"type": "translation_complete", "chapter": 7})

    loop = asyncio.new_event_loop()
    try:
        (msg,) = job.add_websocket(FakeWS(), loop)
    finally:
        loop.close()

    assert msg["book_id"] == 14
    assert msg["job_id"] == job.job_id


def test_explicit_book_id_in_payload_wins():
    """chapter_conflict_needed carries the *incoming* chapter's book."""
    hub = JobHub()
    job = Job(hub, book_id=14)

    job.send_message_sync({"type": "chapter_conflict_needed", "book_id": 79})

    loop = asyncio.new_event_loop()
    try:
        (msg,) = job.add_websocket(FakeWS(), loop)
    finally:
        loop.close()

    assert msg["book_id"] == 79


def test_jobs_get_distinct_ids():
    hub = JobHub()
    assert Job(hub, book_id=1).job_id != Job(hub, book_id=2).job_id


def test_terminal_events_collapse_per_book_not_globally():
    """Book A finishing must not evict book B's completion from the replay.

    Collapsing on type alone meant the second book to finish erased the first,
    so a tab connecting afterwards never learned book A was done.
    """
    hub = JobHub()
    a, b = Job(hub, book_id=14), Job(hub, book_id=79)

    a.send_message_sync({"type": "translation_complete", "chapter": 1})
    b.send_message_sync({"type": "translation_complete", "chapter": 2})
    a.send_message_sync({"type": "translation_complete", "chapter": 3})

    loop = asyncio.new_event_loop()
    try:
        backlog = a.add_websocket(FakeWS(), loop)
    finally:
        loop.close()

    by_book = {m["book_id"]: m["chapter"] for m in backlog}
    assert by_book == {14: 3, 79: 2}, "each book keeps exactly its newest outcome"


def test_resolving_one_books_prompt_leaves_the_others_open():
    hub = JobHub()
    a, b = Job(hub, book_id=14), Job(hub, book_id=79)

    a.send_message_sync({"type": "entity_review_needed", "entities": {}})
    b.send_message_sync({"type": "entity_review_needed", "entities": {}})

    a.submit_review({"entities": {}})

    loop = asyncio.new_event_loop()
    try:
        backlog = a.add_websocket(FakeWS(), loop)
    finally:
        loop.close()

    assert [m["book_id"] for m in backlog] == [79]


def test_jobs_have_independent_cancel_and_review_state():
    hub = JobHub()
    a, b = Job(hub, book_id=14), Job(hub, book_id=79)

    a.request_cancel()
    assert a.is_cancelled()
    assert not b.is_cancelled(), "cancelling one book must not stop the other"

    a.try_begin_job()
    assert a.is_running and not b.is_running
    assert b.try_begin_job(), "a second book can start while the first runs"


def test_activity_log_defaults_to_the_jobs_book():
    class FakeDB:
        def __init__(self):
            self.entries = []

        def add_activity_log(self, **kw):
            self.entries.append(kw)
            return {"id": len(self.entries), **kw}

    hub = JobHub()
    hub.db_manager = FakeDB()
    job = Job(hub, book_id=14)

    job.log_activity(type="error", message="boom")
    job.log_activity(type="info", message="elsewhere", book_id=79)

    assert [e["book_id"] for e in hub.db_manager.entries] == [14, 79]
