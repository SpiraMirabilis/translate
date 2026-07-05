"""Debounced module-activity summaries (modules/activity.py).

Ingest transforms accumulate per (book, module, side) and flush as ONE
activity-log line — explicitly at batch boundaries, or via the fallback sweep
thread after a quiet period. Backfills log a one-shot summary directly.
"""
import time

import pytest

from modules import (apply_translated_ingest, module_activity,
                     module_task_runner, set_activity_notifier)
from modules.activity import ModuleActivityAggregator, log_module_activity


class FakeDB:
    def __init__(self):
        self.entries = []

    def add_activity_log(self, type, message, book_id=None, **kwargs):
        self.entries.append({"type": type, "message": message, "book_id": book_id})
        return self.entries[-1]


@pytest.fixture(autouse=True)
def clean_singletons():
    """Reset the process-wide aggregator + notifier around each test.

    The notifier must also be cleared BEFORE the test: any earlier test that
    built the web app (create_app) installed job_manager.log_activity globally.
    """
    set_activity_notifier(None)
    yield
    set_activity_notifier(None)
    with module_activity._lock:
        module_activity._pending.clear()
    with module_task_runner._lock:
        threads = list(module_task_runner._threads.values())
    for t in threads:
        t.join(timeout=10)
    with module_task_runner._lock:
        module_task_runner._active.clear()
        module_task_runner._threads.clear()
        module_task_runner._last.clear()


def test_records_aggregate_into_one_summary_line():
    agg = ModuleActivityAggregator(quiet_seconds=999, sweep_interval=999)
    db = FakeDB()
    for _ in range(214):
        agg.record(db, 7, "chatgroup_transformer", "source")
    agg.record(db, 7, "chatgroup_transformer", "translated")  # separate side
    agg.record(db, 8, "chatgroup_transformer", "source")      # separate book

    agg.flush(book_id=7)
    assert len(db.entries) == 2
    msgs = sorted(e["message"] for e in db.entries)
    assert "transformed 1 item during translated ingest" in msgs[0]
    assert "transformed 214 items during source ingest" in msgs[1]
    assert all(e["book_id"] == 7 for e in db.entries)
    # Book 8 is still pending — flush(7) must not touch it.
    agg.flush()
    assert len(db.entries) == 3
    # Everything flushed; a second flush is a no-op.
    agg.flush()
    assert len(db.entries) == 3


def test_sweep_thread_flushes_after_quiet_period():
    agg = ModuleActivityAggregator(quiet_seconds=0.05, sweep_interval=0.02)
    db = FakeDB()
    agg.record(db, 1, "markdown_notifications", "translated")
    deadline = time.monotonic() + 5
    while not db.entries and time.monotonic() < deadline:
        time.sleep(0.02)
    assert len(db.entries) == 1
    assert "Markdown Notifications transformed 1 item" in db.entries[0]["message"]
    # Sweeper exits once idle; a later record restarts it.
    agg.record(db, 1, "markdown_notifications", "translated")
    deadline = time.monotonic() + 5
    while len(db.entries) < 2 and time.monotonic() < deadline:
        time.sleep(0.02)
    assert len(db.entries) == 2


def test_notifier_takes_precedence_over_db():
    seen = []
    set_activity_notifier(
        lambda type, message, book_id=None: seen.append((type, message, book_id)))
    db = FakeDB()
    log_module_activity(db, "info", "hello", 3)
    assert seen == [("info", "hello", 3)]
    assert db.entries == []  # notifier handled it
    # Broken notifier falls back to the DB write.
    set_activity_notifier(lambda **kw: (_ for _ in ()).throw(RuntimeError("ws down")))
    log_module_activity(db, "info", "fallback", 3)
    assert db.entries and db.entries[0]["message"] == "fallback"


def test_translated_ingest_records_and_flushes_through_real_db(db):
    book_id = db.create_book("Activity Book")
    assert db.update_book(book_id, modules={"markdown_notifications": True}) is True
    assert module_task_runner.join(book_id, timeout=10)

    book = db.get_book(book_id=book_id)
    out = apply_translated_ingest(
        book, ["【Alert】", "", "prose"], db.config, db.logger, db=db)
    assert out[0] == "| Alert |"  # the transform really ran

    seen = []
    set_activity_notifier(
        lambda type, message, book_id=None: seen.append((message, book_id)))
    module_activity.flush(book_id)
    assert len(seen) == 1
    assert seen[0] == (
        "Markdown Notifications transformed 1 item during translated ingest",
        book_id)

    # No-op re-ingest (already tables) records nothing.
    out2 = apply_translated_ingest(book, out, db.config, db.logger, db=db)
    assert out2 == out
    module_activity.flush(book_id)
    assert len(seen) == 1


def test_backfill_logs_one_summary(db):
    book_id = db.create_book("Backfill Log Book")
    db.save_chapter(book_id, 1, "C1", ["src"], ["【A】"])
    db.save_chapter(book_id, 2, "C2", ["src"], ["【B】"])

    seen = []
    set_activity_notifier(
        lambda type, message, book_id=None: seen.append((message, book_id)))
    assert db.update_book(book_id, modules={"markdown_notifications": True}) is True
    assert module_task_runner.join(book_id, timeout=10)
    assert seen == [
        ("Markdown Notifications: converted 2 chapter(s)", book_id)]
