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
import uuid
from urllib.parse import urlsplit

from db.scrape_repo import now_iso
from scraper_client import ScraperClient, ScraperError
from settings_store import DEFAULT_SCRAPER_URL

MODES = ("off", "queue", "auto")

PRIORITY_TOC = 10
PRIORITY_CATCHUP_AUTO = 20
PRIORITY_CATCHUP = 30
# A read-only TOC read for a book whose site the scraper just learned: after
# the catch-ups (new chapters for books already on), before backfills.
PRIORITY_ONBOARD = 35
PRIORITY_BACKFILL = 40

# A book whose checks failed this many times in a row is skipped by the daily
# sweep until someone runs "Check now".
FAILURE_LIMIT = 3
# Waits before retry N of a failed task; one more failure after the last fails it.
RETRY_BACKOFF_MINUTES = (30, 120, 480)
# A scraper that can't be reached is not the task's fault: retry without
# spending an attempt.
UNREACHABLE_RETRY_MINUTES = 5
# The scraper's /supported_sites list is re-read this often (and retried
# this often after a failed read, keeping the last good list meanwhile).
SITES_REFRESH_HOURS = 24
SITES_RETRY_MINUTES = 60
# Books waiting to auto-translate are checked against the admin's job slots
# this often (one GET /api/translate/jobs per check, not per book). They wait
# as long as they stay eligible — there is no give-up timeout, because nothing
# would re-request a start until the book's next new chapter.
AUTOSTART_CHECK_SECONDS = 60

DEFAULTS = {
    "scrape_enabled": True,
    "scrape_daily_hour": 5,
    "scrape_chunk_size": 50,
    "scrape_default_gap": 10,
    "scrape_toc_spacing_seconds": 20,
    "scraper_url": DEFAULT_SCRAPER_URL,
    "scrape_engine": "",
    "scrape_notify_url": "",
    "scraper_vnc_url": "",
    "scrape_reserved_slots": 1,
    "scrape_translation_model": "claudecode:claude-opus-5-5",
}


def setting(key):
    try:
        import settings_store
        val = settings_store.get(key, DEFAULTS[key])
    except Exception:
        val = DEFAULTS[key]
    return DEFAULTS[key] if val is None else val


def auto_translate_model(run_options=None):
    """The model an auto-start uses: the book's override, else the global
    scrape_translation_model (a blank setting falls back to its default)."""
    override = ((run_options or {}).get("translation_model") or "").strip()
    return override or (setting("scrape_translation_model") or "").strip() \
        or DEFAULTS["scrape_translation_model"]


def valid_model_spec(spec):
    """provider:model, the shape TranslationConfig.parse_model_spec takes."""
    provider, sep, model = (spec or "").strip().partition(":")
    return bool(sep and provider and model and not any(c.isspace() for c in spec.strip()))


def _env_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


def _env_secret(key):
    """A secret from .env, re-read on every call so a value saved in Settings
    (or edited by hand) reaches the long-running worker without a restart."""
    try:
        from dotenv import dotenv_values
        val = dotenv_values(_env_path()).get(key)
        if val:
            return val
    except Exception:
        pass
    return os.environ.get(key, "")


def scraper_token():
    return _env_secret("SCRAPER_TOKEN")


def notify_secret():
    """Shared HMAC key the scraper signs its "blocked" notices with."""
    return _env_secret("SCRAPER_NOTIFY_SECRET")


def vnc_url(reported=None):
    """The noVNC page where a human solves a challenge, or None for no link.
    The scraper knows its own noVNC address (it reports vncUrl with a blocked
    job, signed in a notice), so that wins; scraper_vnc_url is the fallback
    for a scraper that sends none."""
    if isinstance(reported, str) and re.match(r"^https?://", reported.strip()):
        return reported.strip()
    return (str(setting("scraper_vnc_url") or "")).strip() or None


def notify_kwargs():
    """Per-job callback so a blocked job reaches /api/scraper-notify. Sent
    only when both the URL and the secret are configured: an unsigned notice
    would be refused anyway."""
    url = (str(setting("scrape_notify_url") or "")).strip()
    secret = notify_secret()
    if not url or not secret:
        return {}
    return {"notify_url": url, "notify_kind": "json", "notify_secret": secret}


def make_client(timeout: float = 30) -> ScraperClient:
    return ScraperClient(str(setting("scraper_url")), token=scraper_token(), timeout=timeout)


# ---------------------------------------------------------------------------
# Which sites the scraper services (GET /supported_sites)
# ---------------------------------------------------------------------------

SITES_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "scraper_sites.json")


def fetch_supported_sites(sc):
    """The scraper's sorted list of domains it will service. scraper_client.py
    is synced verbatim from ~/scraper, so call its own method when it has one."""
    if hasattr(sc, "supported_sites"):
        sites = sc.supported_sites()
    else:
        sites = sc._req("GET", "/supported_sites")[1]
    if not isinstance(sites, list):
        raise ScraperError(f"/supported_sites returned {type(sites).__name__}, not a list")
    return sorted({str(d).strip().lower().removeprefix("www.") for d in sites if str(d).strip()})


def load_supported_sites():
    """The cached list: {"sites": [...], "fetched_at": iso} or None if the
    worker has never read it. Unknown means "don't block anything"."""
    try:
        with open(SITES_CACHE) as f:
            data = json.load(f)
        return data if isinstance(data.get("sites"), list) else None
    except (OSError, ValueError, AttributeError):
        return None


def save_supported_sites(sites, fetched_at):
    try:
        os.makedirs(os.path.dirname(SITES_CACHE), exist_ok=True)
        tmp = SITES_CACHE + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"sites": sites, "fetched_at": fetched_at}, f)
        os.replace(tmp, SITES_CACHE)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Pause (the Scraping page's Pause/Resume)
# ---------------------------------------------------------------------------

# A file, not a setting: it is operational state the worker reads every tick,
# and it survives a restart of the worker — which is what it's for.
PAUSE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "scrape_paused.json")


def load_pause():
    """{"paused_at": iso} while the lane is paused, else None."""
    try:
        with open(PAUSE_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {"paused_at": None}
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {"paused_at": None}  # unreadable: stay paused rather than guess


def set_paused(paused):
    if not paused:
        try:
            os.remove(PAUSE_FILE)
        except FileNotFoundError:
            pass
        return None
    state = load_pause() or {"paused_at": now_iso()}
    os.makedirs(os.path.dirname(PAUSE_FILE), exist_ok=True)
    tmp = PAUSE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, PAUSE_FILE)
    return state


def site_supported(url, sites):
    """True/False against a known list (a subdomain of a listed domain counts);
    None when the list is unknown."""
    if sites is None:
        return None
    site = site_of(url)
    return any(site == d or site.endswith("." + d) for d in sites)


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

# A bare number run straight into the title, no 第…章 (novel543: "377妖界裂縫",
# "363【…】三"). Only when the next character is an ideograph or an opening
# bracket, so these stay unnumbered: 69shuba's "670.请假条" (TOC position,
# not the author's number), "001：…", and date/range notices ("12月更新計劃",
# "2026一月月票…", "673和674發布…"). Weaker evidence than 第N章: ingest also
# requires it to agree with the entry's place in the sequence (number_chapters).
_BARE_NUMBER_RE = re.compile(r"^\s*([0-9０-９]+)(?=[一-鿿【「『《（(])")
_NOT_A_TITLE_START = set("年月日號号和至到、零〇一二三四五六七八九十百千万萬两")


def title_number(title):
    """(number, bare): bare=True when it came from a bare leading number
    rather than 第N章 — weaker evidence, see number_chapters."""
    m = _TITLE_NUMBER_RE.search(title or "")
    if m:
        return cn_to_num(m.group(1)), False
    m = _BARE_NUMBER_RE.match(title or "")
    if m and title[m.end()] not in _NOT_A_TITLE_START:
        return cn_to_num(m.group(1)), True
    return None, False


def parse_title_number(title):
    return title_number(title)[0]


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
              "skipped_empty": 0, "anomalies": [], "canvas_lines": 0,
              "last_url": None, "last_number": None}
    to_queue = []
    seen = set()
    prev = base_number
    anchor_i = -1          # batch index of the last numbered entry (-1 = the base)
    for i, ch in enumerate(chapters):
        urls = ch.get("sourceUrls") or []
        if urls:
            report["last_url"] = urls[0]
        title = (ch.get("title") or "").strip()
        if numbered:
            n, bare = title_number(title)
            # A bare number only counts when it agrees with where the entry
            # sits: the next chapter after the last numbered one, or that
            # chapter plus its distance in the list (so one missed entry
            # doesn't strand everything after it). "52请假条" at ch1339 is a
            # note and is classified like any unnumbered entry, never queued
            # under a wrong number or left holding the book.
            if bare and prev is not None and n + offset not in (prev + 1, prev + i - anchor_i):
                n = None
            if n is None:
                report["skipped_unnumbered"].append(title or "(untitled)")
                # Kept whole (text included) for ingest to classify and, if it
                # isn't confidently an author's note, park for the admin.
                report.setdefault("_unnumbered", []).append(
                    {"title": title or "(untitled)", "url": urls[0] if urls else None,
                     "text": ch.get("text") or "", "after": prev})
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
        anchor_i = i
        report["last_number"] = number

        # twkan's anti-scrape <canvas> lines: the scraper recovers their text,
        # so they're counted for diagnostics only — never a reason to hold.
        if ch.get("secCanvasCount"):
            report["canvas_lines"] += ch["secCanvasCount"]
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

    def jobs(self):
        """(running book ids, the translation cap) from the admin's job registry."""
        with self._client() as c:
            r = c.get("/api/translate/jobs")
            r.raise_for_status()
            body = r.json()
            return ({j.get("book_id") for j in body.get("jobs", [])},
                    int(body.get("max_concurrent") or 0))

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
        self._sites_retry_at = None
        self._autostart_check_at = None

    # -- helpers -------------------------------------------------------------

    def _now(self):
        return self.clock().isoformat(timespec="seconds")

    def _log(self, level, msg):
        if self.logger:
            getattr(self.logger, level)(msg)

    def _activity(self, book_id, message, type_="scrape"):
        """Log a line for one book as "Scrape (Book Title): message" — the
        activity log mixes every book's events, so the line names its book."""
        title = None
        try:
            title = (self.db.get_book(book_id=book_id) or {}).get("title")
        except Exception:
            pass
        line = f"Scrape ({title or f'book {book_id}'}): {message}"
        try:
            self.db.add_activity_log(type_, line, book_id=book_id, book_name=title)
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
        if load_pause():
            return self.step_paused()
        self.refresh_sites()
        self.schedule_sweep()
        self.run_autostarts()
        return self.step_lane()

    # -- supported sites -----------------------------------------------------

    def refresh_sites(self, force=False):
        """Re-read /supported_sites once a day. A site that appears onboards
        its books: each gets a read-only TOC check, which links it when its
        titles line up — no mode changes, that stays the admin's call. The
        first read ever onboards nothing (the rollout script is for that)."""
        now = self.clock()
        cached = load_supported_sites()
        if not force:
            if self._sites_retry_at and now < self._sites_retry_at:
                return None
            if cached and cached.get("fetched_at"):
                try:
                    age = now - datetime.datetime.fromisoformat(cached["fetched_at"])
                    if age < datetime.timedelta(hours=SITES_REFRESH_HOURS):
                        return cached["sites"]
                except ValueError:
                    pass
        try:
            sites = fetch_supported_sites(self.client_factory())
        except (ScraperError, urllib.error.URLError, OSError, ValueError) as e:
            self._sites_retry_at = now + datetime.timedelta(minutes=SITES_RETRY_MINUTES)
            self._log("warning", f"could not read /supported_sites (keeping the last list): {e}")
            return cached["sites"] if cached else None
        self._sites_retry_at = None
        save_supported_sites(sites, self._now())
        if cached is not None:
            added = sorted(set(sites) - set(cached["sites"]))
            removed = sorted(set(cached["sites"]) - set(sites))
            if removed:
                self._log("warning", f"scraper dropped site(s): {', '.join(removed)}")
            if added:
                self.onboard_sites(added)
        return sites

    def onboard_sites(self, domains):
        books = []
        for b in self.db.list_book_scrapes():
            if not b.get("source_url") or not site_supported(b["source_url"], domains):
                continue
            if (b.get("book_status") or "") in ("completed", "dropped") or b.get("linked"):
                continue
            if self.db.has_open_scrape_task(b["book_id"], kinds=("toc",)):
                continue
            enqueue_check(self.db, b["book_id"], reason="onboard", priority=PRIORITY_ONBOARD)
            books.append(b["book_id"])
        msg = f"scraper now supports {', '.join(domains)}"
        msg += f" — checking {len(books)} book(s)" if books else " — no books use it yet"
        self._log("info", msg)
        try:
            self.db.add_activity_log("scrape", f"Scrape: {msg}")
        except Exception:
            pass
        return books

    # -- daily sweep ---------------------------------------------------------

    def schedule_sweep(self):
        now = self.clock()
        hour = int(setting("scrape_daily_hour"))
        sweep_at = now.replace(hour=max(0, min(23, hour)), minute=0, second=0, microsecond=0)
        if now < sweep_at:
            return 0
        sweep_iso = sweep_at.isoformat(timespec="seconds")
        spacing = int(setting("scrape_toc_spacing_seconds"))
        known = load_supported_sites()
        sites = known["sites"] if known else None
        per_site = {}
        queued = 0
        for b in self.db.list_book_scrapes():
            if b["mode"] not in ("queue", "auto") or not b.get("source_url"):
                continue
            # A site the scraper doesn't service would only fail and burn a
            # lane slot; the book comes back the day its site is listed.
            if site_supported(b["source_url"], sites) is False:
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

    def step_paused(self):
        """Paused: nothing new goes to the scraper, no sweep, no translation
        starts. A job still on the scraper is suspended — cancelled there and
        put back in the queue with its job id, so after Resume submit()
        retries it and the scraper carries on after the last page it stored.
        'busy' until the lane is clear, then 'paused'."""
        task = self.db.lane_task()
        if task is None:
            return "paused"
        if not task.get("scraper_job_id"):
            self.db.update_scrape_task(task["id"], status="queued")
            return "paused"
        if task["status"] in ("running", "blocked"):
            self.db.update_scrape_task(task["id"], status="suspending")
            task["status"] = "suspending"
        self.poll(task)
        return "busy" if self.db.lane_task() else "paused"

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
        notify = notify_kwargs()
        if task["kind"] == "toc":
            return sc.submit_toc(url, engine=engine, **notify)
        # Backfills go in at the scraper's low priority, so anything submitted
        # to it by hand (the CLI) runs ahead of them; t9's own lane already
        # orders what it sends.
        return sc.submit_chapters(url, from_url=task.get("from_url"),
                                  max_chapters=task.get("max_chapters"), engine=engine,
                                  priority="low" if task.get("reason") == "backfill" else None,
                                  **notify)

    def poll(self, task):
        job_id = task.get("scraper_job_id")
        if not job_id:
            # Claimed but never submitted (worker died in between): resubmit.
            self.submit(task)
            return
        sc = self.client_factory()
        try:
            if task["status"] in ("cancelling", "suspending"):
                try:
                    sc.cancel(job_id)
                except ScraperError:
                    pass  # already over (409): status() below says how it ended
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
        fields = {"progress": job.get("progress") or {},
                  "vnc_url": vnc_url(job.get("vncUrl")) if status == "blocked" else None}
        if task["status"] not in ("cancelling", "suspending"):
            fields["status"] = "blocked" if status == "blocked" else "running"
        self.db.update_scrape_task(task["id"], **fields)

    def finish(self, task, job, sc):
        status = job.get("status")
        if task["status"] == "suspending" and status != "done":
            # Paused mid-job: nothing is ingested now — the resumed job's
            # result carries these pages as well.
            self.db.update_scrape_task(task["id"], status="queued", not_before=None,
                                       progress=None, vnc_url=None)
            self._log("info", f"task {task['id']} (book {task['book_id']}) suspended for the pause")
            return
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
        self._activity(task["book_id"], f"{task['kind']} job failed: {error}", type_="scrape_error")

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
            self._activity(book_id, msg, type_="scrape_error")
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
        self._activity(book_id, f"auto-linked {where} ({prop['matched']} title match(es))")
        return prop

    def sort_unnumbered(self, book_id, report):
        """Unnumbered entries in a numbered TOC are usually author's notes, but
        not always (a recap, a 番外 side story). Jev classifies each from the
        text already scraped: a confident `filler` is skipped, anything else —
        story, unsure, or Jev unavailable — is parked on the book for the admin
        to discard, append to the chapter before it, or queue under a number.
        Parked entries block auto-translate until decided. Never raises."""
        entries = report.pop("_unnumbered", None) or []
        report["skipped_notes"], report["parked"] = [], []
        if not entries:
            return
        import chapter_triage
        import jev_client
        threshold = chapter_triage.threshold()
        items = self.db.get_unplaced(book_id)
        known = {i.get("url") for i in items if i.get("url")}
        changed = False
        for e in entries:
            if e["url"] and e["url"] in known:
                continue                    # re-received on a retry: already sorted
            verdict = None
            if jev_client.is_configured():
                try:
                    verdict = chapter_triage.classify_chapter(e["title"], e["text"])
                except Exception as err:
                    self._log("warning", f"book {book_id}: classifying {e['title']!r} failed: {err}")
            if verdict and verdict.get("choice") == "filler" and verdict.get("confidence", 0) >= threshold:
                report["skipped_notes"].append(e["title"])
                status = "skipped"
            else:
                report["parked"].append(e["title"])
                status = "pending"
            items.append({
                "id": uuid.uuid4().hex[:12], "title": e["title"], "url": e["url"],
                "after": e["after"], "status": status,
                "verdict": (verdict or {}).get("choice"), "confidence": (verdict or {}).get("confidence"),
                "classifier": jev_client.label() if verdict else None,
                # A skipped note keeps no text: it's listed for the record only.
                "text": e["text"] if status == "pending" else None,
                "found_at": self._now(),
            })
            changed = True
        if changed:
            self.db.set_unplaced(book_id, _trim_unplaced(items))

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
        self.sort_unnumbered(book_id, report)
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
        if report.get("skipped_notes"):
            parts.append(f"skipped author's note(s): {', '.join(report['skipped_notes'][:3])}")
        if report.get("parked"):
            parts.append(f"⚠ {len(report['parked'])} unnumbered entr{'y' if len(report['parked']) == 1 else 'ies'} "
                         f"parked for you to place: {', '.join(report['parked'][:3])}")
        if report["anomalies"]:
            parts.append(f"⚠ {len(report['anomalies'])} warning(s) — book held for review")
        self._activity(book_id, "; ".join(parts),
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
        if state.get("unplaced_pending"):
            return False, (f"{state['unplaced_pending']} unnumbered entr"
                           f"{'y' if state['unplaced_pending'] == 1 else 'ies'} awaiting a decision "
                           f"(Source Scraping)")
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
        """The process-next payload. The model is the book's own override
        (run_options.translation_model) or else scrape_translation_model —
        never the model of the book's latest chapter: a book picked back up
        after a long break would resume on whatever weaker model was current
        when it stopped."""
        state = self.db.get_book_scrape(book_id)
        opts = dict(state.get("run_options") or {})
        payload = {
            "book_id": book_id,
            "translation_model": auto_translate_model(opts),
            "no_review": opts.get("no_review", True),
            "save_as_draft": bool(opts.get("save_as_draft", False)),
            "auto_process": True,
        }
        return payload

    def run_autostarts(self, force=False):
        """Start waiting auto-translate books, oldest request first, while a
        slot is free — and always leave scrape_reserved_slots of the
        translation cap free for manual work. Waiting books are re-checked
        every AUTOSTART_CHECK_SECONDS and wait as long as they stay eligible."""
        now = self.clock()
        if not force and self._autostart_check_at and now < self._autostart_check_at:
            return 0
        waiting = self.db.due_autostart_tasks(self._now())     # oldest first
        if not waiting:
            return 0
        self._autostart_check_at = now + datetime.timedelta(seconds=AUTOSTART_CHECK_SECONDS)
        try:
            running, cap = self.admin.jobs()
        except Exception as e:
            self._log("warning", f"auto-translate: admin unreachable, will retry: {e}")
            for task in waiting:
                self.db.update_scrape_task(task["id"], last_error=f"admin unreachable: {e}")
            return 0
        reserved = max(0, int(setting("scrape_reserved_slots")))
        # A cap of 1 with one reserved would never auto-translate at all;
        # auto mode always gets at least one slot.
        allowed = max(1, cap - reserved) if cap else 0
        free = allowed - len(running)
        started = 0
        position = 0
        for task in waiting:
            book_id = task["book_id"]
            ok, why = self.autostart_decision(book_id)
            if not ok:
                self._finish_autostart(task, {"started": False, "reason": why}, status="skipped")
                self._activity(book_id, f"Auto-translate not started: {why}")
                continue
            if book_id in running:
                # Its own job is on it; an auto_process run picks the new rows up.
                self._finish_autostart(task, {"started": False, "reason": "already translating"})
                continue
            if free <= 0:
                position += 1
                self.db.update_scrape_task(task["id"], progress={"waiting": "slot", "position": position},
                                           last_error=None)
                continue
            if self._start_translation(task):
                free -= 1
                started += 1
                running.add(book_id)
            else:
                position += 1
        return started

    def _finish_autostart(self, task, summary, status="done"):
        self.db.update_scrape_task(task["id"], status=status, finished_at=self._now(),
                                   result_summary=summary, progress=None)

    def _start_translation(self, task):
        book_id = task["book_id"]
        payload = self.run_options(book_id)
        try:
            code, body = self.admin.process_next(payload)
        except Exception as e:
            code, body = None, {"detail": str(e)}
        if code == 200:
            self._finish_autostart(task, {"started": True, "payload": payload})
            self._activity(book_id, f"Auto-translate started ({payload.get('translation_model') or 'default model'})",
                           type_="scrape_autostart")
            return True
        if code == 404:
            self._finish_autostart(task, {"started": False, "reason": "queue empty"})
            return True   # nothing to wait for; doesn't hold a place in line
        # 409 (a slot taken between our check and the start — a manual job),
        # or a transient failure: keep its place and try at the next check.
        self.db.update_scrape_task(task["id"], attempts=(task.get("attempts") or 0) + 1,
                                   last_error=(body or {}).get("detail") or f"HTTP {code}",
                                   progress={"waiting": "slot"})
        return False


# ---------------------------------------------------------------------------
# Entry points the admin API shares with the worker
# ---------------------------------------------------------------------------

# Decided entries are kept (without text) so a retry re-receiving one doesn't
# park it again; the oldest beyond this many are dropped.
UNPLACED_KEEP_DECIDED = 50


def _trim_unplaced(items):
    pending = [i for i in items if i.get("status") == "pending"]
    decided = [i for i in items if i.get("status") != "pending"][-UNPLACED_KEEP_DECIDED:]
    keep = {id(i) for i in pending + decided}
    return [i for i in items if id(i) in keep]


class UnplacedError(ValueError):
    """A resolution that can't be applied; .status is the HTTP code to answer with."""
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def resolve_unplaced(db, book_id, item_id, action, chapter_number=None):
    """Decide one parked entry:
      discard — it was a note after all
      append  — add its text to the end of chapter `chapter_number` (default:
                the chapter it followed). A queued chapter just grows; a saved
                one has its stored source extended and is re-queued, so the
                retranslation's source matches and the conflict check passes.
      queue   — queue it as chapter `chapter_number` (must be free)
    Returns a short description of what was done."""
    items = db.get_unplaced(book_id)
    item = next((i for i in items if i.get("id") == item_id), None)
    if item is None or item.get("status") != "pending":
        raise UnplacedError("No such pending entry.", status=404)
    book = db.get_book(book_id=book_id) or {}
    lines = (item.get("text") or "").splitlines()
    if action == "discard":
        done = "discarded"
    elif action == "append":
        target = chapter_number if chapter_number is not None else item.get("after")
        if not isinstance(target, int) or target < 1:
            raise UnplacedError("Which chapter should it be appended to?")
        from modules import apply_source_ingest
        extra = apply_source_ingest(book, [item["title"]] + lines, db.config, db.logger, db=db,
                                    chapter_number=target)
        row = db.queue_row_for_chapter(book_id, target)
        if row and row[1] == "processing":
            raise UnplacedError(f"Chapter {target} is being translated right now — try again once it's saved.",
                                status=409)
        if row:
            db.set_queue_content(row[0], row[2] + [""] + list(extra))
            done = f"appended to queued ch{target}"
        else:
            saved = db.chapter_source(book_id, target)
            if not saved:
                raise UnplacedError(f"There is no chapter {target}, queued or saved.")
            title, source = saved
            db.add_to_queue(book_id=book_id, content=source + [""] + list(extra), title=title,
                            chapter_number=target, source="scrape:append",
                            retranslation_reason=(f"The source posted an extra after this chapter "
                                                  f"(“{item['title']}”); it has been appended to the "
                                                  f"source. Translate the whole chapter, extra included."))
            # Store exactly what was queued (after add_to_queue's own source
            # transforms) as the chapter's source, so the retranslation's
            # conflict check sees identical text and proceeds silently.
            queued = db.queue_row_for_chapter(book_id, target)
            if queued:
                db.set_chapter_source(book_id, target, queued[2])
            done = f"appended to saved ch{target} and re-queued it for translation"
    elif action == "queue":
        if not isinstance(chapter_number, int) or chapter_number < 1:
            raise UnplacedError("Pick a chapter number to queue it as.")
        if chapter_number in db.existing_chapter_numbers(book_id):
            raise UnplacedError(f"Chapter {chapter_number} already exists.", status=409)
        db.add_to_queue(book_id=book_id, content=lines, title=item["title"],
                        chapter_number=chapter_number, source="scrape:unplaced")
        done = f"queued as ch{chapter_number}"
    else:
        raise UnplacedError("action must be discard, append or queue")
    item.update(status=action if action != "queue" else "queued", resolution=done,
                resolved_at=datetime.datetime.now().isoformat(timespec="seconds"), text=None)
    db.set_unplaced(book_id, _trim_unplaced(items))
    try:
        db.add_activity_log("scrape", f"Scrape ({book.get('title') or f'book {book_id}'}): "
                                      f"unnumbered entry “{item['title']}” {done}",
                            book_id=book_id, book_name=book.get("title"))
    except Exception:
        pass
    return done


def blocked_notice(db, payload):
    """Turn a verified scraper "blocked" notice into (book_id, title, line, url):
    the activity-log line naming the book, and the noVNC link (None when the
    admin set none). The task is matched by the scraper's job id."""
    job_id = str(payload.get("jobId") or "")
    task = db.find_scrape_task_by_job(job_id) if job_id else None
    book_id = task["book_id"] if task else None
    title = task.get("book_title") if task else None
    site = payload.get("site") or "the source site"
    what = {"toc": " while reading the TOC", "chapters": " while scraping chapters"}.get(
        (task or {}).get("kind"), "")
    line = (f"Scrape ({title or (f'book {book_id}' if book_id else 'unknown book')}): blocked on a "
            f"Cloudflare challenge at {site}{what} — it needs a human to solve it")
    url = vnc_url(payload.get("vncUrl"))
    if task:
        db.update_scrape_task(task["id"], status="blocked", vnc_url=url)
    return book_id, title, line, url


def enqueue_check(db, book_id, *, catchup=False, reason="manual", priority=PRIORITY_TOC):
    """Queue a TOC check now (front of the lane). Resets the failure streak —
    "Check now" is how a book that the sweep gave up on comes back."""
    db.update_book_scrape(book_id, consecutive_failures=0)
    return db.create_scrape_task(book_id, "toc", reason=reason, priority=priority,
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
