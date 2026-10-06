"""Per-job run options and remaining-chapter count on the status payload.

A caller that pauses a book (stop-auto, wait for the in-flight chapter) needs
to restart it with the same models and flags; before this the options lived
only in process_next's closure.
"""
from web.api.translation import _job_state
from web.services.job_manager import Job, JobHub


def _job():
    return Job(JobHub(), book_id=7)


def test_fresh_job_reports_nothing():
    state = _job_state(_job())
    assert state["run_options"] is None
    assert state["auto_remaining"] is None


def test_unlimited_auto_reports_no_remaining():
    job = _job()
    job.start_auto_process(max_chapters=None)
    assert _job_state(job)["auto_remaining"] is None


def test_remaining_counts_down_as_the_loop_advances():
    job = _job()
    job.start_auto_process(max_chapters=3)
    assert job.auto_remaining == 2          # the first chapter is in flight
    assert job.should_continue_auto()
    assert job.auto_remaining == 1
    assert job.should_continue_auto()
    assert job.auto_remaining == 0
    assert not job.should_continue_auto()
    assert job.auto_remaining == 0


def test_stopped_auto_reports_no_remaining():
    job = _job()
    job.start_auto_process(max_chapters=5)
    job.stop_auto_process()
    assert job.auto_remaining is None


def test_run_options_are_echoed():
    job = _job()
    job.run_options = {"translation_model": "claude:x", "no_review": True,
                       "auto_process": True, "max_chapters": 10}
    assert _job_state(job)["run_options"]["translation_model"] == "claude:x"


def test_process_next_records_run_options(web_app, monkeypatch):
    """The endpoint stores the request's options (minus book_id) on the job."""
    from web.api import queue_api

    captured = {}

    class Stop(Exception):
        pass

    def fake_setup(job, wi, item, settings):
        captured["job"] = job

    monkeypatch.setattr(queue_api, "_setup_job", fake_setup)
    monkeypatch.setattr(queue_api, "_make_web_interface", lambda *a, **k: object())
    monkeypatch.setattr(queue_api, "_pick_book_to_process", lambda b: 7)
    monkeypatch.setattr(queue_api._entity_manager, "claim_next_queue_item",
                        lambda book_id=None: {"id": 1, "book_id": 7, "chapter_number": 3})
    monkeypatch.setattr(queue_api._entity_manager, "get_book", lambda b: {"title": "T"})

    import threading
    monkeypatch.setattr(threading, "Thread",
                        lambda *a, **k: type("T", (), {"start": lambda self: None})())

    req = queue_api.ProcessNextRequest(book_id=7, auto_process=True, max_chapters=4,
                                       translation_model="claude:x", no_review=True)
    try:
        queue_api.process_next(req)
    finally:
        queue_api._registry.end(7)
    opts = captured["job"].run_options
    assert opts["translation_model"] == "claude:x"
    assert opts["no_review"] is True
    assert opts["max_chapters"] == 4
    assert opts["auto_process"] is True
    assert "book_id" not in opts
