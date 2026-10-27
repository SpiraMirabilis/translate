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

    def submit_toc(self, url, engine=None, **notify):
        return self._new("toc", url=url, engine=engine, **notify)

    def submit_chapters(self, url, from_url=None, max_chapters=None, priority=None, engine=None, **notify):
        return self._new("chapters", url=url, from_url=from_url, max_chapters=max_chapters,
                         priority=priority, engine=engine, **notify)

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
    def __init__(self, code=200, cap=3):
        self.code = code
        self.cap = cap
        self.running = set()
        self.started = []

    def jobs(self):
        return set(self.running), self.cap

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
    "scrape_engine": "", "scrape_notify_url": "", "scraper_vnc_url": "", "scrape_reserved_slots": 1,
    "scrape_translation_model": "claudecode:global-model",
}


@pytest.fixture(autouse=True)
def fixed_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(scrape_sync, "setting", lambda k: SETTINGS[k])
    monkeypatch.setattr(scrape_sync, "SITES_CACHE", str(tmp_path / "scraper_sites.json"))
    monkeypatch.setattr(scrape_sync, "PAUSE_FILE", str(tmp_path / "scrape_paused.json"))


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
    # A bare number run into the title (novel543) counts, as does 第-less N章.
    assert scrape_sync.parse_title_number("377妖界裂縫") == 377
    assert scrape_sync.parse_title_number("363【鬼魂】三") == 363
    assert scrape_sync.parse_title_number("342第十個保安") == 342
    assert scrape_sync.parse_title_number("357與鬼魂們達成交易") == 357
    assert scrape_sync.parse_title_number("272章 先廢了") == 272
    assert scrape_sync.title_number("377妖界裂縫") == (377, True)
    assert scrape_sync.title_number("第5章 x") == (5, False)
    # TOC positions, dates and notices do not.
    for title in ("670.请假条", "001：1996，你的名字", "12月更新計劃", "2026一月月票抽獎",
                  "673和674發布的時候順序搞反了", "２０２６年总结"):
        assert scrape_sync.parse_title_number(title) is None, title
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


def test_numbering_anomalies_warn_but_queue_and_never_hold(db, book):
    # The book 137 case: the source reposted 第4章, and a gap. Both copies are
    # queued in source order; the translation-time conflict check settles it.
    entries = [entry(1), entry(2), entry(4), entry(4, "第四章 重复")]
    entries[3]["url"] = "https://twkan.com/txt/1/4b.html"
    scraper = FakeScraper(entries)
    engine = make_engine(db, scraper)
    seed_queue(db, book, [1])
    check_and_link(db, engine, book)
    db.update_book_scrape(book, mode="auto")
    scrape_sync.enqueue_check(db, book, catchup=True)
    drain(engine)
    assert [q["chapter_number"] for q in db.list_queue(book_id=book)] == [1, 2, 4, 4]
    state = db.get_book_scrape(book)
    assert state["hold_reason"] is None
    assert "gap: ch2 -> ch4" in state["warnings"]
    assert "numbering went backwards or repeated" in state["warnings"]
    assert engine.admin.started  # warnings don't block the automatic start


def test_volume_restart_is_queued_in_source_order():
    # Book 17: volume 2 starts again at 第1章. Every entry goes in, in order,
    # and the sequence follows the source (no gap noise inside volume 2).
    chs = [{"title": f"第{n}章 t{i}", "text": "x", "sourceUrls": [f"u{i}"]}
           for i, n in enumerate([299, 300, 1, 2, 4])]
    to_queue, report = scrape_sync.number_chapters(
        chs, offset=0, base_number=298, numbered=True, existing={1, 2, 4, 298}, new_only=True)
    assert [n for n, *_ in to_queue] == [299, 300, 1, 2, 4]
    assert report["last_number"] == 4 and report["last_url"] == "u4"
    assert any("ch1 (第1章 t2) follows ch300" in w for w in report["anomalies"])
    assert "gap: ch2 -> ch4" in report["anomalies"]
    assert sum("collides" in w for w in report["anomalies"]) == 3  # ch1, ch2, ch4


def test_backfill_skips_only_the_same_chapter():
    chs = [{"title": "第1章 裴长老", "text": "x", "sourceUrls": ["a"]},     # we hold this one
           {"title": "第1章 新卷开端", "text": "x", "sourceUrls": ["b"]},   # vol 2 ch1: different
           {"title": "第2章", "text": "x", "sourceUrls": ["c"]}]            # nothing to compare
    held = {1: {scrape_sync.title_key("# 第1章 裴长老")}, 2: {scrape_sync.title_key("# 第2章 你爹死了")}}
    to_queue, report = scrape_sync.number_chapters(
        chs, offset=0, base_number=None, numbered=True, existing={1, 2},
        titles_at=lambda n: held.get(n, set()))
    assert [(n, u) for n, _, _, u in to_queue] == [(1, "b")]
    assert report["skipped_existing"] == [1, 2]
    assert any("no title to tell" in w for w in report["anomalies"])


def test_source_titles_at_reads_queue_and_saved_heads(db, book):
    db.add_to_queue(book_id=book, content=["x"], title="第3章 甲", chapter_number=3)
    db.save_chapter(book, 3, "Ch3", ["# 第3章 乙", "正文"], ["# Chapter 3", "text"])
    assert sorted(db.source_titles_at(book, 3)) == ["# 第3章 乙", "第3章 甲"]
    assert scrape_sync.title_key("# 第3章 乙") == scrape_sync.title_key("第3章 乙") == "乙"
    assert scrape_sync.held_title_keys(db, book, 3) == {"甲", "乙"}
    # A saved chapter that opens straight into prose has no title to compare.
    db.save_chapter(book, 5, "Ch5", ["南海西道防城，一场春雨刚刚过去。"], ["text"])
    assert scrape_sync.held_title_keys(db, book, 5) == set()


def test_number_chapters_retry_skips_by_url_not_number():
    # A retry re-ingests from the top: the first copy of 119 was queued before
    # the failure, the repost wasn't. The repost must still go in.
    chs = [{"title": "第119章 a", "text": "x", "sourceUrls": ["u1"]},
           {"title": "第119章 a", "text": "y", "sourceUrls": ["u2"]}]
    to_queue, report = scrape_sync.number_chapters(
        chs, offset=0, base_number=118, numbered=True, existing={119}, new_only=True,
        prior_added={119}, prior_urls={"u1"})
    assert [(n, u) for n, _, _, u in to_queue] == [(119, "u2")]
    assert report["skipped_existing"] == [119]


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
        def submit_toc(self, url, engine=None, **notify):
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


def test_pause_suspends_the_job_and_resume_retries_it(db, book):
    scraper = FakeScraper([entry(n) for n in range(1, 4)])
    clock = Clock()
    clock.now = clock.now.replace(hour=3)  # before the daily sweep
    engine = make_engine(db, scraper, clock=clock)
    check_and_link(db, engine, book)
    db.update_book_scrape(book, mode="queue")
    scrape_sync.queue_chapter_chunk(db, book, "catchup",
                                    db.get_book_scrape(book, include_toc=True)["last_toc"]["chapters"],
                                    base_number=None)
    engine.step_lane()  # on the scraper
    task = db.lane_task()
    assert task["status"] == "running"

    scrape_sync.set_paused(True)
    assert engine.tick() == "paused"
    task = db.get_scrape_task(task["id"])
    assert task["status"] == "queued" and task["scraper_job_id"]
    assert scraper.jobs[task["scraper_job_id"]]["status"] == "cancelled"
    assert queued_numbers(db, book) == []  # nothing ingested from the suspended job

    scrape_sync.enqueue_check(db, book)  # queued work waits while paused
    for _ in range(3):
        assert engine.tick() == "paused"
    assert db.lane_task() is None
    calls = len(scraper.calls)

    scrape_sync.set_paused(False)
    drain(engine)
    kinds = [k for k, _ in scraper.calls[calls:]]
    # The TOC check (priority 10) goes first; the suspended catch-up then
    # resumes on the scraper rather than being submitted afresh.
    assert kinds[:2] == ["toc", "retry"]  # then the next chunk (chunk size 2)
    assert queued_numbers(db, book) == [1, 2, 3]


def test_pause_survives_a_restart_and_repeat_cancels_are_harmless(db, book):
    class Strict(FakeScraper):
        def cancel(self, job_id):  # the real scraper 409s on a job that's already over
            if self.jobs[job_id]["status"] in ("done", "failed", "cancelled"):
                raise ScraperError(f"DELETE /jobs/{job_id}: HTTP 409")
            super().cancel(job_id)
    scraper = Strict([entry(1)])
    engine = make_engine(db, scraper)
    scrape_sync.enqueue_check(db, book)
    engine.step_lane()
    task = db.lane_task()
    db.update_scrape_task(task["id"], status="suspending")
    scraper.jobs[task["scraper_job_id"]]["status"] = "cancelled"  # cancel already landed
    scrape_sync.set_paused(True)
    engine2 = scrape_sync.ScrapeEngine(db, client_factory=lambda: scraper, admin=FakeAdmin(),
                                       clock=Clock())
    assert engine2.tick() == "paused"
    assert db.get_scrape_task(task["id"])["status"] == "queued"
    assert scrape_sync.load_pause()["paused_at"]


def test_autostart_respects_gap_and_uses_global_model(db, book):
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
    # The global setting, not the model of the latest chapter: a book resumed
    # after a break must not inherit a weaker model from when it stopped.
    assert payload["translation_model"] == "claudecode:global-model"


def test_autostart_model_per_book_override(db, book, monkeypatch):
    engine = make_engine(db, FakeScraper([]))
    db.save_chapter(book, 1, "第1章", ["a"], ["A"], translation_model="claude:old-model")
    assert engine.run_options(book)["translation_model"] == "claudecode:global-model"
    db.update_book_scrape(book, run_options={"translation_model": "deepseek:cheap", "no_review": True})
    assert engine.run_options(book)["translation_model"] == "deepseek:cheap"
    # A blank override follows the global setting; a blank global, its default.
    db.update_book_scrape(book, run_options={"translation_model": "  "})
    assert engine.run_options(book)["translation_model"] == "claudecode:global-model"
    monkeypatch.setitem(SETTINGS, "scrape_translation_model", "")
    assert engine.run_options(book)["translation_model"] == "claudecode:claude-opus-5-5"


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


def _ready_auto_book(db, title):
    """An auto-mode book with one queued chapter: eligible to auto-translate."""
    book_id = db.create_book(title)
    db.update_book(book_id, source_url=f"https://twkan.com/book/{book_id}.html")
    db.update_book_scrape(book_id, mode="auto", linked=1)
    seed_queue(db, book_id, [1])
    return book_id


def _autostart(db, book_id):
    return [t for t in db.list_scrape_tasks(book_id=book_id) if t["kind"] == "autostart"][0]


def test_autostart_waits_for_a_slot_without_giving_up(db):
    admin = FakeAdmin(cap=3)
    admin.running = {901, 902}                 # 2 running, 1 reserved -> no room
    clock = Clock()
    engine = make_engine(db, FakeScraper([]), admin=admin, clock=clock)
    book = _ready_auto_book(db, "Waiting")
    engine.request_autostart(book)
    engine.run_autostarts()
    assert admin.started == []
    task = _autostart(db, book)
    assert task["status"] == "queued" and task["progress"] == {"waiting": "slot", "position": 1}
    clock.advance(days=2)                      # no give-up timeout
    engine.run_autostarts()
    assert admin.started == [] and _autostart(db, book)["status"] == "queued"
    admin.running = {901}                      # a job finished
    clock.advance(seconds=scrape_sync.AUTOSTART_CHECK_SECONDS + 1)
    engine.run_autostarts()
    assert [p["book_id"] for p in admin.started] == [book]
    assert _autostart(db, book)["status"] == "done"


def test_waiting_books_start_oldest_first(db):
    admin = FakeAdmin(cap=3)
    admin.running = {901}                      # room for exactly one more
    engine = make_engine(db, FakeScraper([]), admin=admin)
    first, second = _ready_auto_book(db, "First"), _ready_auto_book(db, "Second")
    engine.request_autostart(first)
    engine.request_autostart(second)
    engine.run_autostarts()
    assert [p["book_id"] for p in admin.started] == [first]
    assert _autostart(db, second)["progress"] == {"waiting": "slot", "position": 1}


def test_reserved_slots_setting_and_a_cap_of_one(db, monkeypatch):
    admin = FakeAdmin(cap=3)
    admin.running = {901, 902}
    engine = make_engine(db, FakeScraper([]), admin=admin)
    book = _ready_auto_book(db, "Reserved")
    engine.request_autostart(book)
    monkeypatch.setitem(SETTINGS, "scrape_reserved_slots", 0)    # use the whole cap
    engine.run_autostarts(force=True)
    assert len(admin.started) == 1

    admin2 = FakeAdmin(cap=1)                  # cap 1, 1 reserved: auto still gets one
    engine2 = make_engine(db, FakeScraper([]), admin=admin2)
    monkeypatch.setitem(SETTINGS, "scrape_reserved_slots", 1)
    other = _ready_auto_book(db, "Solo")
    engine2.request_autostart(other)
    engine2.run_autostarts(force=True)
    assert [p["book_id"] for p in admin2.started] == [other]


def test_a_slot_lost_to_a_manual_job_keeps_the_books_place(db):
    admin = FakeAdmin(code=409, cap=3)         # our check saw room; a manual job took it
    engine = make_engine(db, FakeScraper([]), admin=admin)
    book = _ready_auto_book(db, "Raced")
    engine.request_autostart(book)
    engine.run_autostarts()
    task = _autostart(db, book)
    assert task["status"] == "queued" and task["attempts"] == 1
    admin.code = 200
    engine.run_autostarts(force=True)
    assert len(admin.started) == 2 and _autostart(db, book)["status"] == "done"


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


def test_catchup_collision_is_queued_with_a_warning(db, book):
    scraper = FakeScraper([entry(n) for n in range(1, 5)])
    engine = make_engine(db, scraper)
    seed_queue(db, book, [1, 2, 3, 4])
    scrape_sync.enqueue_check(db, book)
    drain(engine)
    db.update_book_scrape(book, linked=1, cursor_url=entry(3)["url"], cursor_number=2,
                          number_offset=-1)  # linked one too low: 第4章 -> ch3, which exists
    scrape_sync.enqueue_check(db, book, catchup=True)
    drain(engine)
    state = db.get_book_scrape(book)
    assert state["hold_reason"] is None
    assert queued_numbers(db, book) == [1, 2, 3, 3, 4]
    assert "collides with the ch3 we already have" in state["warnings"]


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
    assert [n for n, *_ in to_queue] == [5]
    assert report["anomalies"] == [] and report["canvas_lines"] == 3


def test_bare_number_counts_only_when_it_continues_the_sequence():
    chapters = [{"title": t, "text": "正文", "sourceUrls": [t]}
                for t in ("315陳輝", "316老太太", "52请假条", "1900请假条", "317陰魂")]
    to_queue, report = scrape_sync.number_chapters(
        chapters, offset=0, base_number=314, numbered=True, existing=set(), new_only=True)
    assert [n for n, *_ in to_queue] == [315, 316, 317]
    assert report["anomalies"] == []
    # The two notes take the unnumbered path (Jev, then parking), not a number.
    assert report["skipped_unnumbered"] == ["52请假条", "1900请假条"]


def test_bare_number_agreeing_with_position_survives_a_missed_entry():
    # "357月下" can't be read (a date-style opener), but 358 and 359 still sit
    # where 358 and 359 belong, so they are numbered rather than all parked.
    chapters = [{"title": t, "text": "正文", "sourceUrls": [t]}
                for t in ("356監獄", "357月下", "358【故事】一", "359【故事】二")]
    to_queue, report = scrape_sync.number_chapters(
        chapters, offset=0, base_number=355, numbered=True, existing=set(), new_only=True)
    assert [n for n, *_ in to_queue] == [356, 358, 359]
    assert report["skipped_unnumbered"] == ["357月下"]


def test_activity_lines_name_the_book(db, book):
    scraper = FakeScraper([entry(n) for n in range(1, 3)])
    engine = make_engine(db, scraper)
    check_and_link(db, engine, book)
    scrape_sync.enqueue_check(db, book, catchup=True)
    drain(engine)
    lines = [e["message"] for e in db.get_activity_log(limit=20)]
    assert any(m.startswith("Scrape (Test Book): queued 2 chapter(s)") for m in lines), lines


# -- blocked-job callback ------------------------------------------------------

def test_jobs_carry_the_callback_only_when_fully_configured(db, book, monkeypatch):
    scraper = FakeScraper([entry(1)])
    engine = make_engine(db, scraper)
    monkeypatch.setitem(SETTINGS, "scrape_notify_url", "https://t9.example/api/scraper-notify")
    monkeypatch.setattr(scrape_sync, "notify_secret", lambda: "")
    scrape_sync.enqueue_check(db, book)
    drain(engine)
    assert "notify_url" not in scraper.calls[-1][1]          # no secret: no callback
    monkeypatch.setattr(scrape_sync, "notify_secret", lambda: "s3cret")
    scrape_sync.enqueue_check(db, book)
    drain(engine)
    params = scraper.calls[-1][1]
    assert params["notify_url"] == "https://t9.example/api/scraper-notify"
    assert params["notify_kind"] == "json" and params["notify_secret"] == "s3cret"


def test_blocked_link_prefers_the_scrapers_url_then_our_setting(db, book, monkeypatch):
    scraper = FakeScraper([entry(1)])
    engine = make_engine(db, scraper)
    scrape_sync.enqueue_check(db, book)
    engine.step_lane()                                       # submitted
    reported = {"vncUrl": None}
    scraper.status = lambda jid: {"id": jid, "status": "blocked", "progress": {}, **reported}
    engine.step_lane()
    assert db.lane_task()["status"] == "blocked" and db.lane_task()["vnc_url"] is None
    monkeypatch.setitem(SETTINGS, "scraper_vnc_url", "http://fallback:6080/vnc.html")
    engine.step_lane()
    assert db.lane_task()["vnc_url"] == "http://fallback:6080/vnc.html"
    reported["vncUrl"] = "http://100.65.58.103:6080/vnc.html?autoconnect=1&resize=scale"
    engine.step_lane()
    assert db.lane_task()["vnc_url"] == reported["vncUrl"]


# -- unnumbered TOC entries: skip a confident note, park the rest ----------------

def _with_extra(title="十里坡案小结", text="案件回顾正文"):
    entries = [entry(1), entry(2), entry(0, title), entry(3)]
    entries[2]["url"] = "https://twkan.com/txt/1/extra.html"
    entries[2]["text"] = text
    return entries


def _jev(monkeypatch, choice=None, confidence=0.99):
    import chapter_triage
    import jev_client
    monkeypatch.setattr(jev_client, "is_configured", lambda: choice is not None)
    monkeypatch.setattr(chapter_triage, "classify_chapter",
                        lambda title, lines: {"choice": choice, "confidence": confidence})


def _scrape_all(db, book, entries):
    engine = make_engine(db, FakeScraper(entries))
    check_and_link(db, engine, book)                 # empty book: linked before #1
    db.update_book_scrape(book, mode="auto")
    scrape_sync.enqueue_check(db, book, catchup=True)
    drain(engine)
    return engine


def test_confident_note_is_skipped_and_logged(db, book, monkeypatch):
    _jev(monkeypatch, "filler", 0.98)
    _scrape_all(db, book, _with_extra("请假条", "今天请假一天"))
    assert queued_numbers(db, book) == [1, 2, 3]
    state = db.get_book_scrape(book)
    assert state["unplaced_pending"] == 0
    [item] = db.get_unplaced(book)
    assert item["status"] == "skipped" and item["text"] is None
    lines = [e["message"] for e in db.get_activity_log(limit=20)]
    assert any("skipped author's note(s): 请假条" in m for m in lines)


def test_story_or_unclassified_entry_is_parked_and_blocks_autostart(db, book, monkeypatch):
    _jev(monkeypatch, "story", 0.97)
    engine = _scrape_all(db, book, _with_extra())
    [item] = db.get_unplaced(book)
    assert item["status"] == "pending" and item["after"] == 2 and item["text"] == "案件回顾正文"
    assert db.get_book_scrape(book)["unplaced_pending"] == 1
    engine.run_autostarts(force=True)
    assert engine.admin.started == []                # waits for the decision
    task = [t for t in db.list_scrape_tasks(book_id=book) if t["kind"] == "autostart"][0]
    assert task["status"] == "skipped" and "awaiting a decision" in task["result_summary"]["reason"]

    other = db.create_book("Other")
    db.update_book(other, source_url="https://twkan.com/book/2.html")
    _jev(monkeypatch, None)                          # Jev unavailable: park, never drop
    _scrape_all(db, other, _with_extra())
    assert db.get_unplaced(other)[0]["status"] == "pending"


def test_a_retry_does_not_park_the_same_entry_twice(db, book, monkeypatch):
    _jev(monkeypatch, "story")
    engine = make_engine(db, FakeScraper(_with_extra()))
    check_and_link(db, engine, book)
    for _ in range(2):
        engine.sort_unnumbered(book, {"_unnumbered": [
            {"title": "十里坡案小结", "url": "https://twkan.com/txt/1/extra.html", "text": "x", "after": 2}]})
    assert len(db.get_unplaced(book)) == 1


def _parked(db, book, after=2):
    db.set_unplaced(book, [{"id": "abc", "title": "十里坡案小结", "url": "u", "after": after,
                            "status": "pending", "text": "回顾第一行\n回顾第二行"}])


def test_resolve_discard_and_queue(db, book):
    _parked(db, book)
    assert scrape_sync.resolve_unplaced(db, book, "abc", "discard") == "discarded"
    assert db.get_book_scrape(book)["unplaced_pending"] == 0
    with pytest.raises(scrape_sync.UnplacedError):
        scrape_sync.resolve_unplaced(db, book, "abc", "discard")      # already decided

    _parked(db, book)
    seed_queue(db, book, [5])
    with pytest.raises(scrape_sync.UnplacedError) as e:
        scrape_sync.resolve_unplaced(db, book, "abc", "queue", 5)      # taken
    assert e.value.status == 409
    scrape_sync.resolve_unplaced(db, book, "abc", "queue", 900)
    assert 900 in queued_numbers(db, book)


def test_resolve_append_to_a_queued_chapter(db, book):
    seed_queue(db, book, [2])
    _parked(db, book)
    scrape_sync.resolve_unplaced(db, book, "abc", "append")             # defaults to "after"
    _, _, lines = db.queue_row_for_chapter(book, 2)
    text = [ln for ln in lines if ln.strip()]       # source modules may space paragraphs
    assert text[0] == "正文2" and text[-3:] == ["十里坡案小结", "回顾第一行", "回顾第二行"]


def test_resolve_append_to_a_saved_chapter_requeues_with_matching_source(db, book):
    db.save_chapter(book, 2, "Chapter Two", ["原文"], ["Translated"])
    _parked(db, book)
    done = scrape_sync.resolve_unplaced(db, book, "abc", "append", 2)
    assert "re-queued" in done
    qid, status, queued = db.queue_row_for_chapter(book, 2)
    _, source = db.chapter_source(book, 2)
    text = [ln for ln in source if ln.strip()]
    assert queued == source and text[0] == "原文" and text[-1] == "回顾第二行"
    row = [q for q in db.list_queue(book_id=book) if q["chapter_number"] == 2][0]
    assert "appended" in (row.get("retranslation_reason") or "")


def test_resolve_append_refuses_a_chapter_being_translated(db, book):
    seed_queue(db, book, [2])
    qid, _, _ = db.queue_row_for_chapter(book, 2)
    with db._conn() as conn:
        conn.cursor().execute("UPDATE queue SET status = 'processing' WHERE id = ?", (qid,))
    _parked(db, book)
    with pytest.raises(scrape_sync.UnplacedError) as e:
        scrape_sync.resolve_unplaced(db, book, "abc", "append", 2)
    assert e.value.status == 409
