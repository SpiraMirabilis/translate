"""NovelUpdates posting — the T9 half (see novelupdates-t9.md).

NovelUpdates has no API; the scraper drives a logged-in browser and exposes
each step as a job (`POST /nu/*`). This module owns everything else: which
books are on NU, the per-series state machine, posting each newly published
chapter in order, the ~24 h moderation wait after a first release, holds that
stop a series until a human looks, and alerts.

The NU lane is separate from the scrape lane (scrape_sync) but runs in the
same worker process (scrape_worker.py ticks both), and like it keeps at most
one job on the scraper. Submit persists the scraper job id, so a restart
resumes polling and never posts twice.

States (book_nu.state) — `mode` (off|auto) only gates posting:

    untracked → searching ─┬→ active        found, already in our group
                           ├→ linked        found, never posted by us
                           ├→ needs_review  near misses: a human picks or declares it new
                           └→ new_series    nothing found: a human edits and submits a draft
    new_series → series_submitted → (release-search finds its id; first chapter
                 posted by id) → first_release_sent
    linked → (first chapter ≥ start_chapter posted) → first_release_sent
    first_release_sent → (slug appears in a group sync) → active

`hold_reason` stops posting for one book without changing its state; only a
human clears it. A login/config failure on the scraper holds the whole lane
(logs/nu_hold.json).
"""
import datetime
import hashlib
import json
import os
import re
import unicodedata
import urllib.error

from scraper_client import ScraperError

MODES = ("off", "auto")
STATES = ("untracked", "searching", "linked", "needs_review", "new_series",
          "series_submitted", "first_release_sent", "active")
HOLD_REASONS = ("check_nu", "refused", "bad_slug", "approval_timeout", "failing", "not_public")

PRIORITY_SYNC = 10
PRIORITY_RELEASE_SEARCH = 15
PRIORITY_NAMES = 19
PRIORITY_RELEASE = 20
PRIORITY_SERIES = 25
PRIORITY_SEARCH = 30
PRIORITY_VOCAB = 40

RETRY_BACKOFF_MINUTES = (30, 120, 480)
# A just-submitted series can take a moment to appear in the add-release
# form's Series box: look it up this many times, this far apart.
RELEASE_SEARCH_TRIES = 4
RELEASE_SEARCH_RETRY_MINUTES = 5
UNREACHABLE_RETRY_MINUTES = 5
VOCAB_MAX_AGE_DAYS = 7
PLAN_SECONDS = 30

_HERE = os.path.dirname(os.path.abspath(__file__))
HOLD_FILE = os.path.join(_HERE, "logs", "nu_hold.json")
VOCAB_FILE = os.path.join(_HERE, "logs", "nu_vocabulary.json")

DEFAULTS = {
    "nu_enabled": False,
    "nu_release_spacing_seconds": 60,
    "nu_approval_wait_hours": 20,
    "nu_approval_timeout_hours": 72,
    "nu_group_sync_hours": 4,
    "nu_alert_email": "",
    "nu_ntfy_url": "",
    # Rate limit on backlog releases: NU auto-approves a release only when it
    # finds the chapter in our RSS feed; anything else waits in its manual
    # queue. Its rule isn't known (it never fetched our feed around the
    # auto-approvals we saw); book-feed recency is the proxy: a chapter counts
    # as in the feed when it is among its book feed's newest nu_feed_window_book;
    # nu_feed_window_site > 0 additionally requires the site feed's newest N.
    # Other releases are capped per book (and optionally across books) per
    # rolling window; 0 = off.
    "nu_backlog_limit": 150,
    "nu_backlog_window_hours": 8,
    "nu_backlog_limit_all": 0,
    "nu_feed_window_site": 0,
    "nu_feed_window_book": 100,
}

# Substrings of a failed job's error (novelupdates-t9.md, "Errors").
ERR_CHECK = ("may have gone through", "no reply from novelupdates", "neither confirmed nor refused")
ERR_REFUSED = ("refused the",)
ERR_BAD_SLUG = ("no series at", "not a novelupdates series")
ERR_GLOBAL = ("login failed", "must be set")
ERR_UNKNOWN_NAMES = ("doesn't know",)

KIND_PATHS = {
    "search": "/nu/search",
    "release_search": "/nu/release-search",
    "names": "/nu/associated-names",
    "release": "/nu/releases",
    "sync": "/nu/group/sync",
    "series": "/nu/series",
    "vocab": "/nu/vocabulary",
    "login": "/nu/login",
}


def setting(key):
    try:
        import settings_store
        val = settings_store.get(key, DEFAULTS[key])
    except Exception:
        val = DEFAULTS[key]
    return DEFAULTS[key] if val is None else val


def make_client(timeout: float = 30):
    import scrape_sync
    return scrape_sync.make_client(timeout=timeout)


def submit_nu(sc, path, body):
    """Queue a /nu/* job without waiting. The client's nu_* helpers block
    until the job ends, which the lane can't do (it persists the job id and
    polls across restarts)."""
    return sc._req("POST", path, {k: v for k, v in body.items() if v is not None})[1]


def _iso(dt):
    return dt.isoformat(timespec="seconds")


def _parse(ts):
    try:
        return datetime.datetime.fromisoformat(ts) if ts else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Global hold (scraper login/config) and the vocabulary cache
# ---------------------------------------------------------------------------

def load_hold():
    try:
        with open(HOLD_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def set_hold(reason):
    os.makedirs(os.path.dirname(HOLD_FILE), exist_ok=True)
    tmp = HOLD_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"reason": reason, "at": _iso(datetime.datetime.now())}, f)
    os.replace(tmp, HOLD_FILE)


def clear_hold():
    try:
        os.remove(HOLD_FILE)
    except OSError:
        pass


def load_vocab():
    try:
        with open(VOCAB_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save_vocab(vocab):
    os.makedirs(os.path.dirname(VOCAB_FILE), exist_ok=True)
    data = dict(vocab or {})
    data["cachedAt"] = _iso(datetime.datetime.now())
    tmp = VOCAB_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, VOCAB_FILE)


def vocab_stale(now=None):
    v = load_vocab()
    at = _parse((v or {}).get("cachedAt"))
    return at is None or (now or datetime.datetime.now()) - at > datetime.timedelta(days=VOCAB_MAX_AGE_DAYS)


# ---------------------------------------------------------------------------
# Small pure helpers
# ---------------------------------------------------------------------------

def search_names(book):
    """The raw title in both scripts: NU stores whichever form its submitter
    typed and its search doesn't convert. source_title is stored simplified
    (t2s) for Chinese books; anything else is searched as-is."""
    raw = (book.get("source_title") or "").strip()
    if not raw:
        return []
    names = [raw]
    if (book.get("source_language") or "zh").lower() == "zh":
        import trad_simp
        for form in (trad_simp.convert_text(raw), trad_simp.to_traditional(raw)):
            if form and form not in names:
                names.append(form)
    return names


def slug_from_url(url):
    m = re.search(r"/series/([^/?#]+)", url or "")
    return m.group(1) if m else (url or "").strip().strip("/") or None


def norm_title(s):
    """Compare titles ignoring case, spaces, punctuation and width."""
    s = unicodedata.normalize("NFKC", s or "").casefold()
    return "".join(ch for ch in s if ch.isalnum())


def site_base():
    try:
        import sitemap
        return sitemap.resolve_base_url()
    except Exception:
        return (os.environ.get("SITE_BASE_URL") or "").rstrip("/") or None


def chapter_link(book_id, chapter, base=None):
    base = base or site_base()
    return f"{base}/library/read/{book_id}/{chapter}" if base else None


def release_label(chapter):
    return f"c{chapter}"


def release_date(published_at, today=None):
    """NU's date field (MM/DD/YYYY) — only for a release made before today."""
    pub = _parse(published_at)
    today = today or datetime.date.today()
    if pub and pub.date() < today:
        return pub.strftime("%m/%d/%Y")
    return None


LANGUAGES = {"zh": "Chinese", "ko": "Korean", "kr": "Korean", "ja": "Japanese",
             "jp": "Japanese", "ru": "Russian"}


def _names(vocab, key):
    return [x.get("name") for x in (vocab or {}).get(key) or [] if x.get("name")]


def build_series_draft(book, vocab=None, cover_url=None):
    """A first draft of the add-series form from what the book already holds.
    Book tags are matched case-insensitively against NU's genre and tag
    names; what doesn't match is listed for the human, never guessed."""
    tags = [t for t in (book.get("tags") or []) if t]
    genre_by_lower = {n.lower(): n for n in _names(vocab, "genres")}
    tag_by_lower = {n.lower(): n for n in _names(vocab, "tags")}
    genres, nu_tags, unmatched = [], [], []
    for t in tags:
        key = t.lower()
        if key in genre_by_lower:
            genres.append(genre_by_lower[key])
        elif key in tag_by_lower:
            nu_tags.append(tag_by_lower[key])
        elif key not in ("editor's choice",):
            unmatched.append(t)
    lang = LANGUAGES.get((book.get("source_language") or "zh").lower())
    if lang and vocab and lang not in _names(vocab, "languages"):
        lang = None
    status_coo = None
    if book.get("total_source_chapters"):
        word = "Completed" if book.get("status") == "completed" else "Ongoing"
        status_coo = [f"{book['total_source_chapters']} Chapters ({word})"]
    return {
        "title": book.get("nu_title") or book.get("title") or "",
        "associatedNames": search_names(book),
        "description": book.get("description") or "",
        "type": "Web Novel",
        "genres": genres,
        "tags": nu_tags,
        "language": lang,
        "authors": [book["author"]] if book.get("author") else [],
        "statusInCoo": status_coo,
        "coverUrl": cover_url,
        "unmatchedTags": unmatched,
    }


# Fields of a draft that are sent to POST /nu/series.
SERIES_FIELDS = ("title", "associatedNames", "description", "type", "genres", "tags", "language",
                 "authors", "artists", "year", "statusInCoo", "licensed", "completelyTranslated",
                 "originalPublishers", "englishPublishers", "coverUrl")


def series_body(draft):
    body = {k: draft.get(k) for k in SERIES_FIELDS}
    return {k: v for k, v in body.items() if v not in (None, "", [])}


def draft_fingerprint(draft):
    """What a dry run vouches for: the exact body that would be submitted."""
    blob = json.dumps(series_body(draft or {}), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def cover_url_for(db, book):
    try:
        from web.services import media_urls
        full = media_urls.cover_urls(db, book)[0]
        if full:
            return full
    except Exception:
        pass
    base = site_base()
    if book.get("cover_image") and base:
        return f"{base}/api/public/books/{book['id']}/cover"
    return None


# ---------------------------------------------------------------------------
# Human actions (shared by the admin API and the tests)
# ---------------------------------------------------------------------------

class NuActionError(ValueError):
    """A human action that doesn't apply in the book's current state."""


def _book(db, book_id):
    book = db.get_book(book_id=book_id)
    if not book:
        raise NuActionError("book not found")
    if book.get("is_original"):
        raise NuActionError("original works aren't posted to NovelUpdates")
    return book


def request_search(db, book_id):
    """Queue a search (the planner creates the task). Only before a series is
    settled — a book already linked or further along keeps its slug."""
    book = _book(db, book_id)
    state = db.get_book_nu(book_id)
    if state["state"] not in ("untracked", "searching", "needs_review", "new_series", "linked"):
        raise NuActionError(f"the book is {state['state']}; it doesn't need a search")
    names = search_names(book)
    if not names:
        db.update_book_nu(book_id, state="needs_review", candidates=None, search_names=None,
                          last_error="no original title — set it on the book, or pick the series by hand")
        return db.get_book_nu(book_id)
    db.update_book_nu(book_id, state="searching", search_names=names, candidates=None,
                      last_error=None)
    return db.get_book_nu(book_id)


def set_mode(db, book_id, mode, start_chapter=None):
    if mode not in MODES:
        raise NuActionError(f"mode must be one of {', '.join(MODES)}")
    _book(db, book_id)
    state = db.get_book_nu(book_id)
    fields = {"mode": mode}
    if start_chapter is not None:
        if int(start_chapter) < 1:
            raise NuActionError("start chapter must be 1 or more")
        fields["start_chapter"] = int(start_chapter)
    if mode == "auto" and fields.get("start_chapter", state.get("start_chapter")) is None:
        raise NuActionError("set the chapter to start posting from first")
    db.update_book_nu(book_id, **fields)
    return db.get_book_nu(book_id)


def link_series(db, book_id, url_or_slug, title=None, series_id=None):
    """A human picked the series (from the candidates, or by hand). It becomes
    `linked`; the next group sync promotes it to active if it's already ours."""
    book = _book(db, book_id)
    slug = slug_from_url(url_or_slug)
    if not slug:
        raise NuActionError("not a NovelUpdates series URL or slug")
    url = url_or_slug if str(url_or_slug).startswith("http") \
        else f"https://www.novelupdates.com/series/{slug}/"
    state = db.get_book_nu(book_id)
    new_state = state["state"] if state["state"] in ("first_release_sent", "active") else "linked"
    db.update_book_nu(book_id, state=new_state, nu_slug=slug, nu_url=url,
                      nu_series_id=series_id or state.get("nu_series_id"), candidates=None,
                      hold_reason=None if state.get("hold_reason") == "bad_slug" else state.get("hold_reason"))
    if title and not book.get("nu_title"):
        db.update_book(book_id, nu_title=title)
    if not db.open_nu_task(None, "sync"):
        db.create_nu_task(None, "sync", priority=PRIORITY_SYNC)
    return db.get_book_nu(book_id)


def declare_new_series(db, book_id):
    book = _book(db, book_id)
    state = db.get_book_nu(book_id)
    if state["state"] not in ("untracked", "searching", "needs_review", "new_series"):
        raise NuActionError(f"the book is {state['state']}, not a new series")
    draft = state.get("series_draft") or build_series_draft(book, load_vocab(), cover_url_for(db, book))
    db.update_book_nu(book_id, state="new_series", series_draft=draft, candidates=None)
    return db.get_book_nu(book_id)


def save_draft(db, book_id, draft):
    state = db.get_book_nu(book_id)
    if state["state"] != "new_series":
        raise NuActionError("only a new series has a draft")
    clean = {k: draft.get(k) for k in SERIES_FIELDS + ("unmatchedTags",) if k in draft}
    db.update_book_nu(book_id, series_draft=clean)
    return db.get_book_nu(book_id)


def queue_series(db, book_id, dry_run):
    """Dry-run or submit the draft. Submitting needs a successful dry run of
    this exact draft — the human approval gate the doc asks for."""
    state = db.get_book_nu(book_id)
    if state["state"] != "new_series":
        raise NuActionError("only a new series can be submitted")
    draft = state.get("series_draft") or {}
    missing = [k for k in ("title", "associatedNames", "genres") if not draft.get(k)]
    if missing:
        raise NuActionError(f"the draft needs {', '.join(missing)}")
    fp = draft_fingerprint(draft)
    if not dry_run and (state.get("draft_dry_run") or {}).get("fingerprint") != fp:
        raise NuActionError("dry-run this draft first (it changed since the last dry run)")
    if db.open_nu_task(book_id, "series"):
        raise NuActionError("a series job for this book is already queued")
    return db.create_nu_task(book_id, "series", priority=PRIORITY_SERIES,
                             options={"dry_run": bool(dry_run), "fingerprint": fp})


def record_first_release(db, book_id, chapter, label=None):
    """The first release of a brand-new series, posted by hand on NU (the
    scraper can't post to a pending series yet — open issue 2)."""
    state = db.get_book_nu(book_id)
    if state["state"] not in ("series_submitted", "linked"):
        raise NuActionError(f"the book is {state['state']}")
    now = _iso(datetime.datetime.now())
    label = label or release_label(chapter)
    db.upsert_nu_release(book_id, int(chapter), label=label, status="manual",
                         submitted_at=now, message="posted by hand",
                         link=chapter_link(book_id, chapter))
    db.update_book_nu(book_id, state="first_release_sent", first_release_label=label,
                      first_release_at=now, last_release_label=label, last_release_at=now)
    return db.get_book_nu(book_id)


def resolve_release(db, book_id, chapter, action):
    """A release whose submit may or may not have gone through: a human looked
    at NU and says it's there (`sent`) or isn't (`resend`, posted with force)."""
    row = db.get_nu_release(book_id, chapter)
    if not row or row["status"] not in ("check", "failed"):
        raise NuActionError("that release doesn't need resolving")
    state = db.get_book_nu(book_id)
    if action == "sent":
        now = _iso(datetime.datetime.now())
        db.upsert_nu_release(book_id, chapter, status="sent", submitted_at=now,
                             message="confirmed on NovelUpdates by hand", error=None)
        fields = {"last_release_label": row["label"], "last_release_at": now}
        if state["state"] == "linked":
            fields.update(state="first_release_sent", first_release_label=row["label"],
                          first_release_at=now)
    elif action == "resend":
        db.upsert_nu_release(book_id, chapter, status="queued", error=None)
        if not db.open_nu_task(book_id, "release"):
            db.create_nu_task(book_id, "release", priority=PRIORITY_RELEASE,
                              options={"chapter": chapter, "force": True})
        fields = {}
    else:
        raise NuActionError("action must be 'sent' or 'resend'")
    if state.get("hold_reason") in ("check_nu", "refused", "failing"):
        fields.update(hold_reason=None, hold_detail=None, alerted_at=None, consecutive_failures=0)
    db.update_book_nu(book_id, **fields)
    return db.get_book_nu(book_id)


def names_to_add(book):
    """What we add to a series' Associated Names on NU: our English title (so
    NU's bot can match our feed items, whose titles carry it) and the raw title
    in both scripts (so a search by either finds the series). NU's side merges
    and ignores names it already has."""
    names = []
    for n in [book.get("title")] + search_names(book):
        n = (n or "").strip()
        if n and n.casefold() not in {x.casefold() for x in names}:
            names.append(n)
    return names


def queue_names(db, book_id):
    """Add our names to the series' Associated Names now (any state with a slug)."""
    book = _book(db, book_id)
    state = db.get_book_nu(book_id)
    if not state.get("nu_slug"):
        raise NuActionError("the book has no NU series page yet")
    # NU's own series title isn't repeated as an associated name (the scraper
    # compares against the names list only, so it would add it).
    nu_title = norm_title(book.get("nu_title"))
    names = [n for n in names_to_add(book) if norm_title(n) != nu_title]
    if not names:
        raise NuActionError("no names to add")
    existing = db.open_nu_task(book_id, "names")
    if existing:
        return existing["id"]
    return db.create_nu_task(book_id, "names", priority=PRIORITY_NAMES, options={"add": names})


def queue_release_dry_run(db, book_id, chapter):
    """Fill NU's add-release form for one chapter without submitting — to see
    exactly what would be posted. Touches no release record."""
    _book(db, book_id)
    state = db.get_book_nu(book_id)
    if not state.get("nu_slug"):
        raise NuActionError("the book has no NU series yet")
    if not db.chapter_published_at(book_id, chapter):
        raise NuActionError(f"chapter {chapter} isn't published")
    return db.create_nu_task(book_id, "release", priority=PRIORITY_RELEASE,
                             options={"chapter": int(chapter), "dry_run": True})


# NU has confirmed the series isn't ours: we've never had a release approved on it.
NOT_OURS_STATES = ("linked", "new_series", "series_submitted", "first_release_sent")


def suggested_start(db, book_id, state):
    """The "post from" chapter the UI prefills. A series NU confirms isn't ours
    yet starts at 1 — its first release opens it on NU. Anything else (ours,
    or not looked up yet) starts at the next chapter to be published, so a
    hand-posted history is never re-posted."""
    if state in NOT_OURS_STATES:
        return 1
    return db.first_unpublished_chapter(book_id)


def pending_title(db, book_id, state=None):
    """The English title a series was submitted under — what the add-release
    form's Series box knows it by until NU approves it."""
    state = state or db.get_book_nu(book_id)
    return ((state.get("series_draft") or {}).get("title")
            or (db.get_book(book_id=book_id) or {}).get("nu_title") or "").strip() or None


def request_release_search(db, book_id):
    """Look a submitted series up on the add-release form, for its NU id."""
    state = db.get_book_nu(book_id)
    if state["state"] != "series_submitted":
        raise NuActionError("only a submitted series is looked up this way")
    title = pending_title(db, book_id, state)
    if not title:
        raise NuActionError("no submitted title to look up")
    if not db.open_nu_task(book_id, "release_search"):
        db.create_nu_task(book_id, "release_search", priority=PRIORITY_RELEASE_SEARCH,
                          options={"title": title, "tries": 1})
    return db.get_book_nu(book_id)


def set_pending_series_id(db, book_id, series_id):
    """A human picked the pending series' id (the lookup was ambiguous)."""
    state = db.get_book_nu(book_id)
    if state["state"] != "series_submitted":
        raise NuActionError("only a submitted series takes a pending id")
    if not str(series_id or "").strip().isdigit():
        raise NuActionError("a NovelUpdates series id is a number")
    db.update_book_nu(book_id, nu_series_id=str(series_id).strip(), candidates=None, last_error=None,
                      **({"hold_reason": None, "hold_detail": None, "alerted_at": None}
                         if state.get("hold_reason") == "bad_slug" else {}))
    return db.get_book_nu(book_id)


def post_first_release(db, book_id, chapter=None):
    """Post a submitted series' first chapter now, whatever the book's mode —
    NU won't approve the series without it. Goes through the lane like any
    release (holds, "check NU", alerts)."""
    state = db.get_book_nu(book_id)
    if state["state"] != "series_submitted":
        raise NuActionError(f"the book is {state['state']}")
    if not state.get("nu_series_id"):
        raise NuActionError("the series' NU id isn't known yet — look it up first")
    chapter = int(chapter or state.get("start_chapter") or 1)
    if not db.chapter_published_at(book_id, chapter):
        raise NuActionError(f"chapter {chapter} isn't published")
    if db.open_nu_task(book_id, "release"):
        raise NuActionError("a release for this book is already queued")
    return db.create_nu_task(book_id, "release", priority=PRIORITY_RELEASE,
                             options={"chapter": chapter, "first": True})


def clear_book_hold(db, book_id):
    db.update_book_nu(book_id, hold_reason=None, hold_detail=None, alerted_at=None,
                      consecutive_failures=0)
    return db.get_book_nu(book_id)


def find_all(db):
    """Bulk, read-only: a group sync, then a search for every public translated
    book not yet looked at. Posts nothing and changes no mode."""
    if not db.open_nu_task(None, "sync"):
        db.create_nu_task(None, "sync", priority=PRIORITY_SYNC)
    queued, skipped = [], []
    for r in db.list_book_nu(states=("untracked",)):
        if not r.get("is_public"):
            continue
        try:
            st = request_search(db, r["book_id"])
        except NuActionError:
            continue
        (queued if st["state"] == "searching" else skipped).append(r["book_id"])
    return {"searching": queued, "no_title": skipped}


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------

class NuEngine:
    def __init__(self, db, client_factory=make_client, logger=None, clock=None,
                 alerter=None, plan_seconds=PLAN_SECONDS):
        self.db = db
        self.client_factory = client_factory
        self.logger = logger
        self.clock = clock or datetime.datetime.now
        self.alerter = alerter
        self.plan_seconds = plan_seconds
        self._planned_at = None
        self._site_feed = None

    # -- helpers -------------------------------------------------------------

    def _now(self):
        return _iso(self.clock())

    def _later(self, **kw):
        return _iso(self.clock() + datetime.timedelta(**kw))

    def _log(self, level, msg):
        if self.logger:
            getattr(self.logger, level)(msg)

    def _activity(self, book_id, message, type_="nu", link=None):
        title = None
        if book_id is not None:
            try:
                title = (self.db.get_book(book_id=book_id) or {}).get("title")
            except Exception:
                pass
        who = f" ({title or f'book {book_id}'})" if book_id is not None else ""
        entities = [{"label": "NovelUpdates", "href": link}] if link else None
        try:
            self.db.add_activity_log(type_, f"NovelUpdates{who}: {message}", book_id=book_id,
                                     book_name=title, entities=entities)
        except Exception as e:
            self._log("warning", f"activity log failed: {e}")
        self._log("info", f"[nu book {book_id}] {message}")

    def _alert(self, book_id, subject, message, link=None):
        """Activity line (red) + email + ntfy."""
        self._activity(book_id, message, type_="nu_error", link=link)
        title = None
        if book_id is not None:
            title = (self.db.get_book(book_id=book_id) or {}).get("title")
        text = f"{title}: {message}" if title else message
        try:
            if self.alerter is not None:
                self.alerter(subject, text, link)
            else:
                import nu_alerts
                nu_alerts.alert(self.db, subject, text, link)
        except Exception as e:
            self._log("warning", f"NU alert failed: {e}")

    def _hold(self, book_id, reason, detail, link=None):
        """Stop posting for one book and tell a human — once per hold."""
        state = self.db.get_book_nu(book_id)
        if state.get("hold_reason") == reason and state.get("alerted_at"):
            return
        self.db.update_book_nu(book_id, hold_reason=reason, hold_detail=detail,
                               alerted_at=self._now(), last_error=detail)
        self._alert(book_id, reason.replace("_", " "), detail, link or state.get("nu_url"))

    # -- tick ----------------------------------------------------------------

    def tick(self):
        if not setting("nu_enabled"):
            return "disabled"
        if load_hold():
            return "held"
        lane = self.db.nu_lane_task()
        if lane is None:
            now = self.clock()
            if self._planned_at is None or (now - self._planned_at).total_seconds() >= self.plan_seconds:
                self._planned_at = now
                self.plan()
        state = self.step_lane()
        if lane is not None and self.db.nu_lane_task() is None and not load_hold():
            # A job just ended: line up and submit the next one now, rather
            # than on the next planning round — posting a backlog is then
            # paced by the scraper and nu_release_spacing_seconds alone.
            self._planned_at = self.clock()
            self.plan()
            state = self.step_lane()
        return state

    def seconds_until_due(self):
        """Seconds until the next queued NU task may be submitted (0 = now),
        or None when nothing is queued. The worker sleeps no longer than this,
        so a short release spacing isn't stretched to its poll interval."""
        rows = self.db.list_nu_tasks(statuses=("queued",), order="t.not_before", limit=1)
        if not rows:
            return None
        due = _parse(rows[0].get("not_before"))
        if due is None:
            return 0
        return max(0.0, (due - self.clock()).total_seconds())

    def plan(self):
        now = self.clock()
        spacing = int(setting("nu_release_spacing_seconds") or 0)
        wait = datetime.timedelta(hours=float(setting("nu_approval_wait_hours")))
        timeout = datetime.timedelta(hours=float(setting("nu_approval_timeout_hours")))
        base = None
        self._site_feed = None
        need_sync = need_vocab = False
        for r in self.db.list_book_nu():
            book_id, state = r["book_id"], r["state"]
            if state == "searching":
                if not self.db.open_nu_task(book_id, "search"):
                    self.db.create_nu_task(book_id, "search", priority=PRIORITY_SEARCH,
                                           options={"names": r.get("search_names") or []})
                continue
            if state == "new_series" and vocab_stale(now):
                need_vocab = True
            if state == "first_release_sent":
                first = _parse(r.get("first_release_at"))
                if first and now - first >= wait:
                    need_sync = True
                if first and now - first >= timeout and r.get("hold_reason") != "approval_timeout":
                    self._hold(book_id, "approval_timeout",
                               f"{r.get('first_release_label') or 'the first release'} was posted "
                               f"{int((now - first).total_seconds() // 3600)} h ago and the series "
                               "still isn't on our group page — NU may have rejected it")
                continue
            if r["mode"] != "auto" or r.get("hold_reason") \
                    or state not in ("linked", "active", "series_submitted"):
                continue
            if r.get("start_chapter") is None:
                continue
            # A submitted series has no slug until NU approves it; its first
            # chapter goes by the id the add-release form gave us.
            if not (r.get("nu_slug") or (state == "series_submitted" and r.get("nu_series_id"))):
                continue
            if not r.get("is_public"):
                self._hold(book_id, "not_public", "the book is private, so its chapter links "
                                                  "would 404 — make it public, then clear the hold")
                continue
            if base is None:
                base = site_base()
                if not base:
                    set_hold("SITE_BASE_URL is not set, so there are no chapter links to post")
                    self._alert(None, "lane held", "SITE_BASE_URL is not set; NU posting is held")
                    return
            if self.db.open_nu_task(book_id, "release"):
                continue
            if state == "linked":
                # Someone else created this series: add our names before our first
                # release. Once tried (done or given up), it never blocks posting.
                pending = self.db.open_nu_task(book_id, "names")
                if pending and not pending.get("attempts"):
                    continue   # its first try is due; a retrying one doesn't hold the release
                if not self.db.list_nu_tasks(book_id=book_id, kinds=("names",), limit=1):
                    try:
                        queue_names(self.db, book_id)
                        continue
                    except NuActionError:
                        pass
            nxt = self.db.published_chapters_from(book_id, int(r["start_chapter"]), limit=1)
            if not nxt:
                continue
            chapter = nxt[0]["chapter"]
            backlog = not self._in_feed(book_id, chapter)
            if backlog:
                until = self._backlog_until(book_id, now)
                if until is not None:
                    if r.get("rate_limited_until") != _iso(until):
                        if not r.get("rate_limited_until"):
                            self._activity(book_id, f"rate-limited: c{chapter} is an old chapter, which NU "
                                                    "queues for manual approval; the backlog "
                                                    f"resumes at {until:%H:%M}")
                        self.db.update_book_nu(book_id, rate_limited_until=_iso(until))
                    continue
            if r.get("rate_limited_until"):
                self.db.update_book_nu(book_id, rate_limited_until=None)
            last = _parse(r.get("last_release_at"))
            not_before = None
            if state == "active" and last and spacing:
                due = last + datetime.timedelta(seconds=spacing)
                if due > now:
                    not_before = _iso(due)
            self.db.create_nu_task(book_id, "release", priority=PRIORITY_RELEASE,
                                   not_before=not_before,
                                   options={"chapter": chapter, "backlog": backlog,
                                            "first": state in ("linked", "series_submitted")})
        if need_sync and not self.db.open_nu_task(None, "sync"):
            last = self.db.last_finished_nu_task("sync")
            last_at = _parse((last or {}).get("finished_at"))
            every = datetime.timedelta(hours=float(setting("nu_group_sync_hours")))
            if last_at is None or now - last_at >= every:
                self.db.create_nu_task(None, "sync", priority=PRIORITY_SYNC)
        if need_vocab and not self.db.open_nu_task(None, "vocab"):
            self.db.create_nu_task(None, "vocab", priority=PRIORITY_VOCAB)

    def _in_feed(self, book_id, chapter):
        """Will NU find this chapter in our RSS feed (and so approve it on its
        own)? NU reads the book's feed; the site feed is checked as well only
        when nu_feed_window_site is set."""
        site_n = int(setting("nu_feed_window_site") or 0)
        book_n = int(setting("nu_feed_window_book") or 0)
        if book_n <= 0:
            return False
        key = (book_id, chapter)
        if key not in self.db.feed_keys(book_n, book_id=book_id):
            return False
        if site_n > 0:
            if self._site_feed is None:
                self._site_feed = self.db.feed_keys(site_n)
            return key in self._site_feed
        return True

    def _backlog_until(self, book_id, now):
        """None when another backlog release may go now, else when the oldest
        one in the window ages out (per book, or across books for the global
        cap — whichever binds later)."""
        window = datetime.timedelta(hours=float(setting("nu_backlog_window_hours") or 0))
        since = _iso(now - window)
        until = None
        for limit, scope in ((int(setting("nu_backlog_limit") or 0), book_id),
                             (int(setting("nu_backlog_limit_all") or 0), None)):
            if limit <= 0:
                continue
            stamps = self.db.backlog_releases_since(since, book_id=scope)
            # Backlog releases queued but not yet posting count too, or one
            # planning pass could let every book's next release under the cap.
            stamps += [None for t in self.db.list_nu_tasks(statuses=("queued",), kinds=("release",),
                                                           book_id=scope, limit=1000)
                       if (t.get("options") or {}).get("backlog")]
            if len(stamps) < limit:
                continue
            posted = sorted(_parse(t) or now for t in stamps)
            frees = posted[len(posted) - limit] + window
            until = frees if until is None else max(until, frees)
        return until

    # -- the lane ------------------------------------------------------------

    def step_lane(self):
        task = self.db.nu_lane_task()
        if task is None:
            task = self.db.next_nu_task(self._now())
            if task is None:
                return "idle"
            self.submit(task)
            return "busy"
        self.poll(task)
        return "busy"

    def _body(self, task):
        """The request for a task, or None when it no longer applies."""
        kind, opts, book_id = task["kind"], task.get("options") or {}, task.get("book_id")
        if kind == "search":
            return {"names": opts.get("names") or []}
        if kind == "release":
            state = self.db.get_book_nu(book_id)
            chapter = opts["chapter"]
            pub = self.db.chapter_published_at(book_id, chapter)
            link = chapter_link(book_id, chapter)
            if state.get("nu_slug"):
                target = {"series": state.get("nu_url") or state["nu_slug"]}
            elif state.get("nu_series_id") and pending_title(self.db, book_id, state):
                # Awaiting approval: no page yet. The scraper types the title into
                # the Series box and only submits if it offers this exact id.
                target = {"seriesId": state["nu_series_id"],
                          "seriesTitle": pending_title(self.db, book_id, state)}
            else:
                return None
            if not pub or not link:
                return None
            if not opts.get("dry_run"):
                self.db.upsert_nu_release(book_id, chapter, label=release_label(chapter), link=link,
                                          status="posting", nu_task_id=task["id"], error=None,
                                          backlog=1 if opts.get("backlog") else 0)
            return {**target,
                    "release": release_label(chapter), "link": link,
                    "date": release_date(pub, self.clock().date()),
                    "dryRun": True if opts.get("dry_run") else None,
                    "force": True if opts.get("force") else None}
        if kind == "release_search":
            return {"title": opts.get("title")}
        if kind == "names":
            state = self.db.get_book_nu(book_id)
            if not state.get("nu_slug"):
                return None
            return {"series": state.get("nu_url") or state["nu_slug"], "add": opts.get("add") or []}
        if kind == "series":
            draft = self.db.get_book_nu(book_id).get("series_draft") or {}
            body = series_body(draft)
            if opts.get("dry_run"):
                body["dryRun"] = True
            return body
        return {}

    def submit(self, task):
        try:
            body = self._body(task)
        except Exception as e:
            self.db.update_nu_task(task["id"], status="failed", last_error=str(e), finished_at=self._now())
            return
        if body is None:
            self.db.update_nu_task(task["id"], status="skipped", finished_at=self._now(),
                                   last_error="no longer applies (chapter unpublished or series unlinked)")
            return
        sc = self.client_factory()
        try:
            job = submit_nu(sc, KIND_PATHS[task["kind"]], body)
        except ScraperError as e:
            self._on_error(task, str(e), permanent=True)
            return
        except (urllib.error.URLError, OSError) as e:
            self.db.update_nu_task(task["id"], last_error=f"scraper unreachable: {e}",
                                   not_before=self._later(minutes=UNREACHABLE_RETRY_MINUTES))
            self._release_back_to_queue(task)
            return
        self.db.update_nu_task(task["id"], status="running", scraper_job_id=str(job["id"]),
                               started_at=task.get("started_at") or self._now(),
                               last_error=None, vnc_url=None)

    def poll(self, task):
        job_id = task.get("scraper_job_id")
        if not job_id:
            self.submit(task)
            return
        sc = self.client_factory()
        try:
            if task["status"] == "cancelling":
                try:
                    sc.cancel(job_id)
                except ScraperError:
                    pass
            job = sc.status(job_id)
        except ScraperError as e:
            self._on_error(task, f"scraper lost job {job_id}: {e}")
            return
        except (urllib.error.URLError, OSError):
            return
        status = job.get("status")
        if status in ("done", "failed", "cancelled"):
            self.finish(task, job, sc)
            return
        fields = {"progress": job.get("progress") or {}}
        if status == "blocked":
            import scrape_sync
            fields["vnc_url"] = scrape_sync.vnc_url(job.get("vncUrl"))
        if task["status"] != "cancelling":
            fields["status"] = "blocked" if status == "blocked" else "running"
        self.db.update_nu_task(task["id"], **fields)

    def finish(self, task, job, sc):
        status = job.get("status")
        if status == "cancelled" or task["status"] == "cancelling":
            self.db.update_nu_task(task["id"], status="cancelled", finished_at=self._now(), vnc_url=None)
            self._release_back_to_queue(task)
            return
        if status != "done":
            self._on_error(task, job.get("error") or f"{task['kind']} job failed")
            return
        try:
            result = sc.result(job["id"]) or {}
        except (ScraperError, urllib.error.URLError, OSError):
            return  # not readable yet; next tick
        handler = getattr(self, f"on_{task['kind']}", None)
        if handler:
            handler(task, result)
        self.db.update_nu_task(task["id"], status="done", finished_at=self._now(), result=result,
                               vnc_url=None, last_error=None)

    def _release_back_to_queue(self, task):
        opts = task.get("options") or {}
        chapter = opts.get("chapter")
        if task["kind"] == "release" and chapter is not None and not opts.get("dry_run"):
            row = self.db.get_nu_release(task["book_id"], chapter)
            if row and row["status"] == "posting":
                self.db.upsert_nu_release(task["book_id"], chapter, status="queued")

    def _on_error(self, task, error, permanent=False):
        """Classify a failed job (novelupdates-t9.md, "Errors") and act on it."""
        low = (error or "").lower()
        kind, book_id = task["kind"], task.get("book_id")
        chapter = (task.get("options") or {}).get("chapter")
        fail = {"status": "failed", "finished_at": self._now(), "last_error": error, "vnc_url": None}

        if any(s in low for s in ERR_GLOBAL):
            self.db.update_nu_task(task["id"], **fail)
            self._release_back_to_queue(task)
            set_hold(error)
            self._alert(None, "scraper login/config", f"NU posting is held: {error}")
            return
        if kind == "release" and any(s in low for s in ERR_CHECK):
            self.db.update_nu_task(task["id"], **fail)
            self.db.upsert_nu_release(book_id, chapter, status="check", error=error)
            self._hold(book_id, "check_nu", f"{release_label(chapter)} may or may not have reached NU "
                                            f"({error}). Check NU, then mark it sent or resend it.")
            return
        if kind == "series" and any(s in low for s in ERR_CHECK):
            self.db.update_nu_task(task["id"], **fail)
            self.db.update_book_nu(book_id, state="series_submitted", last_error=error)
            self._alert(book_id, "check NU", f"the new-series submit may have gone through ({error}). "
                                             "Check NU before doing anything else.")
            return
        if any(s in low for s in ERR_REFUSED):
            self.db.update_nu_task(task["id"], **fail)
            if kind == "release":
                self.db.upsert_nu_release(book_id, chapter, status="failed", error=error)
                self._hold(book_id, "refused", f"NU refused {release_label(chapter)}: {error}")
            else:
                self.db.update_book_nu(book_id, last_error=error)
                self._alert(book_id, "refused", f"NU refused the {kind}: {error}")
            return
        if kind == "release" and "didn't offer id" in low \
                and not self.db.get_book_nu(book_id).get("nu_slug"):
            self.db.update_nu_task(task["id"], **fail)
            self.db.upsert_nu_release(book_id, chapter, status="failed", error=error)
            self._hold(book_id, "bad_slug", "NU's add-release form didn't offer the stored id for the "
                                            f"submitted title ({error}). Look the series up again, "
                                            "then clear the hold.")
            return
        if kind == "release" and any(s in low for s in ERR_BAD_SLUG):
            self.db.update_nu_task(task["id"], **fail)
            self.db.upsert_nu_release(book_id, chapter, status="failed", error=error)
            self._hold(book_id, "bad_slug", f"the stored NU series is wrong: {error}")
            return
        if kind == "names" and ("didn't save as sent" in low or "no answer" in low):
            self.db.update_nu_task(task["id"], **fail)
            self._alert(book_id, "check associated names", f"editing the series' associated names "
                        f"may not have saved as sent ({error}); look at the series page")
            return
        if kind == "series" and any(s in low for s in ERR_UNKNOWN_NAMES):
            # Fails before anything is sent: fix the draft, no alert.
            self.db.update_nu_task(task["id"], **fail)
            self.db.update_book_nu(book_id, last_error=error)
            return

        attempts = (task.get("attempts") or 0) + 1
        if not permanent and attempts <= len(RETRY_BACKOFF_MINUTES):
            self.db.update_nu_task(task["id"], status="queued", attempts=attempts, last_error=error,
                                   scraper_job_id=None, vnc_url=None,
                                   not_before=self._later(minutes=RETRY_BACKOFF_MINUTES[attempts - 1]))
            self._release_back_to_queue(task)
            self._log("warning", f"nu task {task['id']} ({kind}) failed, retry {attempts}: {error}")
            return
        self.db.update_nu_task(task["id"], attempts=attempts, **fail)
        if kind == "release":
            self.db.upsert_nu_release(book_id, chapter, status="failed", error=error)
            self._hold(book_id, "failing", f"{release_label(chapter)} keeps failing: {error}")
        elif kind == "search":
            self.db.update_book_nu(book_id, state="needs_review", last_error=f"search failed: {error}")
            self._activity(book_id, f"search failed: {error}", type_="nu_error")
        elif book_id is not None:
            self.db.update_book_nu(book_id, last_error=error)
            self._activity(book_id, f"{kind} failed: {error}", type_="nu_error")
        else:
            self._activity(None, f"{kind} failed: {error}", type_="nu_error")

    # -- results -------------------------------------------------------------

    def on_search(self, task, res):
        book_id = task["book_id"]
        book = self.db.get_book(book_id=book_id) or {}
        fields = {"checked_at": self._now(), "last_error": None}
        series = res.get("series") or {}
        if res.get("found") and series.get("slug"):
            fields.update(nu_slug=series["slug"], nu_url=series.get("url"),
                          nu_series_id=series.get("id"), candidates=None)
            if series.get("title") and not book.get("nu_title"):
                self.db.update_book(book_id, nu_title=series["title"])
            if res.get("inOurGroup"):
                fields.update(state="active", active_since=self._now())
                msg = f"found “{series.get('title')}”, already in our group"
            else:
                fields["state"] = "linked"
                msg = f"found “{series.get('title')}” (we haven't posted to it yet)"
            self.db.update_book_nu(book_id, **fields)
            self._activity(book_id, msg, link=series.get("url"))
        elif res.get("candidates"):
            self.db.update_book_nu(book_id, state="needs_review", candidates=res["candidates"], **fields)
            self._activity(book_id, f"no exact match; {len(res['candidates'])} candidate(s) to review",
                           type_="nu_error")
        else:
            draft = build_series_draft(book, load_vocab(), cover_url_for(self.db, book))
            self.db.update_book_nu(book_id, state="new_series", candidates=None,
                                   series_draft=draft, **fields)
            self._activity(book_id, "not on NovelUpdates — a new-series draft is waiting for review")

    def on_release(self, task, res):
        book_id = task["book_id"]
        opts = task.get("options") or {}
        chapter = opts["chapter"]
        status = res.get("status")
        if status == "dry_run":
            return  # the form and screenshot are on the task's result
        label = release_label(chapter)
        now = self._now()
        row_status = "exists" if status == "exists" else "sent"
        self.db.upsert_nu_release(book_id, chapter, status=row_status, submitted_at=now,
                                  message=res.get("message") or status, error=None)
        state = self.db.get_book_nu(book_id)
        fields = {"last_release_label": label, "last_release_at": now, "consecutive_failures": 0,
                  "last_error": None}
        if state["state"] in ("linked", "series_submitted"):
            fields.update(state="first_release_sent", first_release_label=label, first_release_at=now)
            self._activity(book_id, f"first release {label} sent; waiting for NU to approve the series",
                           link=state.get("nu_url"))
        else:
            self._activity(book_id, f"{label} {'was already on NU' if status != 'submitted' else 'posted'}",
                           link=state.get("nu_url"))
        self.db.update_book_nu(book_id, **fields)

    def on_sync(self, task, res):
        listed = res.get("series") or []
        slugs = {s.get("slug") for s in listed}
        by_title = {norm_title(s.get("title")): s for s in listed if s.get("title")}
        for r in self.db.list_book_nu(states=("linked", "first_release_sent", "series_submitted")):
            book_id, slug = r["book_id"], r.get("nu_slug")
            hit = None
            if slug and slug in slugs:
                hit = next(s for s in listed if s.get("slug") == slug)
            elif not slug:
                draft = r.get("series_draft") or {}
                for t in (r.get("nu_title"), draft.get("title")):
                    if t and norm_title(t) in by_title:
                        hit = by_title[norm_title(t)]
                        break
            if not hit:
                continue
            fields = {"state": "active", "active_since": self._now(), "nu_slug": hit.get("slug"),
                      "nu_url": hit.get("url") or r.get("nu_url")}
            if r.get("hold_reason") == "approval_timeout":
                fields.update(hold_reason=None, hold_detail=None, alerted_at=None)
            self.db.update_book_nu(book_id, **fields)
            self._activity(book_id, "NU approved the series; posting new chapters from now on",
                           link=fields["nu_url"])

    def on_series(self, task, res):
        book_id = task["book_id"]
        opts = task.get("options") or {}
        status = res.get("status")
        if status == "dry_run":
            self.db.update_book_nu(book_id, draft_dry_run_at=self._now(), last_error=None,
                                   draft_dry_run={"fingerprint": opts.get("fingerprint"),
                                                  "form": res.get("form"),
                                                  "screenshot": res.get("screenshot")})
            return
        self.db.update_book_nu(book_id, state="series_submitted", last_error=None)
        title = pending_title(self.db, book_id)
        if title and not (self.db.get_book(book_id=book_id) or {}).get("nu_title"):
            self.db.update_book(book_id, nu_title=title)
        self._activity(book_id, "new series submitted; looking it up on NU's add-release form "
                                "so its first chapter can be posted")
        try:
            request_release_search(self.db, book_id)
        except NuActionError as e:
            self.db.update_book_nu(book_id, last_error=str(e))

    def on_release_search(self, task, res):
        book_id = task["book_id"]
        opts = task.get("options") or {}
        match = res.get("match") or {}
        hits = res.get("hits") or []
        if match.get("id") and not res.get("ambiguous"):
            self.db.update_book_nu(book_id, nu_series_id=str(match["id"]), candidates=None,
                                   last_error=None)
            mode = self.db.get_book_nu(book_id).get("mode")
            self._activity(book_id, f"found the pending series on NU (id {match['id']}); "
                           + ("posting its first chapter" if mode == "auto"
                              else "post its first chapter from the NovelUpdates modal"))
            return
        if res.get("ambiguous"):
            self.db.update_book_nu(book_id, candidates=[{"id": h.get("id"), "title": h.get("title")}
                                                        for h in hits], last_error=None)
            self._alert(book_id, "pick the series",
                        f"several series on NU are named “{opts.get('title')}” — pick ours in the "
                        "NovelUpdates modal so its first chapter can be posted")
            return
        tries = int(opts.get("tries") or 1)
        if tries < RELEASE_SEARCH_TRIES:
            self.db.create_nu_task(book_id, "release_search", priority=PRIORITY_RELEASE_SEARCH,
                                   not_before=self._later(minutes=RELEASE_SEARCH_RETRY_MINUTES),
                                   options={"title": opts.get("title"), "tries": tries + 1})
            return
        detail = (f"NU's add-release form doesn't list “{opts.get('title')}” after {tries} tries"
                  + (f" (near misses: {', '.join(h.get('title') or '' for h in hits[:4])})" if hits else ""))
        self.db.update_book_nu(book_id, last_error=detail,
                               candidates=[{"id": h.get("id"), "title": h.get("title")} for h in hits] or None)
        self._alert(book_id, "new series not found", detail + ". Look it up again later, pick it, "
                                                              "or post the first chapter by hand.")

    def on_names(self, task, res):
        if res.get("status") == "updated":
            added = [n for n in (res.get("after") or []) if n not in (res.get("before") or [])]
            self._activity(task["book_id"], "added to the series' associated names on NU: "
                           + (", ".join(added) or "(see the task)"),
                           link=self.db.get_book_nu(task["book_id"]).get("nu_url"))

    def on_vocab(self, task, res):
        save_vocab(res)
