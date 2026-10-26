"""Automated scraping (scrape_sync.py) against a fake scraper — no network.

The fake scraper enforces the invariant the whole design rests on: at most one
job in flight. Jobs finish on their first status poll, so a test drives the
engine with tick() until the lane is idle.
"""
import datetime

import pytest

import scrape_sync
from scraper_client import ScraperError

BOOK_URL = "https://twkan.com/book/1.html"


def entry(n, title=None):
    return {"title": title or f"第{n}章 标题{n}", "url": f"https://twkan.com/txt/1/{n}.html",
            "text": f"正文{n}\n第二行"}


class FakeScraper:
    def __init__(self, entries):
        self.entries = entries
        self.jobs = {}
        self.calls = []
        self.fail_next = 0
        self._n = 0
        self.sites = ["twkan.com"]
        self.sites_calls = 0

    def supported_sites(self):
        # Not a job: the real endpoint answers directly, outside the lane.
        self.sites_calls += 1
        if self.sites is None:
            raise OSError("unreachable")
        return list(self.sites)

    def _active(self):
        return [j for j in self.jobs.values() if j["status"] not in ("done", "failed", "cancelled")]

    def _new(self, kind, **params):
        assert not self._active(), "more than one job on the scraper"
        self._n += 1
        job = {"id": f"j{self._n}", "kind": kind, "status": "running", "params": params}
        self.jobs[job["id"]] = job
        self.calls.append((kind, params))
        return {"id": job["id"], "status": "queued"}

    def submit_toc(self, url, engine=None):
        return self._new("toc", url=url, engine=engine)

    def submit_chapters(self, url, from_url=None, max_chapters=None, priority=None, engine=None, **kw):
        return self._new("chapters", url=url, from_url=from_url, max_chapters=max_chapters,
                         priority=priority, engine=engine)

    def retry(self, job_id):
        job = self.jobs[job_id]
        assert not self._active()
        job["status"] = "running"
        self.calls.append(("retry", {"job_id": job_id}))
        return {"id": job_id, "status": "queued"}

    def cancel(self, job_id):
        self.jobs[job_id]["status"] = "cancelled"

    def status(self, job_id):
        job = self.jobs[job_id]
        if job["status"] == "running":
            if self.fail_next:
                self.fail_next -= 1
                job["status"] = "failed"
                job["error"] = "challenge timed out"
            else:
                job["status"] = "done"
        return {"id": job_id, "status": job["status"], "error": job.get("error"), "progress": {},
                "stopReason": job.get("stopReason")}

    def result(self, job_id):
        job = self.jobs[job_id]
        if job["kind"] == "toc":
            chs = [{"position": i + 1, "number": i + 1, "title": e["title"], "url": e["url"]}
                   for i, e in enumerate(self.entries)]
            return {"chapterCount": len(chs), "chapters": chs}
        p = job["params"]
        start = next(i for i, e in enumerate(self.entries) if e["url"] == p["from_url"])
        picked = self.entries[start:start + (p["max_chapters"] or len(self.entries))]
        hit_max = p["max_chapters"] and len(picked) == p["max_chapters"] \
            and start + len(picked) < len(self.entries)
        job["stopReason"] = "maxChapters" if hit_max else "end"
        return {"site": "twkan", "job": {"stopReason": job["stopReason"]},
                "chapters": [{"number": i + 1, "title": e["title"], "text": e["text"],
                              "sourceUrls": [e["url"]]} for i, e in enumerate(picked)]}

    def status_with_stop(self, job_id):
        return self.status(job_id)


class FakeAdmin:
    def __init__(self, code=200):
        self.code = code
        self.running = set()
        self.started = []

    def running_book_ids(self):
        return set(self.running)

    def process_next(self, payload):
        self.started.append(payload)
        return self.code, {"detail": "busy"} if self.code != 200 else {"status": "started"}


class Clock:
    def __init__(self):
        self.now = datetime.datetime(2026, 10, 26, 6, 0, 0)

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now += datetime.timedelta(**kw)


SETTINGS = {
    "scrape_enabled": True, "scrape_daily_hour": 5, "scrape_chunk_size": 2,
    "scrape_default_gap": 10, "scrape_toc_spacing_seconds": 0, "scraper_url": "http://x",
    "scrape_engine": "",
}


@pytest.fixture(autouse=True)
def fixed_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(scrape_sync, "setting", lambda k: SETTINGS[k])
    monkeypatch.setattr(scrape_sync, "SITES_CACHE", str(tmp_path / "scraper_sites.json"))


@pytest.fixture
def book(db):
    book_id = db.create_book("Test Book")
    db.update_book(book_id, source_url=BOOK_URL)
    return book_id


def make_engine(db, scraper, admin=None, clock=None):
    # Chapters jobs report stopReason via status(); the engine reads it from the
    # status payload, so result() must run first in the fake — fold it in.
    orig_status = scraper.status

    def status(job_id):
        job = scraper.jobs[job_id]
        out = orig_status(job_id)
        if out["status"] == "done" and job["kind"] == "chapters" and not job.get("stopReason"):
            scraper.result(job_id)
            out["stopReason"] = job["stopReason"]
        return out
    scraper.status = status
    return scrape_sync.ScrapeEngine(db, client_factory=lambda: scraper, admin=admin or FakeAdmin(),
                                    clock=clock or Clock())


def drain(engine, limit=100):
    for _ in range(limit):
        if engine.tick() == "idle" and engine.db.lane_task() is None \
                and engine.db.next_scrape_task(engine._now()) is None:
            return
    raise AssertionError("lane never went idle")


def queued_numbers(db, book_id):
    return sorted(q["chapter_number"] for q in db.list_queue(book_id=book_id, include_processing=True))


def seed_queue(db, book_id, numbers):
    for n in numbers:
        db.add_to_queue(book_id=book_id, content=[f"正文{n}"], title=f"第{n}章 标题{n}", chapter_number=n)


def check_and_link(db, engine, book_id):
    scrape_sync.enqueue_check(db, book_id)
    drain(engine)
    state = db.get_book_scrape(book_id, include_toc=True)
    have = max(db.existing_chapter_numbers(book_id) or {0})
    prop = scrape_sync.propose_link(state["last_toc"]["chapters"], have,
                                    our_titles=db.recent_source_titles(book_id))
    assert prop is not None
    if not state.get("linked"):
        scrape_sync.link_book(db, book_id, prop["index"], prop["chapter_number"])


# ---------------------------------------------------------------------------

def test_parsers():
    assert scrape_sync.cn_to_num("一千零二十三") == 1023
    assert scrape_sync.cn_to_num("十五") == 15
    assert scrape_sync.cn_to_num("１２") == 12
    assert scrape_sync.parse_title_number("第三百二十一章 风起") == 321
    assert scrape_sync.parse_title_number("请假条") is None
    assert scrape_sync.url_key("http://www.twkan.com/a/1.html/") == scrape_sync.url_key("https://twkan.com/a/1.html")
    assert scrape_sync.normalize_book_url("69shuba.com/txt/1") == "https://69shuba.com/txt/1"


def test_daily_sweep_catches_up_in_chunks(db, book):
    scraper = FakeScraper([entry(n) for n in range(1, 8)])
    clock = Clock()
    engine = make_engine(db, scraper, clock=clock)
    seed_queue(db, book, [1, 2, 3])
    check_and_link(db, engine, book)
    db.update_book_scrape(book, mode="queue")

    engine.tick()  # sweep fires (06:00 > 05:00)
    drain(engine)
    assert queued_numbers(db, book) == [1, 2, 3, 4, 5, 6, 7]
    state = db.get_book_scrape(book)
    assert state["cursor_url"].endswith("/7.html") and state["cursor_number"] == 7
    assert state["pending_count"] == 0
    chapter_jobs = [p for k, p in scraper.calls if k == "chapters"]
    assert [p["max_chapters"] for p in chapter_jobs] == [2, 2]  # chunked: 4-5, 6-7
    assert {p["priority"] for p in chapter_jobs} == {None}  # catch-ups run at normal priority

    # Same day: no second sweep.
    n = len(scraper.calls)
    engine.tick()
    drain(engine)
    assert len(scraper.calls) == n


def test_backfill_yields_to_toc_check_between_chunks(db, book):
    scraper = FakeScraper([entry(n) for n in range(1, 7)])
    engine = make_engine(db, scraper)
    scrape_sync.enqueue_check(db, book)
    drain(engine)
    scrape_sync.link_book(db, book, -1)
    scrape_sync.enqueue_backfill(db, book, 0)
    engine.step_lane()   # submit chunk 1
    scrape_sync.enqueue_check(db, book)  # arrives mid-backfill
    engine.step_lane()   # finish chunk 1 -> queues chunk 2
    engine.step_lane()   # next pick must be the TOC check
    assert scraper.calls[-1][0] == "toc"
    drain(engine)
    assert queued_numbers(db, book) == [1, 2, 3, 4, 5, 6]
    assert {p["priority"] for k, p in scraper.calls if k == "chapters"} == {"low"}


def test_numbering_anomalies_hold_the_book(db, book):
    entries = [entry(1), entry(2), entry(0, "请假条"), entry(4), entry(4, "第四章 重复")]
    entries[2]["url"] = "https://twkan.com/txt/1/notice.html"
    entries[4]["url"] = "https://twkan.com/txt/1/4b.html"
    scraper = FakeScraper(entries)
    engine = make_engine(db, scraper)
    seed_queue(db, book, [1])
    check_and_link(db, engine, book)
    db.update_book_scrape(book, mode="auto")
    scrape_sync.enqueue_check(db, book, catchup=True)
    drain(engine)
    assert queued_numbers(db, book) == [1, 2, 4]  # notice skipped, duplicate 4 skipped
    state = db.get_book_scrape(book)
    assert "gap: ch2 -> ch4" in state["hold_reason"]
    assert "ch4" in state["hold_reason"]
    assert engine.admin.started == []  # held: no automatic translation


def test_vanished_cursor_stops_the_book(db, book):
    scraper = FakeScraper([entry(n) for n in range(1, 4)])
    engine = make_engine(db, scraper)
    seed_queue(db, book, [1, 2])
    check_and_link(db, engine, book)
    db.update_book_scrape(book, mode="queue")
    scraper.entries = [entry(1), entry(3), entry(4)]  # ch2's page was removed
    scrape_sync.enqueue_check(db, book, catchup=True)
    drain(engine)
    assert queued_numbers(db, book) == [1, 2]
    assert "no longer in the source TOC" in db.get_book_scrape(book)["hold_reason"]


def test_failed_job_retries_with_backoff_and_resumes(db, book):
    scraper = FakeScraper([entry(n) for n in range(1, 4)])
    clock = Clock()
    clock.now = clock.now.replace(hour=3)  # before the daily sweep
    engine = make_engine(db, scraper, clock=clock)
    check_and_link(db, engine, book)  # empty book: linked before ch1
    db.update_book_scrape(book, mode="queue")
    scraper.fail_next = 1
    scrape_sync.queue_chapter_chunk(db, book, "catchup",
                                    db.get_book_scrape(book, include_toc=True)["last_toc"]["chapters"],
                                    base_number=None)
    drain(engine)  # fails, requeued for later -> lane idle (not due)
    task = [t for t in db.list_scrape_tasks(book_id=book) if t["kind"] == "chapters"][0]
    assert task["status"] == "queued" and task["attempts"] == 1 and task["scraper_job_id"]
    assert queued_numbers(db, book) == [1, 2]  # the failed job's partial result
    clock.advance(minutes=31)
    drain(engine)
    kinds = [k for k, _ in scraper.calls]
    assert "retry" in kinds and kinds.count("chapters") == 2  # resumed, then chunk 2
    assert queued_numbers(db, book) == [1, 2, 3]


def test_unsupported_site_fails_without_retry(db, book):
    class Rejecting(FakeScraper):
        def submit_toc(self, url, engine=None):
            raise ScraperError("POST /toc: HTTP 400: unsupported site")
    engine = make_engine(db, Rejecting([]))
    scrape_sync.enqueue_check(db, book)
    drain(engine)
    task = db.list_scrape_tasks(book_id=book, limit=1)[0]
    assert task["status"] == "failed"
    assert db.get_book_scrape(book)["consecutive_failures"] == 1


def test_restart_resumes_polling_instead_of_resubmitting(db, book):
    scraper = FakeScraper([entry(1)])
    engine = make_engine(db, scraper)
    scrape_sync.enqueue_check(db, book)
    engine.step_lane()  # submitted, job id persisted
    assert len(scraper.calls) == 1
    engine2 = scrape_sync.ScrapeEngine(db, client_factory=lambda: scraper, admin=FakeAdmin(),
                                       clock=Clock())
    engine2.step_lane()
    assert len(scraper.calls) == 1  # polled, not resubmitted
    assert db.get_book_scrape(book)["toc_count"] == 1


def test_autostart_respects_gap_and_uses_last_model(db, book):
    scraper = FakeScraper([entry(n) for n in range(1, 5)])
    admin = FakeAdmin()
    engine = make_engine(db, scraper, admin=admin)
    db.save_chapter(book, 1, "第1章", ["a"], ["A"], translation_model="claude:test-model")
    seed_queue(db, book, [2])
    check_and_link(db, engine, book)
    db.update_book_scrape(book, mode="auto")
    scrape_sync.enqueue_check(db, book, catchup=True)
    drain(engine)
    engine.tick()  # runs the autostart task
    assert len(admin.started) == 1
    payload = admin.started[0]
    assert payload["book_id"] == book and payload["no_review"] is True
    assert payload["auto_process"] is True
    assert payload["translation_model"] == "claude:test-model"


def test_autostart_skips_when_gap_too_large(db, book):
    scraper = FakeScraper([entry(n) for n in range(1, 5)])
    admin = FakeAdmin()
    engine = make_engine(db, scraper, admin=admin)
    db.save_chapter(book, 1, "第1章", ["a"], ["A"])
    seed_queue(db, book, [2])
    check_and_link(db, engine, book)
    db.update_book_scrape(book, mode="auto", auto_gap=1)
    scrape_sync.enqueue_check(db, book, catchup=True)
    drain(engine)
    engine.tick()
    assert admin.started == []
    task = [t for t in db.list_scrape_tasks(book_id=book) if t["kind"] == "autostart"][0]
    assert task["status"] == "skipped" and "gap too large" in task["result_summary"]["reason"]


def test_autostart_retries_while_job_cap_is_full(db, book):
    scraper = FakeScraper([entry(n) for n in range(1, 3)])
    admin = FakeAdmin(code=409)
    clock = Clock()
    engine = make_engine(db, scraper, admin=admin, clock=clock)
    check_and_link(db, engine, book)
    db.update_book_scrape(book, mode="auto")
    scrape_sync.enqueue_check(db, book, catchup=True)
    drain(engine)
    engine.tick()
    assert len(admin.started) == 1
    engine.tick()
    assert len(admin.started) == 1  # not due yet
    admin.code = 200
    clock.advance(minutes=16)
    engine.tick()
    assert len(admin.started) == 2
    task = [t for t in db.list_scrape_tasks(book_id=book) if t["kind"] == "autostart"][0]
    assert task["status"] == "done"


def test_existing_numbers_are_not_requeued(db, book):
    scraper = FakeScraper([entry(n) for n in range(1, 4)])
    engine = make_engine(db, scraper)
    seed_queue(db, book, [2])
    scrape_sync.enqueue_check(db, book)
    drain(engine)
    scrape_sync.link_book(db, book, -1)
    scrape_sync.enqueue_backfill(db, book, 0)
    drain(engine)
    assert queued_numbers(db, book) == [1, 2, 3]


def test_forced_check_behaves_like_the_daily_one(db, book):
    scraper = FakeScraper([entry(n) for n in range(1, 4)])
    clock = Clock()
    clock.now = clock.now.replace(hour=3)  # before the daily sweep
    engine = make_engine(db, scraper, clock=clock)
    seed_queue(db, book, [1])
    check_and_link(db, engine, book)
    scrape_sync.enqueue_check(db, book, reason="forced")
    drain(engine)
    assert queued_numbers(db, book) == [1]  # mode off: TOC only
    db.update_book_scrape(book, mode="queue")
    scrape_sync.enqueue_check(db, book, reason="forced")
    drain(engine)
    assert queued_numbers(db, book) == [1, 2, 3]


def test_title_match_links_across_scripts_and_offsets():
    # The book 130 case: our numbering runs 3 ahead of the source's 第N章, the
    # TOC is traditional and our stored source simplified.
    toc = [{"title": f"{i:03d}：前言{i}", "url": f"u{i}"} for i in range(1, 4)] + \
          [{"title": f"第{n}章 {n}：跨頁式作畫{n}", "url": f"v{n}"} for n in range(1, 103)]
    ours = {104: "101：跨页式作画101", 103: "100：跨页式作画100"}
    prop = scrape_sync.propose_link(toc, 104, our_titles=ours)
    assert prop["method"] == "title" and prop["confident"]
    assert prop["index"] == 103 and prop["number_offset"] == 3   # 第101章 + 3 = ch104


def test_wrong_offset_holds_instead_of_silently_skipping(db, book):
    scraper = FakeScraper([entry(n) for n in range(1, 5)])
    engine = make_engine(db, scraper)
    seed_queue(db, book, [1, 2, 3, 4])
    scrape_sync.enqueue_check(db, book)
    drain(engine)
    db.update_book_scrape(book, linked=1, cursor_url=entry(3)["url"], cursor_number=2,
                          number_offset=-1)  # linked one too low: 第4章 -> ch3, which exists
    scrape_sync.enqueue_check(db, book, catchup=True)
    drain(engine)
    assert "numbering is probably off" in db.get_book_scrape(book)["hold_reason"]


def test_unlinked_book_auto_links_by_title_on_first_toc(db, book):
    scraper = FakeScraper([entry(n) for n in range(1, 6)])
    engine = make_engine(db, scraper)
    seed_queue(db, book, [1, 2, 3])
    db.update_book_scrape(book, mode="queue")
    scrape_sync.enqueue_check(db, book, reason="forced")
    drain(engine)
    toc_task = [t for t in db.list_scrape_tasks(book_id=book) if t["reason"] == "forced"][0]
    assert toc_task["result_summary"]["auto_linked"] == 2   # #3 = our ch3
    assert queued_numbers(db, book) == [1, 2, 3, 4, 5]
    assert db.get_book_scrape(book)["cursor_number"] == 5


# -- /supported_sites ----------------------------------------------------------

def _book_on(db, url, status=None):
    book_id = db.create_book(f"Book at {url}")
    db.update_book(book_id, source_url=url, **({"status": status} if status else {}))
    return book_id


def test_first_sites_read_caches_and_onboards_nothing(db, book):
    scraper = FakeScraper([entry(1)])
    engine = make_engine(db, scraper)
    assert engine.refresh_sites() == ["twkan.com"]
    assert scrape_sync.load_supported_sites()["sites"] == ["twkan.com"]
    assert db.list_scrape_tasks(book_id=book) == []
    engine.refresh_sites()
    assert scraper.sites_calls == 1  # cached for a day


def test_new_site_onboards_its_unlinked_books(db, book):
    scraper = FakeScraper([entry(1)])
    clock = Clock()
    engine = make_engine(db, scraper, clock=clock)
    engine.refresh_sites()
    new_book = _book_on(db, "https://www.69shuba.com/book/5.htm")
    finished = _book_on(db, "https://69shuba.com/book/6.htm", status="completed")
    other = _book_on(db, "https://ixdzs8.com/read/7/")
    scraper.sites = ["69shuba.com", "twkan.com"]
    clock.advance(hours=25)
    engine.refresh_sites()
    tasks = db.list_scrape_tasks(book_id=new_book)
    assert [(t["kind"], t["reason"], t["priority"]) for t in tasks] == \
        [("toc", "onboard", scrape_sync.PRIORITY_ONBOARD)]
    assert db.list_scrape_tasks(book_id=finished) == []
    assert db.list_scrape_tasks(book_id=other) == []
    assert db.list_scrape_tasks(book_id=book) == []  # twkan was already listed


def test_sweep_skips_books_on_unsupported_sites(db, book):
    scraper = FakeScraper([entry(1)])
    engine = make_engine(db, scraper)
    unsupported = _book_on(db, "https://ixdzs8.com/read/7/")
    db.update_book_scrape(book, mode="queue")
    db.update_book_scrape(unsupported, mode="queue")
    engine.refresh_sites()
    engine.schedule_sweep()
    assert [t["kind"] for t in db.list_scrape_tasks(book_id=book)] == ["toc"]
    assert db.list_scrape_tasks(book_id=unsupported) == []


def test_unreachable_sites_endpoint_keeps_the_last_list(db, book):
    scraper = FakeScraper([entry(1)])
    clock = Clock()
    engine = make_engine(db, scraper, clock=clock)
    engine.refresh_sites()
    scraper.sites = None
    clock.advance(hours=25)
    assert engine.refresh_sites() == ["twkan.com"]
    calls = scraper.sites_calls
    clock.advance(minutes=10)
    engine.refresh_sites()
    assert scraper.sites_calls == calls  # waits out the retry interval


def test_site_supported_matches_subdomains():
    assert scrape_sync.site_supported("https://www.twkan.com/book/1.html", ["twkan.com"])
    assert scrape_sync.site_supported("https://m.twkan.com/book/1.html", ["twkan.com"])
    assert scrape_sync.site_supported("https://nottwkan.com/x", ["twkan.com"]) is False
    assert scrape_sync.site_supported("https://x.com/", None) is None


def test_canvas_lines_are_counted_not_held():
    chapters = [{"title": "第5章 x", "text": "正文", "sourceUrls": ["u5"], "secCanvasCount": 3}]
    to_queue, report = scrape_sync.number_chapters(
        chapters, offset=0, base_number=4, numbered=True, existing=set(), new_only=True)
    assert [n for n, _, _ in to_queue] == [5]
    assert report["anomalies"] == [] and report["canvas_lines"] == 3
