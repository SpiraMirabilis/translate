"""A published prompt must be visible to the very next snapshot.

`/api/translate/jobs` gates each pending_* payload on the matching awaiting_*
status. The status used to be set later — by the blocking wait_for_* — so a
client hydrating in between was told "running, nothing pending" and erased the
modal it had just opened off the WebSocket message, leaving the run parked with
no way to answer it.
"""
import threading

import pytest

from web.api.translation import _job_state
from web.services.job_manager import Job, JobHub


@pytest.fixture
def job():
    return Job(JobHub(), book_id=14)


@pytest.mark.parametrize("kind, status, payload", [
    ("review", "awaiting_review", {"entities": {}, "context": "x"}),
    ("json_fix", "awaiting_json_fix", {"raw_response": "{", "chunk_index": 1}),
    ("chapter_conflict", "awaiting_chapter_conflict", {"chapter_number": 7}),
])
def test_payload_and_status_are_published_together(job, kind, status, payload):
    job.status = "running"
    job.await_prompt(kind, payload)

    state = _job_state(job)
    assert state["status"] == status
    assert state[f"pending_{kind}"] == payload


def test_state_is_visible_before_the_worker_blocks(job):
    """The gap that mattered: the broadcast and the activity-log write happen
    between publishing the payload and the thread parking on it."""
    job.await_prompt("review", {"entities": {}, "context": "x"})
    seen = {}

    worker = threading.Thread(target=lambda: seen.update(result=job.wait_for_review()))
    worker.start()
    try:
        # Whatever the worker has managed to do so far, the snapshot is complete.
        assert _job_state(job)["pending_review"] is not None
        assert _job_state(job)["status"] == "awaiting_review"
    finally:
        job.submit_review({"ok": True})
        worker.join(timeout=5)

    assert seen["result"] == {"ok": True}
    assert job.pending_review is None
    assert job.status == "running"
