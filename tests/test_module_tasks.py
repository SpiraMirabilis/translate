"""Background module-task runner: threading semantics + guardrails.

Module enable/disable backfills (and settings rebuilds) run on
``modules.task_runner.module_task_runner`` — one task per book. While a book
has an active task, module toggles, module-settings changes and book deletion
must be refused with ModuleTaskBusyError / HTTP 409.
"""
import threading

import pytest

from modules import (REGISTRY, ModuleTaskBusyError, apply_module_settings_change,
                     module_task_runner)
from modules.task_runner import ModuleTaskRunner


@pytest.fixture(autouse=True)
def clean_runner():
    """Drain and clear the singleton runner after each test.

    Each test gets a fresh SQLite DB, so book ids repeat (1, 2, …) across
    tests — leftover claims or finished-task records on the process-wide
    singleton would bleed into the next test's assertions.
    """
    yield
    with module_task_runner._lock:
        threads = list(module_task_runner._threads.values())
    for t in threads:
        t.join(timeout=10)
    with module_task_runner._lock:
        module_task_runner._active.clear()
        module_task_runner._threads.clear()
        module_task_runner._last.clear()


# ---------------------------------------------------------------------------
# Runner unit semantics (standalone instance)
# ---------------------------------------------------------------------------

def test_claim_is_exclusive_and_releasable():
    r = ModuleTaskRunner()
    r.claim(1, "first")
    assert r.active(1)["state"] == "pending"
    with pytest.raises(ModuleTaskBusyError):
        r.claim(1, "second")
    r.claim(2, "other book")  # other books are independent
    r.release(1)
    assert r.active(1) is None
    r.claim(1, "again")  # slot is reusable after release


def test_start_requires_claim_and_records_completion():
    r = ModuleTaskRunner()
    with pytest.raises(RuntimeError):
        r.start(1, lambda: None)

    release = threading.Event()
    r.claim(1, "claimed")
    r.start(1, lambda: release.wait(10), label="running label")
    assert r.active(1)["state"] == "running"
    assert r.active(1)["label"] == "running label"
    assert r.join(1, timeout=0.2) is False  # still running
    release.set()
    assert r.join(1, timeout=10) is True
    st = r.status(1)
    assert st["running"] is None
    assert st["last"]["state"] == "done"
    assert st["last"]["error"] is None
    # release() must not clear a running/finished record (pending-only).
    assert r.active(1) is None


def test_failed_task_records_error():
    r = ModuleTaskRunner()
    r.claim(1, "boom")

    def fail():
        raise ValueError("kaput")

    r.start(1, fail)
    assert r.join(1, timeout=10)
    st = r.status(1)
    assert st["running"] is None
    assert st["last"]["state"] == "error"
    assert "kaput" in st["last"]["error"]


# ---------------------------------------------------------------------------
# update_book: background backfill + busy guardrails
# ---------------------------------------------------------------------------

def test_update_book_module_toggle_backfills_in_background(db, monkeypatch):
    book_id = db.create_book("Mod Book")
    db.save_chapter(book_id, 1, "C1", ["src"], ["【Alert】", "", "prose"])

    started = threading.Event()
    release = threading.Event()
    mod = REGISTRY["markdown_notifications"]
    orig = mod.event_add_to_book

    def slow_add(ctx):
        started.set()
        release.wait(10)
        orig(ctx)

    monkeypatch.setattr(mod, "event_add_to_book", slow_add)

    assert db.update_book(book_id, modules={"markdown_notifications": True}) is True
    assert started.wait(5), "backfill thread never started"
    active = module_task_runner.active(book_id)
    assert active and active["state"] == "running"
    assert "markdown_notifications" in active["label"]

    # Guardrails while the backfill runs:
    with pytest.raises(ModuleTaskBusyError):
        db.update_book(book_id, modules={})  # module toggle blocked
    with pytest.raises(ModuleTaskBusyError):
        apply_module_settings_change(
            db, db.get_book(book_id=book_id), "chatgroup_transformer",
            {"restrict_to_entities": False}, db.config, db.logger)
    # ...but unrelated book edits still go through.
    assert db.update_book(book_id, title="Renamed") is True

    release.set()
    assert module_task_runner.join(book_id, timeout=10)
    st = module_task_runner.status(book_id)
    assert st["running"] is None and st["last"]["state"] == "done"

    # The backfill actually ran: the 【…】 line became a table.
    ch = db.get_chapter(book_id=book_id, chapter_number=1)
    assert ch["content"][0] == "| Alert |"
    assert ch["content"][1] == "| --- |"


def test_update_book_without_module_diff_releases_slot(db):
    book_id = db.create_book("NoDiff Book")
    # 'modules' in kwargs but the enabled set doesn't change → the claimed
    # slot must be released, not leaked.
    assert db.update_book(book_id, modules=None) is True
    assert module_task_runner.active(book_id) is None
    # Slot is immediately reusable.
    assert db.update_book(book_id, modules={"markdown_notifications": True}) is True
    assert module_task_runner.join(book_id, timeout=10)


# ---------------------------------------------------------------------------
# Settings rebuild: background remove→add with old settings visible to remove
# ---------------------------------------------------------------------------

def test_settings_rebuild_runs_in_background_with_old_settings(db, monkeypatch):
    book_id = db.create_book("Settings Book")
    assert db.update_book(book_id, modules={"chatgroup_transformer": True}) is True
    assert module_task_runner.join(book_id, timeout=10)

    seen = {}
    mod = REGISTRY["chatgroup_transformer"]
    monkeypatch.setattr(mod, "event_removed_from_book", lambda ctx: seen.update(
        removed=(ctx.get("module_settings") or {}).get("chatgroup_transformer")))
    monkeypatch.setattr(mod, "event_add_to_book", lambda ctx: seen.update(
        added=(ctx.get("module_settings") or {}).get("chatgroup_transformer")))

    book = db.get_book(book_id=book_id)
    ok = apply_module_settings_change(
        db, book, "chatgroup_transformer", {"restrict_to_entities": False},
        db.config, db.logger)
    assert ok is True
    assert module_task_runner.join(book_id, timeout=10)
    st = module_task_runner.status(book_id)
    assert st["running"] is None and st["last"]["state"] == "done"

    # Reverse pass saw the OLD (empty) stored settings; add pass saw the new.
    assert not seen["removed"]
    assert seen["added"] == {"restrict_to_entities": False}
    # And the settings were persisted.
    assert db.get_module_settings(book_id, "chatgroup_transformer") == {
        "restrict_to_entities": False}


def test_settings_change_without_rebuild_persists_synchronously(db):
    book_id = db.create_book("Plain Settings Book")
    # Module NOT enabled → no rebuild, just persistence; no task spawned.
    book = db.get_book(book_id=book_id)
    ok = apply_module_settings_change(
        db, book, "chatgroup_transformer", {"restrict_to_entities": False},
        db.config, db.logger)
    assert ok is True
    assert module_task_runner.active(book_id) is None


# ---------------------------------------------------------------------------
# HTTP guardrails
# ---------------------------------------------------------------------------

def test_module_task_http_endpoints(db, admin_client):
    book_id = db.create_book("HTTP Mod Book")

    r = admin_client.get(f"/api/books/{book_id}/module-task")
    assert r.status_code == 200
    assert r.json() == {"running": None, "last": None}

    module_task_runner.claim(book_id, "test task")
    try:
        r = admin_client.get(f"/api/books/{book_id}/module-task")
        assert r.json()["running"]["label"] == "test task"

        r = admin_client.delete(f"/api/books/{book_id}")
        assert r.status_code == 409

        r = admin_client.put(f"/api/books/{book_id}",
                             json={"modules": {"markdown_notifications": True}})
        assert r.status_code == 409

        r = admin_client.put(
            f"/api/books/{book_id}/modules/chatgroup_transformer/settings",
            json={"settings": {"restrict_to_entities": False}})
        assert r.status_code == 409
    finally:
        module_task_runner.release(book_id)

    # After release everything works again.
    r = admin_client.put(f"/api/books/{book_id}", json={"title": "Still Here"})
    assert r.status_code == 200
