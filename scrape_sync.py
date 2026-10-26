"""Automated source scraping — the logic scrape_worker.py runs.

The scraper (a separate service on a residential connection, see
scraper_client.py) runs one job at a time. t9 owns the lane: scrape_tasks is
the queue, and ScrapeEngine.tick() keeps at most one toc/chapters job on the
scraper, submitting the next only after the last one is ingested. The order is
priority, then age: TOC checks first (cheap, and they discover the work), then
catch-ups for auto-translate books, then catch-ups for queue-only books, then
backfills. Every chapters task is a chunk of at most scrape_chunk_size, so a
long backfill yields to newly arrived TOC checks between chunks.

Where a book stands is a cursor *URL* — the source URL of the last chapter
ingested — never a count or a TOC position: sources insert and delete notice
"chapters" mid-TOC, which shifts positions but not URLs. A cursor that vanishes
from the TOC stops the book rather than guessing.

Chapter numbers come from the title (第N章) plus the book's number_offset.
Anything that would end in a chapter-conflict prompt — numbers going
backwards, duplicates, gaps, twkan's anti-scrape canvas lines — is ingested
(or skipped) with a warning and puts the book on hold, which blocks the
automatic translation start until someone looks.
"""
import datetime
import json
import os
import re
import unicodedata
import urllib.error
from urllib.parse import urlsplit

from db.scrape_repo import now_iso
from scraper_client import ScraperClient, ScraperError

MODES = ("off", "queue", "auto")

PRIORITY_TOC = 10
PRIORITY_CATCHUP_AUTO = 20
PRIORITY_CATCHUP = 30
PRIORITY_BACKFILL = 40

# A book whose checks failed this many times in a row is skipped by the daily
# sweep until someone runs "Check now".
FAILURE_LIMIT = 3
# Waits before retry N of a failed task; one more failure after the last fails it.
RETRY_BACKOFF_MINUTES = (30, 120, 480)
# A scraper that can't be reached is not the task's fault: retry without
# spending an attempt.
UNREACHABLE_RETRY_MINUTES = 5
# A translation start refused because every job slot is busy.
AUTOSTART_RETRY_MINUTES = 15
AUTOSTART_GIVE_UP_HOURS = 6

DEFAULTS = {
    "scrape_enabled": True,
    "scrape_daily_hour": 5,
    "scrape_chunk_size": 50,
    "scrape_default_gap": 10,
    "scrape_toc_spacing_seconds": 20,
    "scraper_url": "http://100.65.58.103:8787",
    "scrape_engine": "",
}


def setting(key):
    try:
        import settings_store
        val = settings_store.get(key, DEFAULTS[key])
    except Exception:
        val = DEFAULTS[key]
    return DEFAULTS[key] if val is None else val


def _env_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


def scraper_token():
    """SCRAPER_TOKEN, re-read from .env so a token saved in Settings reaches the
    long-running worker without a restart."""
    try:
        from dotenv import dotenv_values
        val = dotenv_values(_env_path()).get("SCRAPER_TOKEN")
        if val:
            return val
    except Exception:
        pass
    return os.environ.get("SCRAPER_TOKEN", "")


def make_client(timeout: float = 30) -> ScraperClient:
    return ScraperClient(str(setting("scraper_url")), token=scraper_token(), timeout=timeout)


def _minutes_from_now(minutes, now=None):
    now = now or datetime.datetime.now()
    return (now + datetime.timedelta(minutes=minutes)).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# URLs, TOCs, numbering
# ---------------------------------------------------------------------------

def normalize_book_url(url):
    url = (url or "").strip()
    if url and not re.match(r"^[a-z][a-z0-9+.-]*://", url, re.I):
        url = "https://" + url
    return url


def url_key(url):
    """Compare URLs by host (minus www.) + path + query: the scheme and a
    trailing slash are noise."""
    if not url:
        return ""
    parts = urlsplit(normalize_book_url(url))
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = parts.path.rstrip("/")
    return f"{host}{path}" + (f"?{parts.query}" if parts.query else "")


def site_of(url):
    host = (urlsplit(normalize_book_url(url)).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000}
_CN_BIG = {"万": 10000, "萬": 10000}


def cn_to_num(s):
    """'一千零二十三' -> 1023, '十五' -> 15, '123' -> 123."""
    s = s.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    if s.isdigit():
        return int(s)
    total = section = digit = 0
    for ch in s:
        if ch in _CN_DIGITS:
            digit = _CN_DIGITS[ch]
        elif ch in _CN_UNITS:
            section += (digit or 1) * _CN_UNITS[ch]
            digit = 0
        elif ch in _CN_BIG:
            total += (section + digit) * _CN_BIG[ch]
            section = digit = 0
        elif ch.isdigit():
            digit = digit * 10 + int(ch)
        else:
            return None
    return total + section + digit


_TITLE_NUMBER_RE = re.compile(r"第\s*([0-9０-９零〇一二三四五六七八九十百千万萬两]+)\s*[章节節回]")


def parse_title_number(title):
    m = _TITLE_NUMBER_RE.search(title or "")
    return cn_to_num(m.group(1)) if m else None


def compact_toc(toc):
    chapters = [{"position": c.get("position"), "number": c.get("number"),
                 "title": c.get("title") or "", "url": c.get("url")}
                for c in (toc.get("chapters") or [])]
    return {"chapterCount": toc.get("chapterCount", len(chapters)),
            "titlesKnown": toc.get("titlesKnown", True),
            "chapters": chapters}


def toc_is_numbered(toc_chapters):
    """Do this source's titles carry 第N章? (If not, chapters are numbered
    sequentially from the cursor.) A minority of unnumbered entries — leave
    notices, extras — doesn't change the answer."""
    if not toc_chapters:
        return True
    hits = sum(1 for c in toc_chapters if parse_title_number(c.get("title")) is not None)
    return hits * 2 >= len(toc_chapters)


def toc_index(toc_chapters, url):
    key = url_key(url)
    if not key:
        return None
    for i, c in enumerate(toc_chapters or []):
        if url_key(c.get("url")) == key:
            return i
    return None


def cursor_index(state, toc_chapters):
    """Index of the cursor entry; -1 = linked before the first entry
    (everything is new); None = unlinked, or the cursor vanished."""
    if not state.get("linked"):
        return None
    if not state.get("cursor_url"):
        return -1
    return toc_index(toc_chapters, state["cursor_url"])


def to_simplified(text):
    try:
        import trad_simp
        return trad_simp.convert_text(text)
    except Exception:
        return text


def normalize_title(title):
    """Fold a chapter title for comparison: simplified script, half-width
    punctuation, no 第N章 prefix, no whitespace. The TOC is in whatever
    script the site serves; our stored source may have been converted."""
    t = unicodedata.normalize("NFKC", title or "")
    t = to_simplified(t)
    t = _TITLE_NUMBER_RE.sub("", t)
    return re.sub(r"\s+", "", t)


def propose_link(toc_chapters, have_max, offset=0, our_titles=None):
    """Where the cursor should go: the TOC entry that is our chapter
    `have_max` (the highest chapter we hold).

    Returns {index, chapter_number, number_offset, method, matched, confident}
    or None. index -1 = before the first entry (an empty book). Methods, best
    first:
      title    — our recent chapters' source titles found in the TOC; the
                 TOC-index minus chapter-number delta they agree on places
                 have_max. Confident when two titles agree, or have_max's own
                 title matched.
      number   — a TOC title's 第N章 + offset equals have_max.
      position — TOC entry #have_max (sources that number by position).
    """
    if not have_max:
        return {"index": -1, "chapter_number": None, "number_offset": offset,
                "method": "empty", "matched": 0, "confident": True}
    n_toc = len(toc_chapters)

    def result(index, method, matched=0, confident=False):
        if not (0 <= index < n_toc):
            return None
        title_n = parse_title_number(toc_chapters[index].get("title"))
        return {"index": index, "chapter_number": have_max,
                "number_offset": (have_max - title_n) if title_n is not None else offset,
                "method": method, "matched": matched, "confident": confident}

    if our_titles:
        by_norm = {}
        for i, c in enumerate(toc_chapters):
            norm = normalize_title(c.get("title"))
            if len(norm) >= 2:
                by_norm.setdefault(norm, []).append(i)
        deltas = {}
        own_match = False
        for k, title in our_titles.items():
            hits = by_norm.get(normalize_title(title)) or []
            if len(hits) == 1:
                deltas.setdefault(hits[0] - k, []).append(k)
                own_match = own_match or k == have_max
        if deltas:
            delta, ks = max(deltas.items(), key=lambda kv: len(kv[1]))
            r = result(have_max + delta, "title", matched=len(ks),
                       confident=len(ks) >= 2 or (own_match and have_max in ks))
            if r:
                return r

    if toc_is_numbered(toc_chapters):
        hits = [i for i, c in enumerate(toc_chapters)
                if (n := parse_title_number(c.get("title"))) is not None and n + offset == have_max]
        if len(hits) == 1:
            return result(hits[0], "number")
    return result(have_max - 1, "position")


def number_chapters(chapters, *, offset, base_number, numbered, existing,
                    new_only=False, prior_added=()):
    """Assign our chapter numbers to a scraped batch and decide what to queue.

    base_number is the number this batch should continue from (the cursor's
    number when the task was created; None for a backfill with no anchor).
    new_only (catch-ups): every chapter is past the cursor, so a number we
    already hold means the numbering is off — an anomaly, not a skip.
    prior_added are numbers this same task queued before a retry resumed it.
    Returns (to_queue, report): to_queue is [(number, title, lines)], report
    holds the skips, the anomalies and the last chapter's url/number (the new
    cursor candidate).
    """
    report = {"received": len(chapters), "skipped_existing": [], "skipped_unnumbered": [],
              "skipped_empty": 0, "anomalies": [], "last_url": None, "last_number": None}
    to_queue = []
    seen = set()
    prev = base_number
    for ch in chapters:
        urls = ch.get("sourceUrls") or []
        if urls:
            report["last_url"] = urls[0]
        title = (ch.get("title") or "").strip()
        if numbered:
            n = parse_title_number(title)
            if n is None:
                report["skipped_unnumbered"].append(title or "(untitled)")
                continue
            number = n + offset
        else:
            number = (prev or 0) + 1

        if prev is not None and number <= prev:
            report["anomalies"].append(
                f"ch{number} ({title}) does not follow ch{prev} — numbering went backwards or repeated "
                f"(if the source renumbered, relink the book or fix its offset)")
            continue
        if number in seen:
            report["anomalies"].append(f"duplicate ch{number} ({title})")
            continue
        if prev is not None and number > prev + 1:
            report["anomalies"].append(f"gap: ch{prev} -> ch{number}")
        seen.add(number)
        prev = number
        report["last_number"] = number

        if ch.get("secCanvasCount"):
            report["anomalies"].append(
                f"ch{number} has {ch['secCanvasCount']} anti-scrape canvas line(s) — text may be missing")
        text = ch.get("text") or ""
        if not text.strip():
            report["skipped_empty"] += 1
            report["anomalies"].append(f"ch{number} came back empty")
            continue
        if number in existing:
            report["skipped_existing"].append(number)
            if new_only and number not in prior_added:
                report["anomalies"].append(
                    f"ch{number} ({title}) is new at the source but we already have a ch{number} — "
                    f"the chapter numbering is probably off; relink the book")
            continue
        to_queue.append((number, title or f"Chapter {number}", text.splitlines()))
    return to_queue, report


def format_ranges(nums):
    nums = sorted(set(nums))
    if not nums:
        return ""
    parts, start, prev = [], nums[0], nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
            continue
        parts.append((start, prev))
        start = prev = n
    parts.append((start, prev))
    return ", ".join(str(a) if a == b else f"{a}–{b}" for a, b in parts)


def queue_chapter_chunk(db, book_id, reason, entries, *, base_number, mode=None, priority=None,
                        to_url=None, chain_added=0):
    """Queue the next chunk of `entries` (TOC entries, in order) as a chapters
    task. The chunk ends after scrape_chunk_size chapters; its completion
    queues the following one, behind anything of higher priority."""
    if not entries:
        return None
    if priority is None:
        if reason == "backfill":
            priority = PRIORITY_BACKFILL
        else:
            priority = PRIORITY_CATCHUP_AUTO if mode == "auto" else PRIORITY_CATCHUP
    chunk = max(1, int(setting("scrape_chunk_size")))
    return db.create_scrape_task(
        book_id, "chapters", reason=reason, priority=priority,
        from_url=entries[0]["url"], to_url=to_url or entries[-1]["url"],
        max_chapters=min(len(entries), chunk),
        options={"base_number": base_number, "chain_added": chain_added})


# ---------------------------------------------------------------------------
# Admin API (translation starts live in the admin process)
# ---------------------------------------------------------------------------

class AdminAPI:
    """The two admin calls the worker makes, over t9_client's minted cookie."""

    def __init__(self, url=None):
        self.url = url

    def _client(self):
        import t9_client
        return t9_client.client(self.url) if self.url else t9_client.client()

    def running_book_ids(self):
        with self._client() as c:
            r = c.get("/api/translate/jobs")
            r.raise_for_status()
            return {j.get("book_id") for j in r.json().get("jobs", [])}

    def process_next(self, payload):
        with self._client() as c:
            r = c.post("/api/queue/process-next", json=payload)
            try:
                body = r.json()
            except ValueError:
                body = {"detail": r.text[:200]}
            return r.status_code, body


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------

class ScrapeEngine:
    def __init__(self, db, client_factory=make_client, admin=None, logger=None, clock=None):
        self.db = db
        self.client_factory = client_factory
        self.admin = admin or AdminAPI()
        self.logger = logger
        self.clock = clock or datetime.datetime.now

    # -- helpers -------------------------------------------------------------

    def _now(self):
        return self.clock().isoformat(timespec="seconds")

    def _log(self, level, msg):
        if self.logger:
            getattr(self.logger, level)(msg)

    def _activity(self, book_id, message, type_="scrape"):
        try:
            book = self.db.get_book(book_id=book_id) or {}
            self.db.add_activity_log(type_, message, book_id=book_id, book_name=book.get("title"))
        except Exception as e:
            self._log("warning", f"activity log failed: {e}")
        self._log("info", f"[book {book_id}] {message}")

    def _book_url(self, book_id):
        book = self.db.get_book(book_id=book_id) or {}
        return normalize_book_url(book.get("source_url"))

    # -- tick ----------------------------------------------------------------

    def tick(self):
        """One pass: schedule the daily sweep, run due translation starts,
        then advance the scraper lane by one step. Returns 'busy' while a
        scraper job is in flight, else 'idle'."""
        if not setting("scrape_enabled"):
            return "disabled"
        self.schedule_sweep()
        self.run_autostarts()
        return self.step_lane()

    # -- daily sweep ---------------------------------------------------------

    def schedule_sweep(self):
        now = self.clock()
        hour = int(setting("scrape_daily_hour"))
        sweep_at = now.replace(hour=max(0, min(23, hour)), minute=0, second=0, microsecond=0)
        if now < sweep_at:
            return 0
        sweep_iso = sweep_at.isoformat(timespec="seconds")
        spacing = int(setting("scrape_toc_spacing_seconds"))
        per_site = {}
        queued = 0
        for b in self.db.list_book_scrapes():
            if b["mode"] not in ("queue", "auto") or not b.get("source_url"):
                continue
            if b["consecutive_failures"] >= FAILURE_LIMIT:
                continue
            if b.get("last_sweep_at") and b["last_sweep_at"] >= sweep_iso:
                continue
            if self.db.has_open_scrape_task(b["book_id"], kinds=("toc",)):
                continue
            site = site_of(b["source_url"])
            i = per_site.get(site, 0)
            per_site[site] = i + 1
            not_before = (now + datetime.timedelta(seconds=i * spacing)).isoformat(timespec="seconds")
            self.db.create_scrape_task(b["book_id"], "toc", reason="sweep",
                                       priority=PRIORITY_TOC, not_before=not_before)
            self.db.update_book_scrape(b["book_id"], last_sweep_at=self._now())
            queued += 1
        if queued:
            self._log("info", f"daily sweep: queued {queued} TOC check(s)")
        return queued

    # -- the lane ------------------------------------------------------------

    def step_lane(self):
        task = self.db.lane_task()
        if task is None:
            task = self.db.next_scrape_task(self._now())
            if task is None:
                return "idle"
            self.submit(task)
            return "busy"
        self.poll(task)
        return "busy"

    def submit(self, task):
        url = self._book_url(task["book_id"])
        if not url:
            self.db.update_scrape_task(task["id"], status="failed", last_error="book has no source URL",
                                       finished_at=self._now())
            return
        sc = self.client_factory()
        try:
            if task.get("scraper_job_id"):
                try:
                    job = sc.retry(task["scraper_job_id"])
                except ScraperError:
                    # The scraper no longer has it (pruned, or never failed):
                    # start over — ingest dedupes whatever comes back twice.
                    job = self._submit_fresh(sc, task, url)
            else:
                job = self._submit_fresh(sc, task, url)
        except ScraperError as e:
            # HTTP error from the scraper: unsupported site, bad URL. Permanent.
            self._fail(task, str(e), retryable=False)
            return
        except (urllib.error.URLError, OSError) as e:
            self.db.update_scrape_task(task["id"], last_error=f"scraper unreachable: {e}",
                                       not_before=_minutes_from_now(UNREACHABLE_RETRY_MINUTES, self.clock()))
            return
        self.db.update_scrape_task(task["id"], status="running", scraper_job_id=str(job["id"]),
                                   started_at=task.get("started_at") or self._now(),
                                   last_error=None, vnc_url=None)

    def _submit_fresh(self, sc, task, url):
        # Blank engine = the scraper's own default (Patchright today).
        engine = (str(setting("scrape_engine") or "")).strip() or None
        if task["kind"] == "toc":
            return sc.submit_toc(url, engine=engine)
        # Backfills go in at the scraper's low priority, so anything submitted
        # to it by hand (the CLI) runs ahead of them; t9's own lane already
        # orders what it sends.
        return sc.submit_chapters(url, from_url=task.get("from_url"),
                                  max_chapters=task.get("max_chapters"), engine=engine,
                                  priority="low" if task.get("reason") == "backfill" else None)

    def poll(self, task):
        job_id = task.get("scraper_job_id")
        if not job_id:
            # Claimed but never submitted (worker died in between): resubmit.
            self.submit(task)
            return
        sc = self.client_factory()
        try:
            if task["status"] == "cancelling":
                sc.cancel(job_id)
            job = sc.status(job_id)
        except ScraperError as e:
            self._fail(task, f"scraper lost job {job_id}: {e}", retryable=True, drop_job=True)
            return
        except (urllib.error.URLError, OSError):
            return  # transient; try again next tick
        status = job.get("status")
        if status in ("done", "failed", "cancelled"):
            self.finish(task, job, sc)
            return
        fields = {"progress": job.get("progress") or {}, "vnc_url": job.get("vncUrl")}
        if task["status"] != "cancelling":
            fields["status"] = "blocked" if status == "blocked" else "running"
        self.db.update_scrape_task(task["id"], **fields)

    def finish(self, task, job, sc):
        status = job.get("status")
        if task["kind"] == "toc":
            if status == "done":
                self.on_toc(task, sc.result(job["id"]))
            elif status == "cancelled" or task["status"] == "cancelling":
                self.db.update_scrape_task(task["id"], status="cancelled", finished_at=self._now())
            else:
                self._fail(task, job.get("error") or "TOC job failed", retryable=True, drop_job=True)
            return

        result = {}
        try:
            result = sc.result(job["id"]) or {}
        except (ScraperError, urllib.error.URLError, OSError) as e:
            if status == "done":
                return  # result not readable yet; poll again next tick
            self._log("warning", f"task {task['id']}: no partial result: {e}")
        report = self.ingest(task, result) if result.get("chapters") else None

        if status == "done":
            self.on_chapters_done(task, job, report)
        elif status == "cancelled" or task["status"] == "cancelling":
            self.db.update_scrape_task(task["id"], status="cancelled", finished_at=self._now(),
                                       result_summary=report)
        else:
            self._fail(task, job.get("error") or "scrape job failed", retryable=True,
                       summary=report)

    def _fail(self, task, error, *, retryable, drop_job=False, summary=None):
        attempts = (task.get("attempts") or 0) + 1
        fields = {"attempts": attempts, "last_error": error, "vnc_url": None}
        if summary is not None:
            fields["result_summary"] = summary
        if retryable and attempts <= len(RETRY_BACKOFF_MINUTES):
            fields.update(status="queued",
                          not_before=_minutes_from_now(RETRY_BACKOFF_MINUTES[attempts - 1], self.clock()))
            if drop_job:
                fields["scraper_job_id"] = None
            self.db.update_scrape_task(task["id"], **fields)
            self._log("warning", f"task {task['id']} (book {task['book_id']}) failed, retry "
                                 f"{attempts}/{len(RETRY_BACKOFF_MINUTES)}: {error}")
            return
        fields.update(status="failed", finished_at=self._now())
        self.db.update_scrape_task(task["id"], **fields)
        state = self.db.get_book_scrape(task["book_id"])
        self.db.update_book_scrape(task["book_id"],
                                   consecutive_failures=(state.get("consecutive_failures") or 0) + 1,
                                   last_error=error)
        self._activity(task["book_id"], f"Scrape {task['kind']} failed: {error}", type_="scrape_error")

    # -- results -------------------------------------------------------------

    def on_toc(self, task, toc):
        book_id = task["book_id"]
        toc = compact_toc(toc or {})
        chs = toc["chapters"]
        state = self.db.get_book_scrape(book_id, include_toc=False)
        fields = {"last_toc": toc, "toc_count": len(chs),
                  "toc_latest_title": chs[-1]["title"] if chs else None,
                  "last_checked_at": self._now(), "consecutive_failures": 0, "last_error": None}
        summary = {"toc_count": len(chs)}
        if not state.get("linked") and chs:
            linked = self.auto_link(book_id, chs, state)
            if linked:
                state = self.db.get_book_scrape(book_id)
                fields.pop("pending_count", None)
                summary["auto_linked"] = linked["index"]
        idx = cursor_index(state, chs)
        if state.get("linked") and idx is None:
            msg = "the linked chapter is no longer in the source TOC — relink the book"
            fields.update(pending_count=None, hold_reason=msg, last_error=msg)
            self.db.update_book_scrape(book_id, **fields)
            self.db.update_scrape_task(task["id"], status="done", finished_at=self._now(),
                                       result_summary={**summary, "error": msg})
            self._activity(book_id, f"Scrape: {msg}", type_="scrape_error")
            return
        new = chs[idx + 1:] if idx is not None else []
        fields["pending_count"] = len(new) if idx is not None else None
        self.db.update_book_scrape(book_id, **fields)
        summary["new"] = fields["pending_count"]

        opts = task.get("options") or {}
        # The daily check — and a forced one ("Check now" from Books) — scrape
        # what's new for a switched-on book; a plain manual check only reads.
        want = opts.get("catchup") or (task.get("reason") in ("sweep", "forced")
                                       and state["mode"] in ("queue", "auto"))
        if new and want and not self.db.has_open_scrape_task(book_id, kinds=("chapters",)):
            queue_chapter_chunk(self.db, book_id, "catchup", new,
                                base_number=state.get("cursor_number"), mode=state["mode"])
            summary["catchup_queued"] = len(new)
        self.db.update_scrape_task(task["id"], status="done", finished_at=self._now(),
                                   result_summary=summary)

    def auto_link(self, book_id, chs, state):
        """Link an unlinked book on its TOC read when the source titles of
        our latest chapters place it unambiguously. Anything less stays a
        proposal in the Source modal."""
        rows = self.db.list_book_scrapes(book_id=book_id)
        have_max = rows[0].get("have_max") if rows else None
        prop = propose_link(chs, have_max, state.get("number_offset") or 0,
                            our_titles=self.db.recent_source_titles(book_id))
        if not prop or not prop["confident"] or prop["method"] not in ("title", "empty"):
            return None
        apply_link(self.db, book_id, chs, prop["index"], prop["chapter_number"], prop["number_offset"])
        where = "before the first entry" if prop["index"] < 0 else \
            f"#{prop['index'] + 1} \u201c{chs[prop['index']].get('title')}\u201d = ch{have_max}"
        self._activity(book_id, f"Scrape: auto-linked {where} ({prop['matched']} title match(es))")
        return prop

    def ingest(self, task, result):
        book_id = task["book_id"]
        state = self.db.get_book_scrape(book_id, include_toc=True)
        toc_chs = (state.get("last_toc") or {}).get("chapters") or []
        opts = task.get("options") or {}
        to_queue, report = number_chapters(
            result.get("chapters") or [],
            offset=state.get("number_offset") or 0,
            base_number=opts.get("base_number"),
            numbered=toc_is_numbered(toc_chs),
            existing=self.db.existing_chapter_numbers(book_id),
            new_only=task.get("reason") == "catchup",
            prior_added=set(((task.get("result_summary") or {}).get("added")) or []))
        site = result.get("site") or site_of(state.get("cursor_url") or "")
        added = []
        for number, title, lines in to_queue:
            qid = self.db.add_to_queue(book_id=book_id, content=lines, title=title,
                                       chapter_number=number, source=f"scrape:{site}")
            if qid:
                added.append(number)
        try:
            from modules import module_activity
            module_activity.flush(book_id)
        except Exception:
            pass
        report["added"] = added
        if result.get("job", {}).get("stopReason") == "stall":
            report["anomalies"].append("the scraper stalled (page navigation stopped moving)")

        # Advance the cursor — never backwards (a backfill behind it must not
        # rewind where the daily check resumes from), and never for an
        # unlinked book or one whose cursor vanished: those wait for a relink.
        fields = {}
        last_url = report.get("last_url")
        cur_idx = cursor_index(state, toc_chs)
        if last_url and cur_idx is not None:
            new_idx = toc_index(toc_chs, last_url)
            if (new_idx is not None and new_idx > cur_idx) or \
                    (new_idx is None and task.get("reason") == "catchup"):
                fields["cursor_url"] = last_url
                if report.get("last_number") is not None:
                    fields["cursor_number"] = report["last_number"]
                if new_idx is not None:
                    fields["pending_count"] = max(0, len(toc_chs) - new_idx - 1)
        if added:
            fields["last_new_at"] = self._now()
        if report["anomalies"]:
            prior = state.get("hold_reason")
            note = "; ".join(report["anomalies"][:5])
            fields["hold_reason"] = (f"{prior}; {note}" if prior else note)[:2000]
        if fields:
            self.db.update_book_scrape(book_id, **fields)

        parts = [f"queued {len(added)} chapter(s)" + (f" (ch{format_ranges(added)})" if added else "")]
        if report["skipped_existing"]:
            parts.append(f"{len(report['skipped_existing'])} already present")
        if report["skipped_unnumbered"]:
            parts.append(f"skipped unnumbered: {', '.join(report['skipped_unnumbered'][:3])}")
        if report["anomalies"]:
            parts.append(f"⚠ {len(report['anomalies'])} warning(s) — book held for review")
        self._activity(book_id, "Scrape: " + "; ".join(parts),
                       type_="scrape_error" if report["anomalies"] else "scrape")
        return report

    def on_chapters_done(self, task, job, report):
        book_id = task["book_id"]
        opts = task.get("options") or {}
        chain_added = (opts.get("chain_added") or 0) + len((report or {}).get("added") or [])
        self.db.update_scrape_task(task["id"], status="done", finished_at=self._now(),
                                   result_summary=report)
        self.db.update_book_scrape(book_id, consecutive_failures=0, last_error=None)

        state = self.db.get_book_scrape(book_id, include_toc=True)
        toc_chs = (state.get("last_toc") or {}).get("chapters") or []
        stop = job.get("stopReason")
        remaining = []
        last_url = (report or {}).get("last_url")
        if stop == "maxChapters" and last_url:
            start = toc_index(toc_chs, last_url)
            end = toc_index(toc_chs, task.get("to_url")) if task.get("to_url") else len(toc_chs) - 1
            if start is not None and end is not None and end > start:
                remaining = toc_chs[start + 1:end + 1]
        if remaining:
            base = (report or {}).get("last_number")
            if base is None:
                base = opts.get("base_number")
            tid = queue_chapter_chunk(self.db, book_id, task.get("reason"), remaining,
                                      base_number=base, priority=task["priority"],
                                      to_url=task.get("to_url"), chain_added=chain_added)
            self._log("info", f"book {book_id}: next chunk queued as task {tid} "
                              f"({len(remaining)} chapter(s) left)")
            return
        if task.get("reason") == "catchup" and chain_added and state.get("mode") == "auto":
            self.request_autostart(book_id)

    # -- automatic translation start ------------------------------------------

    def request_autostart(self, book_id):
        if any(t["kind"] == "autostart"
               for t in self.db.list_scrape_tasks(statuses=("queued",), book_id=book_id)):
            return None
        return self.db.create_scrape_task(book_id, "autostart", reason="catchup", priority=0)

    def autostart_decision(self, book_id):
        """(ok, reason). ok=True means start a job now."""
        state = self.db.get_book_scrape(book_id)
        if state.get("mode") != "auto":
            return False, "book is no longer in auto mode"
        if state.get("hold_reason"):
            return False, f"held: {state['hold_reason']}"
        rows = self.db.list_book_scrapes(book_id=book_id)
        if not rows or not rows[0].get("queued_count"):
            return False, "nothing queued"
        head = rows[0].get("queued_max")
        translated = rows[0].get("translated_max") or 0
        limit = state.get("auto_gap")
        if limit is None:
            limit = int(setting("scrape_default_gap"))
        if head is not None and head - translated > limit:
            return False, (f"gap too large: queued up to ch{head}, translated to ch{translated} "
                           f"(limit {limit}) — translate it by hand or raise the gap")
        return True, None

    def run_options(self, book_id):
        """The process-next payload. The model is whatever translated the
        book's most recent chapter — the book's own continuity, not a setting
        that could drift from it."""
        state = self.db.get_book_scrape(book_id)
        opts = dict(state.get("run_options") or {})
        payload = {
            "book_id": book_id,
            "translation_model": self.db.latest_translation_model(book_id),
            "no_review": opts.get("no_review", True),
            "save_as_draft": bool(opts.get("save_as_draft", False)),
            "auto_process": True,
        }
        return payload

    def run_autostarts(self):
        for task in self.db.due_autostart_tasks(self._now()):
            self._run_autostart(task)

    def _run_autostart(self, task):
        book_id = task["book_id"]
        done = lambda summary, status="done": self.db.update_scrape_task(  # noqa: E731
            task["id"], status=status, finished_at=self._now(), result_summary=summary)
        ok, why = self.autostart_decision(book_id)
        if not ok:
            done({"started": False, "reason": why}, status="skipped")
            self._activity(book_id, f"Auto-translate not started: {why}")
            return
        try:
            if book_id in self.admin.running_book_ids():
                done({"started": False, "reason": "already translating"})
                return
            payload = self.run_options(book_id)
            code, body = self.admin.process_next(payload)
        except Exception as e:
            code, body = None, {"detail": str(e)}
        if code == 200:
            done({"started": True, "payload": payload})
            self._activity(book_id, f"Auto-translate started ({payload.get('translation_model') or 'default model'})",
                           type_="scrape_autostart")
            return
        if code == 404:
            done({"started": False, "reason": "queue empty"})
            return
        created = datetime.datetime.fromisoformat(task["created_at"])
        if self.clock() - created > datetime.timedelta(hours=AUTOSTART_GIVE_UP_HOURS):
            msg = (body or {}).get("detail") or f"HTTP {code}"
            done({"started": False, "reason": f"gave up: {msg}"}, status="failed")
            self._activity(book_id, f"Auto-translate gave up after {AUTOSTART_GIVE_UP_HOURS}h: {msg}",
                           type_="scrape_error")
            return
        self.db.update_scrape_task(
            task["id"], attempts=(task.get("attempts") or 0) + 1,
            last_error=(body or {}).get("detail") or f"HTTP {code}",
            not_before=_minutes_from_now(AUTOSTART_RETRY_MINUTES, self.clock()))


# ---------------------------------------------------------------------------
# Entry points the admin API shares with the worker
# ---------------------------------------------------------------------------

def enqueue_check(db, book_id, *, catchup=False, reason="manual"):
    """Queue a TOC check now (front of the lane). Resets the failure streak —
    "Check now" is how a book that the sweep gave up on comes back."""
    db.update_book_scrape(book_id, consecutive_failures=0)
    return db.create_scrape_task(book_id, "toc", reason=reason, priority=PRIORITY_TOC,
                                 options={"catchup": True} if catchup else None)


def apply_link(db, book_id, chs, index, chapter_number, number_offset):
    """Point the cursor at TOC entry `index` (-1 = before the first entry)
    as our chapter `chapter_number`, with the given title-number offset."""
    if index == -1:
        db.update_book_scrape(book_id, linked=1, cursor_url=None, cursor_number=None,
                              pending_count=len(chs), hold_reason=None, last_error=None)
        return {"linked": True, "cursor_url": None, "pending": len(chs)}
    entry = chs[index]
    db.update_book_scrape(book_id, linked=1, cursor_url=entry["url"], cursor_number=chapter_number,
                          number_offset=number_offset, pending_count=len(chs) - index - 1,
                          hold_reason=None, last_error=None)
    return {"linked": True, "cursor_url": entry["url"], "cursor_number": chapter_number,
            "number_offset": number_offset, "pending": len(chs) - index - 1}


def link_book(db, book_id, index, chapter_number=None):
    """Link at TOC entry `index` as our chapter `chapter_number`. The offset
    between the entry's 第N章 and our number is derived, so new chapters
    are numbered the way this book already is. With no chapter number, the
    proposal's mapping (title match) decides it, else the entry's own title."""
    state = db.get_book_scrape(book_id, include_toc=True)
    chs = (state.get("last_toc") or {}).get("chapters") or []
    if index != -1 and not (0 <= index < len(chs)):
        raise ValueError("TOC entry out of range — run a TOC check first")
    offset = state.get("number_offset") or 0
    if index == -1:
        return apply_link(db, book_id, chs, -1, None, offset)
    title_n = parse_title_number(chs[index].get("title"))
    if chapter_number is None:
        chapter_number = (title_n + offset) if title_n is not None else index + 1
    if title_n is not None:
        offset = chapter_number - title_n
    return apply_link(db, book_id, chs, index, chapter_number, offset)


def enqueue_backfill(db, book_id, from_index, to_index=None):
    """Queue a chunked scrape of TOC entries [from_index, to_index]."""
    state = db.get_book_scrape(book_id, include_toc=True)
    chs = (state.get("last_toc") or {}).get("chapters") or []
    if not chs:
        raise ValueError("no TOC on record — run a TOC check first")
    to_index = len(chs) - 1 if to_index is None else to_index
    if not (0 <= from_index <= to_index < len(chs)):
        raise ValueError("TOC range out of bounds")
    entries = chs[from_index:to_index + 1]
    # Anchor numbering on the entry before the range when its title tells us.
    base = None
    if from_index > 0:
        n = parse_title_number(chs[from_index - 1].get("title"))
        if n is not None:
            base = n + (state.get("number_offset") or 0)
    return queue_chapter_chunk(db, book_id, "backfill", entries, base_number=base)
