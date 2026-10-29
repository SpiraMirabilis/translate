"""NovelUpdates posting (nu_sync.py) against a fake scraper — no network, no
email, no ntfy. The fake asserts the lane's invariant: at most one NU job on
the scraper. Jobs finish on their first status poll, so a test drives the
engine with tick() until the lane is idle."""
import datetime

import pytest

import nu_sync
from scraper_client import ScraperError

BASE = "https://reader.test"
PAST = "2026-01-01T00:00:00"
FUTURE = "2999-01-01T00:00:00"


class FakeNu:
    def __init__(self):
        self.jobs = {}
        self.calls = []
        self._n = 0
        # path -> callable(body) -> result dict, or an Exception instance/str error
        self.handlers = {
            "/nu/search": lambda b: {"found": False, "candidates": [], "series": None,
                                     "inOurGroup": False},
            "/nu/releases": lambda b: {"status": "submitted", "message": "Thank you!"},
            "/nu/group/sync": lambda b: {"added": [], "series": []},
            "/nu/series": lambda b: {"status": "dry_run" if b.get("dryRun") else "submitted",
                                     "form": {"title": b.get("title")}, "screenshot": "s.png"},
            "/nu/associated-names": lambda b: {"status": "unchanged", "before": [], "after": []},
            "/nu/release-search": lambda b: {"title": b.get("title"), "match": None,
                                             "ambiguous": False, "hits": []},
            "/nu/vocabulary": lambda b: {"genres": [{"id": 1, "name": "Xianxia"}],
                                         "tags": [{"id": 2, "name": "Female Protagonist"}],
                                         "types": [], "languages": [{"id": 3, "name": "Chinese"}]},
        }
        self.errors = {}  # path -> list of error strings for the next jobs

    def _active(self):
        return [j for j in self.jobs.values() if j["status"] not in ("done", "failed", "cancelled")]

    def _req(self, method, path, body=None):
        assert method == "POST"
        assert not self._active(), "more than one NU job on the scraper"
        self._n += 1
        job = {"id": f"n{self._n}", "path": path, "body": body, "status": "running"}
        errs = self.errors.get(path)
        if errs:
            job["error"] = errs.pop(0)
        self.jobs[job["id"]] = job
        self.calls.append((path, body))
        return 202, {"id": job["id"], "status": "queued"}

    def status(self, job_id):
        job = self.jobs[job_id]
        if job["status"] == "running":
            job["status"] = "failed" if job.get("error") else "done"
        return {"id": job_id, "status": job["status"], "error": job.get("error"), "progress": {}}

    def result(self, job_id):
        job = self.jobs[job_id]
        return self.handlers[job["path"]](job["body"] or {})

    def cancel(self, job_id):
        self.jobs[job_id]["status"] = "cancelled"

    def posted(self, path="/nu/releases"):
        return [b for p, b in self.calls if p == path]


class Clock:
    def __init__(self):
        self.now = datetime.datetime(2026, 10, 29, 12, 0, 0)

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now += datetime.timedelta(**kw)


SETTINGS = dict(nu_sync.DEFAULTS, nu_enabled=True, nu_release_spacing_seconds=0)


@pytest.fixture(autouse=True)
def fixed(monkeypatch, tmp_path):
    monkeypatch.setattr(nu_sync, "setting", lambda k: SETTINGS[k])
    monkeypatch.setattr(nu_sync, "HOLD_FILE", str(tmp_path / "nu_hold.json"))
    monkeypatch.setattr(nu_sync, "VOCAB_FILE", str(tmp_path / "nu_vocab.json"))
    monkeypatch.setattr(nu_sync, "site_base", lambda: BASE)
    monkeypatch.setattr(nu_sync, "cover_url_for", lambda db, book: None)


@pytest.fixture
def nu():
    return FakeNu()


@pytest.fixture
def alerts():
    return []


@pytest.fixture
def engine(db, nu, alerts):
    return nu_sync.NuEngine(db, client_factory=lambda: nu, clock=Clock(), plan_seconds=0,
                            alerter=lambda subj, text, link: alerts.append((subj, text)))


def make_book(db, title="Necromancer", source_title="我，死灵法师，遵纪守法", chapters=(),
              public=True):
    book_id = db.create_book(title)
    db.update_book(book_id, source_title=source_title, is_public=public)
    for n, pub in chapters:
        db.save_chapter(book_id, n, f"Chapter {n}", ["源"], ["text"])
        db.set_chapter_published(book_id, n, pub)
    return book_id


def drain(engine, limit=200):
    for _ in range(limit):
        state = engine.tick()
        if state in ("held", "disabled"):
            return
        if state == "idle" and engine.db.nu_lane_task() is None:
            if engine.db.next_nu_task(engine._now()) is None:
                return
    raise AssertionError("NU lane never went idle")


def go_active(db, book_id, start, slug="the-law-abiding-necromancer"):
    db.update_book_nu(book_id, state="active", nu_slug=slug,
                      nu_url=f"https://www.novelupdates.com/series/{slug}/")
    nu_sync.set_mode(db, book_id, "auto", start_chapter=start)


# -- search ------------------------------------------------------------------

def test_search_sends_both_scripts(db, nu, engine):
    book = make_book(db)
    nu_sync.request_search(db, book)
    drain(engine)
    names = nu.posted("/nu/search")[0]["names"]
    assert names == ["我，死灵法师，遵纪守法", "我，死靈法師，遵紀守法"]


@pytest.mark.parametrize("res,state", [
    ({"found": True, "inOurGroup": True, "candidates": [],
      "series": {"slug": "s", "url": "https://www.novelupdates.com/series/s/", "title": "NU Name", "id": "9"}},
     "active"),
    ({"found": True, "inOurGroup": False, "candidates": [],
      "series": {"slug": "s", "url": "https://www.novelupdates.com/series/s/", "title": "NU Name", "id": "9"}},
     "linked"),
    ({"found": False, "inOurGroup": False, "series": None,
      "candidates": [{"slug": "x", "title": "Close", "exact": False}]}, "needs_review"),
    ({"found": False, "inOurGroup": False, "series": None, "candidates": []}, "new_series"),
])
def test_search_outcomes(db, nu, engine, res, state):
    book = make_book(db)
    nu.handlers["/nu/search"] = lambda b: res
    nu_sync.request_search(db, book)
    drain(engine)
    st = db.get_book_nu(book)
    assert st["state"] == state
    if state in ("active", "linked"):
        assert st["nu_slug"] == "s"
        assert db.get_book(book_id=book)["nu_title"] == "NU Name"
    if state == "needs_review":
        assert st["candidates"][0]["slug"] == "x"
    if state == "new_series":
        assert st["series_draft"]["associatedNames"][0] == "我，死灵法师，遵纪守法"


def test_no_source_title_needs_review(db, engine):
    book = make_book(db, source_title=None)
    st = nu_sync.request_search(db, book)
    assert st["state"] == "needs_review"
    assert "original title" in st["last_error"]


def test_find_all_searches_public_books_only(db, nu, engine):
    pub = make_book(db, title="A")
    make_book(db, title="B", public=False)
    out = nu_sync.find_all(db)
    assert out["searching"] == [pub]
    drain(engine)
    paths = [p for p, _ in nu.calls]
    assert paths[0] == "/nu/group/sync"           # sync first (priority)
    assert paths.count("/nu/search") == 1


# -- posting -----------------------------------------------------------------

def test_baseline_and_strict_order_with_gaps(db, nu, engine):
    book = make_book(db, chapters=[(n, PAST) for n in (1, 2, 3, 5, 6)])
    go_active(db, book, start=3)
    drain(engine)
    labels = [b["release"] for b in nu.posted()]
    assert labels == ["c3", "c5", "c6"]            # nothing below 3, c4 not invented
    links = [b["link"] for b in nu.posted()]
    assert links[0] == f"{BASE}/library/read/{book}/3"
    assert db.get_book_nu(book)["last_release_label"] == "c6"
    assert all(r["status"] == "sent" for r in db.list_nu_releases(book))


def test_scheduled_and_draft_chapters_wait(db, nu, engine):
    book = make_book(db, chapters=[(1, PAST), (2, FUTURE)])
    db.save_chapter(book, 3, "Draft", ["源"], ["t"], publish=False)
    go_active(db, book, start=1)
    drain(engine)
    assert [b["release"] for b in nu.posted()] == ["c1"]


def test_past_release_sends_date(db, nu, engine):
    book = make_book(db, chapters=[(1, "2026-10-01T09:00:00")])
    go_active(db, book, start=1)
    drain(engine)
    assert nu.posted()[0]["date"] == "10/01/2026"


def test_mode_off_posts_nothing(db, nu, engine):
    book = make_book(db, chapters=[(1, PAST)])
    db.update_book_nu(book, state="active", nu_slug="s", start_chapter=1)
    drain(engine)
    assert nu.posted() == []


def test_auto_needs_start_chapter(db):
    book = make_book(db)
    with pytest.raises(nu_sync.NuActionError):
        nu_sync.set_mode(db, book, "auto")


def test_private_book_held(db, nu, engine, alerts):
    book = make_book(db, chapters=[(1, PAST)], public=False)
    go_active(db, book, start=1)
    drain(engine)
    assert nu.posted() == []
    assert db.get_book_nu(book)["hold_reason"] == "not_public"
    assert len(alerts) == 1
    drain(engine)
    assert len(alerts) == 1                        # alerted once per hold


def test_release_spacing(db, nu, engine, monkeypatch):
    monkeypatch.setitem(SETTINGS, "nu_release_spacing_seconds", 120)
    book = make_book(db, chapters=[(1, PAST), (2, PAST)])
    go_active(db, book, start=1)
    drain(engine)
    assert [b["release"] for b in nu.posted()] == ["c1"]
    engine.clock.advance(seconds=121)
    drain(engine)
    assert [b["release"] for b in nu.posted()] == ["c1", "c2"]


# -- first release & approval -------------------------------------------------

def test_linked_first_release_then_sync_activates(db, nu, engine, alerts):
    book = make_book(db, chapters=[(1, PAST), (2, PAST)])
    db.update_book_nu(book, state="linked", nu_slug="s", nu_url="https://www.novelupdates.com/series/s/")
    nu_sync.set_mode(db, book, "auto", start_chapter=1)
    drain(engine)
    st = db.get_book_nu(book)
    assert st["state"] == "first_release_sent"
    assert [b["release"] for b in nu.posted()] == ["c1"]

    engine.clock.advance(hours=10)                 # before the approval wait: no sync
    drain(engine)
    assert "/nu/group/sync" not in [p for p, _ in nu.calls]

    nu.handlers["/nu/group/sync"] = lambda b: {"added": ["s"], "series": [
        {"slug": "s", "title": "NU Name", "url": "https://www.novelupdates.com/series/s/"}]}
    engine.clock.advance(hours=11)
    drain(engine)
    assert db.get_book_nu(book)["state"] == "active"
    assert [b["release"] for b in nu.posted()] == ["c1", "c2"]
    assert alerts == []


def test_approval_timeout_alerts_once(db, nu, engine, alerts):
    book = make_book(db, chapters=[(1, PAST)])
    db.update_book_nu(book, state="linked", nu_slug="s")
    nu_sync.set_mode(db, book, "auto", start_chapter=1)
    drain(engine)
    engine.clock.advance(hours=73)
    drain(engine)
    drain(engine)
    st = db.get_book_nu(book)
    assert st["hold_reason"] == "approval_timeout"
    assert len(alerts) == 1


def test_sync_learns_slug_of_new_series_by_title(db, nu, engine):
    book = make_book(db, title="Our Title")
    db.update_book(book, nu_title="The NU Title")
    db.update_book_nu(book, state="series_submitted")
    nu_sync.record_first_release(db, book, 1)
    nu.handlers["/nu/group/sync"] = lambda b: {"added": ["the-nu-title"], "series": [
        {"slug": "the-nu-title", "title": "The NU Title", "url": "https://www.novelupdates.com/series/the-nu-title/"}]}
    engine.clock.advance(hours=21)
    # record_first_release stamps real time; push the engine past the wait
    db.update_book_nu(book, first_release_at="2026-10-28T10:00:00")
    drain(engine)
    st = db.get_book_nu(book)
    assert st["state"] == "active" and st["nu_slug"] == "the-nu-title"
    assert db.get_nu_release(book, 1)["status"] == "manual"


# -- errors -------------------------------------------------------------------

def test_check_nu_holds_and_resolution(db, nu, engine, alerts):
    book = make_book(db, chapters=[(1, PAST), (2, PAST)])
    go_active(db, book, start=1)
    nu.errors["/nu/releases"] = ["an earlier attempt (job 7) may have gone through; check NU"]
    drain(engine)
    st = db.get_book_nu(book)
    assert st["hold_reason"] == "check_nu"
    assert db.get_nu_release(book, 1)["status"] == "check"
    assert len(nu.posted()) == 1 and len(alerts) == 1

    nu_sync.resolve_release(db, book, 1, "resend")
    drain(engine)
    bodies = nu.posted()
    assert bodies[1]["release"] == "c1" and bodies[1]["force"] is True
    assert bodies[2]["release"] == "c2"
    assert db.get_book_nu(book)["hold_reason"] is None


def test_check_nu_mark_sent(db, nu, engine):
    book = make_book(db, chapters=[(1, PAST), (2, PAST)])
    go_active(db, book, start=1)
    nu.errors["/nu/releases"] = ["neither confirmed nor refused it"]
    drain(engine)
    nu_sync.resolve_release(db, book, 1, "sent")
    drain(engine)
    assert [b["release"] for b in nu.posted()] == ["c1", "c2"]
    assert db.get_nu_release(book, 1)["status"] == "sent"


@pytest.mark.parametrize("error,hold", [
    ("NovelUpdates refused the release: duplicate", "refused"),
    ("no series at https://www.novelupdates.com/series/x/", "bad_slug"),
])
def test_hard_errors_hold(db, nu, engine, alerts, error, hold):
    book = make_book(db, chapters=[(1, PAST)])
    go_active(db, book, start=1)
    nu.errors["/nu/releases"] = [error]
    drain(engine)
    assert db.get_book_nu(book)["hold_reason"] == hold
    assert len(nu.posted()) == 1 and len(alerts) == 1


def test_transient_error_backs_off_then_holds(db, nu, engine, alerts):
    book = make_book(db, chapters=[(1, PAST)])
    go_active(db, book, start=1)
    nu.errors["/nu/releases"] = ["Cloudflare blocked"] * 4
    drain(engine)
    assert len(nu.posted()) == 1
    for minutes in nu_sync.RETRY_BACKOFF_MINUTES:
        engine.clock.advance(minutes=minutes + 1)
        drain(engine)
    assert len(nu.posted()) == 4
    assert db.get_book_nu(book)["hold_reason"] == "failing"
    assert len(alerts) == 1


def test_login_failure_holds_whole_lane(db, nu, engine, alerts):
    book = make_book(db, chapters=[(1, PAST), (2, PAST)])
    go_active(db, book, start=1)
    nu.errors["/nu/releases"] = ["NovelUpdates login failed: bad password"]
    drain(engine)
    assert nu_sync.load_hold()
    assert engine.tick() == "held"
    assert db.get_nu_release(book, 1)["status"] == "queued"
    nu_sync.clear_hold()
    drain(engine)
    assert [b["release"] for b in nu.posted()] == ["c1", "c1", "c2"]


def test_restart_resumes_polling(db, nu, engine):
    book = make_book(db, chapters=[(1, PAST)])
    go_active(db, book, start=1)
    engine.tick()                                  # plan + submit
    assert db.nu_lane_task()["status"] == "running"
    fresh = nu_sync.NuEngine(db, client_factory=lambda: nu, clock=engine.clock, plan_seconds=0,
                             alerter=lambda *a: None)
    drain(fresh)
    assert len(nu.posted()) == 1


def test_disabled_does_nothing(db, nu, engine, monkeypatch):
    monkeypatch.setitem(SETTINGS, "nu_enabled", False)
    book = make_book(db, chapters=[(1, PAST)])
    go_active(db, book, start=1)
    assert engine.tick() == "disabled"
    assert nu.calls == []


def test_unpublished_before_submit_is_skipped(db, nu, engine):
    book = make_book(db, chapters=[(1, PAST)])
    go_active(db, book, start=1)
    engine.plan()
    db.set_chapter_published(book, 1, None)
    drain(engine)
    assert nu.posted() == []


# -- new series ----------------------------------------------------------------

def test_series_requires_dry_run_of_same_draft(db, nu, engine):
    book = make_book(db)
    db.update_book(book, tags=["xianxia", "female protagonist", "made up"])
    nu.handlers["/nu/vocabulary"]  # vocab fetched by planner for a new series
    nu_sync.declare_new_series(db, book)
    drain(engine)                                  # fetches the vocabulary
    st = db.get_book_nu(book)
    draft = nu_sync.build_series_draft(db.get_book(book_id=book), nu_sync.load_vocab())
    assert draft["genres"] == ["Xianxia"] and draft["tags"] == ["Female Protagonist"]
    assert draft["unmatchedTags"] == ["made up"]
    nu_sync.save_draft(db, book, draft)

    with pytest.raises(nu_sync.NuActionError):
        nu_sync.queue_series(db, book, dry_run=False)
    nu_sync.queue_series(db, book, dry_run=True)
    drain(engine)
    assert nu.posted("/nu/series")[-1]["dryRun"] is True
    nu_sync.queue_series(db, book, dry_run=False)
    drain(engine)
    assert "dryRun" not in nu.posted("/nu/series")[-1]
    assert db.get_book_nu(book)["state"] == "series_submitted"

    # a changed draft needs a fresh dry run
    db.update_book_nu(book, state="new_series")
    nu_sync.save_draft(db, book, dict(draft, title="Changed"))
    with pytest.raises(nu_sync.NuActionError):
        nu_sync.queue_series(db, book, dry_run=False)
    assert st["state"] == "new_series"


def test_link_series_by_hand_then_sync_promotes(db, nu, engine):
    book = make_book(db, chapters=[(1, PAST)])
    nu.handlers["/nu/group/sync"] = lambda b: {"added": [], "series": [
        {"slug": "picked", "title": "Picked", "url": "https://www.novelupdates.com/series/picked/"}]}
    nu_sync.link_series(db, book, "https://www.novelupdates.com/series/picked/", title="Picked")
    drain(engine)
    st = db.get_book_nu(book)
    assert st["state"] == "active" and st["nu_slug"] == "picked"
    assert nu.posted() == []                        # mode still off


def test_renumber_moves_release_rows(db, nu, engine):
    book = make_book(db, chapters=[(1, PAST)])
    go_active(db, book, start=1)
    drain(engine)
    ok, _ = db.renumber_chapter(book, 1, 7)
    assert ok
    assert db.get_nu_release(book, 7)["status"] == "sent"


# -- pacing --------------------------------------------------------------------

def test_next_release_follows_in_the_same_tick(db, nu, engine):
    book = make_book(db, chapters=[(n, PAST) for n in (1, 2, 3)])
    go_active(db, book, start=1)
    engine.plan_seconds = 3600          # only the end of a job may trigger a re-plan
    engine.tick()                       # plan + submit c1
    for expected in (["c1", "c2"], ["c1", "c2", "c3"]):
        engine.tick()                   # c(n) finishes; c(n+1) is submitted at once
        assert [b["release"] for b in nu.posted()] == expected


def test_seconds_until_due_honours_spacing(db, nu, engine, monkeypatch):
    monkeypatch.setitem(SETTINGS, "nu_release_spacing_seconds", 2)
    book = make_book(db, chapters=[(1, PAST), (2, PAST)])
    go_active(db, book, start=1)
    assert engine.seconds_until_due() is None
    engine.tick()                       # submit c1
    engine.tick()                       # c1 done; c2 queued 2 s out
    assert [b["release"] for b in nu.posted()] == ["c1"]
    assert 0 < engine.seconds_until_due() <= 2
    engine.clock.advance(seconds=2)
    assert engine.seconds_until_due() == 0
    drain(engine)
    assert [b["release"] for b in nu.posted()] == ["c1", "c2"]


# -- a submitted series' first chapter, by id ------------------------------------

def _submitted(db, nu, engine, title="Tokyo 1996: Manga by Keyword", chapters=((1, PAST), (2, PAST))):
    book = make_book(db, title="Manga", chapters=list(chapters))
    db.update_book_nu(book, state="new_series", series_draft={
        "title": title, "associatedNames": ["原名"], "genres": ["Drama"]})
    nu_sync.queue_series(db, book, dry_run=True)
    drain(engine)
    nu_sync.queue_series(db, book, dry_run=False)
    return book


def test_submitted_series_is_found_and_its_first_chapter_posted_by_id(db, nu, engine, alerts):
    nu.handlers["/nu/release-search"] = lambda b: {"title": b["title"], "ambiguous": False,
                                                   "match": {"id": "990001", "title": b["title"]},
                                                   "hits": [{"id": "990001", "title": b["title"]}]}
    book = _submitted(db, nu, engine)
    nu_sync.set_mode(db, book, "auto", start_chapter=1)
    drain(engine)
    st = db.get_book_nu(book)
    assert st["state"] == "first_release_sent" and st["nu_series_id"] == "990001"
    first = nu.posted()[0]
    assert first["seriesId"] == "990001" and first["seriesTitle"] == "Tokyo 1996: Manga by Keyword"
    assert "series" not in first and first["release"] == "c1"
    assert db.get_book(book_id=book)["nu_title"] == "Tokyo 1996: Manga by Keyword"

    # approved: the group sync finds it by title; c2 goes by slug
    nu.handlers["/nu/group/sync"] = lambda b: {"added": ["tokyo-1996"], "series": [
        {"slug": "tokyo-1996", "title": "Tokyo 1996: Manga by Keyword",
         "url": "https://www.novelupdates.com/series/tokyo-1996/"}]}
    engine.clock.advance(hours=21)
    drain(engine)
    assert db.get_book_nu(book)["state"] == "active"
    assert nu.posted()[1]["series"].endswith("/series/tokyo-1996/") and nu.posted()[1]["release"] == "c2"
    assert alerts == []


def test_submitted_series_mode_off_waits_for_the_button(db, nu, engine):
    nu.handlers["/nu/release-search"] = lambda b: {"title": b["title"], "ambiguous": False,
                                                   "match": {"id": "7", "title": b["title"]}, "hits": []}
    book = _submitted(db, nu, engine)
    drain(engine)
    assert nu.posted() == []                        # mode off: nothing posts by itself
    nu_sync.post_first_release(db, book)            # the "Post c1 now" button
    drain(engine)
    assert [b["release"] for b in nu.posted()] == ["c1"]
    assert db.get_book_nu(book)["state"] == "first_release_sent"


def test_ambiguous_lookup_lists_hits_for_a_human(db, nu, engine, alerts):
    hits = [{"id": "1", "title": "Same Name"}, {"id": "2", "title": "Same Name"}]
    nu.handlers["/nu/release-search"] = lambda b: {"title": b["title"], "ambiguous": True,
                                                   "match": None, "hits": hits}
    book = _submitted(db, nu, engine, title="Same Name")
    drain(engine)
    st = db.get_book_nu(book)
    assert st["nu_series_id"] is None and [c["id"] for c in st["candidates"]] == ["1", "2"]
    assert len(alerts) == 1
    nu_sync.set_pending_series_id(db, book, "2")
    assert db.get_book_nu(book)["nu_series_id"] == "2"


def test_lookup_retries_then_alerts(db, nu, engine, alerts):
    book = _submitted(db, nu, engine)
    drain(engine)
    for _ in range(nu_sync.RELEASE_SEARCH_TRIES):
        engine.clock.advance(minutes=nu_sync.RELEASE_SEARCH_RETRY_MINUTES + 1)
        drain(engine)
    assert len(nu.posted("/nu/release-search")) == nu_sync.RELEASE_SEARCH_TRIES
    assert len(alerts) == 1 and "doesn't list" in db.get_book_nu(book)["last_error"]


def test_wrong_pending_id_holds(db, nu, engine, alerts):
    nu.handlers["/nu/release-search"] = lambda b: {"title": b["title"], "ambiguous": False,
                                                   "match": {"id": "5", "title": b["title"]}, "hits": []}
    book = _submitted(db, nu, engine)
    nu_sync.set_mode(db, book, "auto", start_chapter=1)
    nu.errors["/nu/releases"] = ["NU's Series search for \"Tokyo\" didn't offer id 5"]
    drain(engine)
    st = db.get_book_nu(book)
    assert st["hold_reason"] == "bad_slug" and st["state"] == "series_submitted"
    assert len(alerts) == 1


# -- backlog rate limit ------------------------------------------------------------

def test_backlog_is_rate_limited_and_new_chapters_are_not(db, nu, engine, monkeypatch):
    # c1-c6 published; the book feed carries the newest 2 (c5, c6)
    monkeypatch.setitem(SETTINGS, "nu_feed_window_book", 2)
    monkeypatch.setitem(SETTINGS, "nu_backlog_limit", 2)
    monkeypatch.setitem(SETTINGS, "nu_backlog_window_hours", 8)
    book = make_book(db, chapters=[(n, PAST) for n in range(1, 7)])
    go_active(db, book, start=1)
    drain(engine)
    assert [b["release"] for b in nu.posted()] == ["c1", "c2"]      # then the limit binds
    st = db.get_book_nu(book)
    assert st["rate_limited_until"]
    assert [r["backlog"] for r in sorted(db.list_nu_releases(book), key=lambda r: r["chapter_number"])] == [1, 1]

    engine.clock.advance(hours=8, seconds=1)
    drain(engine)
    # c3, c4 are backlog (2 more); c5, c6 are in the feed and go regardless
    assert [b["release"] for b in nu.posted()] == ["c1", "c2", "c3", "c4", "c5", "c6"]
    rows = {r["chapter_number"]: r["backlog"] for r in db.list_nu_releases(book)}
    assert rows[5] == 0 and rows[6] == 0
    assert db.get_book_nu(book)["rate_limited_until"] is None


def test_global_backlog_cap(db, nu, engine, monkeypatch):
    monkeypatch.setitem(SETTINGS, "nu_feed_window_book", 1)
    monkeypatch.setitem(SETTINGS, "nu_backlog_limit", 0)          # no per-book limit
    monkeypatch.setitem(SETTINGS, "nu_backlog_limit_all", 3)
    a = make_book(db, title="A", chapters=[(n, PAST) for n in range(1, 4)])
    b = make_book(db, title="B", chapters=[(n, PAST) for n in range(1, 4)])
    go_active(db, a, start=1, slug="a")
    go_active(db, b, start=1, slug="b")
    drain(engine)
    backlog = [r for bid in (a, b) for r in db.list_nu_releases(bid) if r["backlog"]]
    assert len(backlog) == 3                                        # the cap across books


def test_limit_off_posts_everything(db, nu, engine, monkeypatch):
    monkeypatch.setitem(SETTINGS, "nu_feed_window_book", 1)
    monkeypatch.setitem(SETTINGS, "nu_backlog_limit", 0)
    book = make_book(db, chapters=[(n, PAST) for n in range(1, 5)])
    go_active(db, book, start=1)
    drain(engine)
    assert len(nu.posted()) == 4


# -- associated names ------------------------------------------------------------

def test_linked_series_gets_our_names_before_the_first_release(db, nu, engine):
    nu.handlers["/nu/associated-names"] = lambda b: {"status": "updated", "before": ["X"],
                                                     "after": ["X"] + b["add"]}
    book = make_book(db, title="Our English Title", chapters=[(1, PAST)])
    db.update_book_nu(book, state="linked", nu_slug="s", nu_url="https://www.novelupdates.com/series/s/")
    nu_sync.set_mode(db, book, "auto", start_chapter=1)
    drain(engine)
    paths = [p for p, _ in nu.calls]
    assert paths.index("/nu/associated-names") < paths.index("/nu/releases")
    add = nu.posted("/nu/associated-names")[0]["add"]
    assert add == ["Our English Title", "我，死灵法师，遵纪守法", "我，死靈法師，遵紀守法"]
    assert db.get_book_nu(book)["state"] == "first_release_sent"


def test_names_failure_never_blocks_the_release(db, nu, engine, alerts):
    nu.errors["/nu/associated-names"] = ["the names didn't save as sent: missing Our Title"]
    book = make_book(db, chapters=[(1, PAST)])
    db.update_book_nu(book, state="linked", nu_slug="s")
    nu_sync.set_mode(db, book, "auto", start_chapter=1)
    drain(engine)
    assert [b["release"] for b in nu.posted()] == ["c1"]
    assert len(alerts) == 1
    assert len(nu.posted("/nu/associated-names")) == 1      # tried once, not again


def test_active_series_get_names_only_on_request(db, nu, engine):
    book = make_book(db, chapters=[(1, PAST)])
    go_active(db, book, start=1)
    drain(engine)
    assert nu.posted("/nu/associated-names") == []
    nu_sync.queue_names(db, book)
    drain(engine)
    assert len(nu.posted("/nu/associated-names")) == 1
