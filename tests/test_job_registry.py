"""One job per book, and a ceiling on how many run at once.

Chapters within a book must stay sequential — the entity glossary is built
incrementally — so the registry refuses a second job for a book that is already
translating. Different books share nothing, so they run in parallel up to a
configurable cap (each worker is another MySQL pool consumer and another
concurrent provider stream).
"""
import pytest

from web.services.job_manager import (
    JobBusyError, JobCapacityError, JobHub, JobRegistry,
)


@pytest.fixture
def registry():
    return JobRegistry(JobHub(), max_concurrent=3)


def test_different_books_run_in_parallel(registry):
    a = registry.begin(14)
    b = registry.begin(79)

    assert a.book_id == 14 and b.book_id == 79
    assert a.job_id != b.job_id
    assert registry.running_book_ids() == {14, 79}


def test_second_job_for_the_same_book_is_refused(registry):
    registry.begin(14)

    with pytest.raises(JobBusyError) as exc:
        registry.begin(14)
    assert "14" in str(exc.value)

    assert len(registry.active()) == 1


def test_a_book_can_start_again_once_its_job_ends(registry):
    first = registry.begin(14)
    registry.end(14)
    assert not first.is_running

    second = registry.begin(14)
    assert second is not first
    assert second.is_running


def test_cap_is_enforced(registry):
    registry.begin(1)
    registry.begin(2)
    registry.begin(3)

    with pytest.raises(JobCapacityError) as exc:
        registry.begin(4)
    assert "3" in str(exc.value)

    registry.end(2)
    assert registry.begin(4).book_id == 4


def test_capacity_counts_only_running_jobs(registry):
    for book_id in (1, 2, 3):
        registry.begin(book_id)
        registry.end(book_id)

    assert registry.has_capacity()
    assert registry.begin(4).is_running


def test_finished_job_stays_readable_until_pruned(registry):
    job = registry.begin(14)
    job.status = "complete"
    registry.end(14)

    # The status endpoint still needs the outcome after the slot is freed.
    assert registry.get(14) is job
    assert registry.get(14).status == "complete"


def test_prune_drops_only_finished_jobs(registry):
    live = registry.begin(99)
    for book_id in range(1, 12):
        registry.begin(book_id)
        registry.end(book_id)

    registry.prune(keep=2)

    assert registry.get(99) is live, "a running job is never pruned"
    remembered = [j for j in registry.all_jobs() if not j.is_running]
    assert len(remembered) == 2


def test_cap_is_read_live_from_settings(monkeypatch):
    import settings_store

    reg = JobRegistry(JobHub())  # None = consult settings each time
    monkeypatch.setattr(settings_store, "get", lambda k, d=None: 1)
    assert reg.max_concurrent() == 1

    reg.begin(1)
    with pytest.raises(JobCapacityError):
        reg.begin(2)

    # Raising the limit in Settings applies without a restart.
    monkeypatch.setattr(settings_store, "get", lambda k, d=None: 2)
    assert reg.begin(2).book_id == 2


def test_cap_never_drops_below_one(monkeypatch):
    import settings_store

    reg = JobRegistry(JobHub())
    monkeypatch.setattr(settings_store, "get", lambda k, d=None: 0)
    assert reg.max_concurrent() == 1


def test_rekey_moves_a_job_to_the_book_it_turned_out_to_be_for(registry):
    job = registry.begin(None)
    moved = registry.rekey(None, 14)

    assert moved is job
    assert job.book_id == 14
    assert registry.get(14) is job
    assert registry.get(None) is None


def test_rekey_refuses_to_collide_with_a_live_job(registry):
    registry.begin(14)
    registry.begin(None)

    with pytest.raises(JobBusyError):
        registry.rekey(None, 14)

    # The placeholder job is left where it was, not silently dropped.
    assert registry.get(None) is not None
