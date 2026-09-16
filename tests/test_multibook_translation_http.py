"""HTTP-level tests for concurrent per-book translation jobs.

Exercises the real start/status/cancel endpoints. The translation itself is
stubbed: the worker just parks until the test releases it, so the registry's
slot accounting can be observed without making a model call.
"""
import threading

import pytest


class StubInterface:
    """Stands in for WebInterface: blocks in run_translation until released."""

    def __init__(self, job, gate):
        self.job_manager = job
        self._gate = gate
        self.entity_manager = None

    def run_translation(self):
        self._gate.wait(timeout=10)


@pytest.fixture
def gate():
    return threading.Event()


@pytest.fixture
def stub_runs(web_app, gate, monkeypatch):
    """Replace the per-run WebInterface factory with a blocking stub."""
    import web.api.queue_api as queue_api
    import web.api.translation as translation_api

    def factory(job, translation_model=None, advice_model=None):
        return StubInterface(job, gate)

    monkeypatch.setattr(translation_api, "_make_web_interface", factory)
    monkeypatch.setattr(queue_api, "_make_web_interface", factory)

    registry = translation_api._registry
    yield registry

    # Never leave a parked worker thread behind holding a slot.
    gate.set()
    for job in registry.active():
        registry.end(job.book_id)


def _start(admin_client, book_id, chapter=1):
    return admin_client.post("/api/translate", json={
        "text": "第一章\n内容", "book_id": book_id, "chapter_number": chapter,
        "no_review": True,
    })


def test_two_books_translate_at_once(admin_client, stub_runs):
    assert _start(admin_client, 14).status_code == 200
    assert _start(admin_client, 79).status_code == 200

    jobs = admin_client.get("/api/translate/jobs").json()
    assert jobs["running"] == 2
    assert {j["book_id"] for j in jobs["jobs"]} == {14, 79}


def test_same_book_twice_is_refused(admin_client, stub_runs):
    assert _start(admin_client, 14).status_code == 200

    second = _start(admin_client, 14, chapter=2)
    assert second.status_code == 409
    assert "already translating" in second.json()["detail"]


def test_capacity_limit_is_enforced(admin_client, stub_runs, monkeypatch):
    monkeypatch.setattr(stub_runs, "_max_concurrent", 2)

    assert _start(admin_client, 1).status_code == 200
    assert _start(admin_client, 2).status_code == 200

    third = _start(admin_client, 3)
    assert third.status_code == 409
    assert "slots are in use" in third.json()["detail"]


def test_status_reports_aggregate_and_per_book(admin_client, stub_runs):
    _start(admin_client, 14)
    _start(admin_client, 79)

    body = admin_client.get("/api/translate/status").json()

    # Aggregates translation_status.py depends on.
    assert body["is_running"] is True
    assert body["status"] == "running"
    # Per-book breakdown for the multi-job UI.
    assert set(body["jobs"]) == {"14", "79"}
    assert body["jobs"]["14"]["book_id"] == 14
    assert body["running"] == 2


def test_status_is_idle_with_nothing_running(admin_client, stub_runs):
    body = admin_client.get("/api/translate/status").json()
    assert body["status"] == "idle"
    assert body["is_running"] is False
    assert body["jobs"] == {}


def test_cancel_targets_one_book_and_leaves_the_other(admin_client, stub_runs):
    _start(admin_client, 14)
    _start(admin_client, 79)

    resp = admin_client.post("/api/translate/cancel", json={"book_id": 14})
    assert resp.status_code == 200
    assert resp.json()["cancelled"] == [14]

    assert stub_runs.get(14).is_cancelled()
    assert not stub_runs.get(79).is_cancelled(), "book 79 must keep running"


def test_cancel_without_book_id_stops_everything(admin_client, stub_runs):
    _start(admin_client, 14)
    _start(admin_client, 79)

    resp = admin_client.post("/api/translate/cancel", json={})
    assert sorted(resp.json()["cancelled"]) == [14, 79]


def test_cancel_with_nothing_running_is_not_an_error(admin_client, stub_runs):
    resp = admin_client.post("/api/translate/cancel", json={})
    assert resp.status_code == 200
    assert resp.json()["status"] == "not_running"


def test_finished_book_can_start_again(admin_client, stub_runs, gate):
    assert _start(admin_client, 14).status_code == 200

    gate.set()  # let the worker finish and release the slot
    for _ in range(200):
        if not stub_runs.running_book_ids():
            break
        threading.Event().wait(0.01)

    assert _start(admin_client, 14, chapter=2).status_code == 200


# ------------------------------------------------------------------
# Queue-driven runs
# ------------------------------------------------------------------

def _make_book(admin_client, title):
    resp = admin_client.post("/api/books", json={"title": title, "author": "A"})
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _enqueue(admin_client, book_id, chapter):
    resp = admin_client.post("/api/queue/add", json={
        "text": f"第{chapter}章\n内容", "book_id": book_id, "chapter_number": chapter,
    })
    assert resp.status_code == 200, resp.text


def test_process_all_starts_one_worker_per_queued_book(admin_client, stub_runs):
    a = _make_book(admin_client, "Queue Book A")
    b = _make_book(admin_client, "Queue Book B")
    for book_id in (a, b):
        _enqueue(admin_client, book_id, 1)
        _enqueue(admin_client, book_id, 2)

    body = admin_client.post("/api/queue/process-all", json={"no_review": True}).json()

    assert {s["book_id"] for s in body["started"]} == {a, b}
    assert body["running"] == 2
    # One worker per book, not one per queued chapter.
    assert len(body["started"]) == 2


def test_process_all_skips_a_book_already_running(admin_client, stub_runs):
    a = _make_book(admin_client, "Queue Book A")
    b = _make_book(admin_client, "Queue Book B")
    _enqueue(admin_client, a, 1)
    _enqueue(admin_client, b, 1)

    assert _start(admin_client, a).status_code == 200

    body = admin_client.post("/api/queue/process-all", json={"no_review": True}).json()

    assert [s["book_id"] for s in body["started"]] == [b]
    assert [s["book_id"] for s in body["skipped"]] == [a]
    assert body["skipped"][0]["reason"] == "already_running"


def test_process_all_stops_at_the_cap(admin_client, stub_runs, monkeypatch):
    monkeypatch.setattr(stub_runs, "_max_concurrent", 1)
    a = _make_book(admin_client, "Queue Book A")
    b = _make_book(admin_client, "Queue Book B")
    _enqueue(admin_client, a, 1)
    _enqueue(admin_client, b, 1)

    body = admin_client.post("/api/queue/process-all", json={"no_review": True}).json()

    assert len(body["started"]) == 1
    assert body["skipped"][0]["reason"] == "at_capacity"


def test_process_next_without_a_book_picks_an_idle_one(admin_client, stub_runs):
    a = _make_book(admin_client, "Queue Book A")
    b = _make_book(admin_client, "Queue Book B")
    _enqueue(admin_client, a, 1)
    _enqueue(admin_client, b, 1)

    first = admin_client.post("/api/queue/process-next", json={"no_review": True})
    second = admin_client.post("/api/queue/process-next", json={"no_review": True})

    assert first.status_code == 200 and second.status_code == 200
    # The second call must not pick the book the first is already draining.
    assert stub_runs.running_book_ids() == {a, b}


def test_process_next_reports_when_every_queued_book_is_busy(admin_client, stub_runs):
    a = _make_book(admin_client, "Queue Book A")
    _enqueue(admin_client, a, 1)
    _enqueue(admin_client, a, 2)

    assert admin_client.post("/api/queue/process-next", json={"no_review": True}).status_code == 200

    second = admin_client.post("/api/queue/process-next", json={"no_review": True})
    assert second.status_code == 409
    assert "already translating" in second.json()["detail"]


def test_release_is_blocked_only_for_the_owning_book(admin_client, stub_runs):
    a = _make_book(admin_client, "Queue Book A")
    b = _make_book(admin_client, "Queue Book B")
    _enqueue(admin_client, a, 1)
    _enqueue(admin_client, b, 1)

    # Book A's worker claims A's row.
    assert admin_client.post(
        "/api/queue/process-next", json={"book_id": a, "no_review": True}).status_code == 200

    rows = admin_client.get("/api/queue").json()["items"]
    a_row = next(r for r in rows if r["book_id"] == a)
    b_row = next(r for r in rows if r["book_id"] == b)

    blocked = admin_client.post(f"/api/queue/{a_row['id']}/release", json={})
    assert blocked.status_code == 409, "its own worker holds this row"

    # Book B is idle, so its rows stay releasable while A translates.
    allowed = admin_client.post(f"/api/queue/{b_row['id']}/release", json={})
    assert allowed.status_code in (200, 404)
