"""JSON-backed settings store for runtime-mutable, non-secret configuration.

Settings live in `settings.json` at the project root. Secrets and infrastructure
config (API keys, DB credentials, signing secrets, etc.) stay in `.env`. On
startup, `load()` reads the JSON file and mirrors every value into `os.environ`
so existing `os.getenv()` callers keep working transparently without per-call
refactoring.

First-run migration: if `settings.json` does not yet exist, the store seeds it
from the current values in `os.environ` (which has just been populated by
`load_dotenv()`), so upgrading an existing deployment is a no-op for the user.
"""
import json
import os
import threading

_lock = threading.Lock()

# The scraper on this machine: the compose `scraper` service in the Docker
# image (T9_DOCKER is set there), else a local install's loopback port.
DEFAULT_SCRAPER_URL = ("http://scraper:8787" if os.environ.get("T9_DOCKER")
                       else "http://127.0.0.1:8787")

# Schema: setting_key -> (env_var_name, default, type)
# Each managed setting maps to a legacy env var (for the os.environ compat
# shim) and carries a typed default for first-run migration / forward compat.
SCHEMA = {
    "translation_model":       ("TRANSLATION_MODEL",       "oai:o3-mini",             str),
    "advice_model":            ("ADVICE_MODEL",            "oai:o3-mini",             str),
    "pronoun_repair_model":    ("PRONOUN_REPAIR_MODEL",    "claude:claude-haiku-4-5", str),
    "comment_automod_enabled": ("COMMENT_AUTOMOD_ENABLED", False,                     bool),
    "comment_automod_model":   ("COMMENT_AUTOMOD_MODEL",   "claude:claude-haiku-4-5", str),
    # Only read when comment_automod_model is "jev"/"jev:<model>": a Jev verdict
    # below this confidence leaves the comment pending as "unsure".
    "comment_automod_jev_confidence": ("COMMENT_AUTOMOD_JEV_CONFIDENCE", 0.9,        float),
    "unit_cleaning_model":     ("UNIT_CLEANING_MODEL",     "claude:claude-haiku-4-5", str),
    "character_fix_model":     ("CHARACTER_FIX_MODEL",     "claude:claude-opus-4-8",  str),
    "overload_retry_wait_seconds": ("OVERLOAD_RETRY_WAIT_SECONDS", 300,             int),
    "json_fix_timeout_seconds":    ("JSON_FIX_TIMEOUT_SECONDS",    300,             int),
    # How many books may translate at once. Chapters within a book always stay
    # sequential (the entity glossary is built incrementally); different books
    # share nothing, so they can run in parallel. Bounded because each worker is
    # another MySQL pool consumer and another concurrent provider stream.
    "max_concurrent_translations": ("MAX_CONCURRENT_TRANSLATIONS", 3,               int),
    "site_name":               ("SITE_NAME",               "T9",                      str),
    "public_site_name":        ("PUBLIC_SITE_NAME",        "Boonnovels",              str),
    "site_base_url":           ("SITE_BASE_URL",           "",                        str),
    "email_from":              ("EMAIL_FROM",              "",                        str),
    "email_backend":           ("EMAIL_BACKEND",           "ses",                     str),
    "debug_mode":              ("DEBUG",                   False,                     bool),
    "public_library":          ("T9_PUBLIC_LIBRARY",       True,                      bool),
    # May readers submit translation error reports (the Reader / book-page
    # "Report an error" modal)? Read live by the public process on every POST.
    "error_reports_enabled":   ("ERROR_REPORTS_ENABLED",   True,                      bool),
    "trad_to_simp":            ("TRAD_TO_SIMP",            False,                     bool),
    # May the translation model revise notes — and correct gender — on entities
    # it already knows? Every change is snapshotted (entity_note_revisions /
    # entity_gender_revisions) and revertible, so this is a kill switch rather
    # than the safety mechanism.
    "entity_note_updates":     ("ENTITY_NOTE_UPDATES",     True,                      bool),
    # Fleet-wide off switch for collecting footnote candidates during the
    # translation pass. Which books do it, and with what prompt, is per book
    # (the footnote_scan module's scan_mode); this only takes it away.
    "footnote_inline_scan":    ("FOOTNOTE_INLINE_SCAN",    True,                      bool),
    "repetition_guard":        ("REPETITION_GUARD",        False,                     bool),
    # Repair a complete-but-malformed chunk response (unescaped quotes etc.)
    # in place of the JSON Fix modal -- accepted only when the repaired text
    # is identical to the raw output (json_recovery.try_repair). Truncated
    # streams are retried regardless of this switch; they are never repaired.
    "json_auto_repair":        ("JSON_AUTO_REPAIR",        True,                      bool),
    # Default for Claude Code and Codex read-only MCP lookup tools: what a
    # book's claude_code_tools module does on Auto (On/Off per book override it).
    # The URL is the t9-mcp-readonly.service endpoint.
    "claude_code_mcp_tools":   ("CLAUDE_CODE_MCP_TOOLS",   False,                     bool),
    "claude_code_mcp_url":     ("CLAUDE_CODE_MCP_URL",     "http://127.0.0.1:8766/mcp", str),
    # Jev (TypeSafe System One) -- cheap classification, never translation.
    # The key is a secret and lives in .env as TYPESAFE_KEY. Chapter-conflict
    # triage: off | suggest (show Jev's verdict in the panel) | auto (resolve
    # confident author's-note/ad vs story conflicts without asking).
    "jev_model":               ("JEV_MODEL",               "jev-latest",              str),
    "jev_chapter_conflict":    ("JEV_CHAPTER_CONFLICT",    "auto",                    str),
    "jev_conflict_confidence": ("JEV_CONFLICT_CONFIDENCE", 0.9,                       float),
    # Unit-converter false-positive filter. With a key and this on, Jev decides
    # each match it is confident about; the per-run cleaning model (if any) gets
    # only the ones below jev_unit_confidence.
    "jev_unit_filter":         ("JEV_UNIT_FILTER",         True,                      bool),
    "jev_unit_confidence":     ("JEV_UNIT_CONFIDENCE",     0.9,                       float),
    "disable_content_cache":   ("DISABLE_CONTENT_CACHE",   False,                     bool),
    "disable_media_cache":     ("DISABLE_MEDIA_CACHE",     False,                     bool),
    "wp_url":                  ("WP_URL",                  "",                        str),
    "wp_username":             ("WP_USERNAME",             "",                        str),
    "grammar_check_enabled":   ("GRAMMAR_CHECK_ENABLED",   False,                     bool),
    "languagetool_url":        ("LANGUAGETOOL_URL",        "http://127.0.0.1:8081",   str),
    "grammar_language":        ("GRAMMAR_LANGUAGE",        "en-US",                   str),
    "polish_model":            ("POLISH_MODEL",            "claude:claude-sonnet-4-6", str),
    # Automated source scraping (scrape_worker.py / t9-scraper.service). The
    # scraper runs on a home server; its bearer token is a secret and lives in
    # .env as SCRAPER_TOKEN. Which books are scraped is per book (book_scrape
    # .mode); scrape_enabled only takes it all away. Read live by the worker.
    "scrape_enabled":          ("SCRAPE_ENABLED",          True,                      bool),
    "scraper_url":             ("SCRAPER_URL",             DEFAULT_SCRAPER_URL,       str),
    # TOC sweep hours (server time), comma-separated: "5,13,21".
    "scrape_sweep_hours":      ("SCRAPE_SWEEP_HOURS",      "5,13,21",                 str),
    "scrape_chunk_size":       ("SCRAPE_CHUNK_SIZE",       50,                        int),
    # Auto-translate only starts when the newest queued chapter is at most this
    # far past the newest translated one (per-book override: book_scrape.auto_gap).
    "scrape_default_gap":      ("SCRAPE_DEFAULT_GAP",      10,                        int),
    # The model auto-translate starts use (per-book override:
    # book_scrape.run_options.translation_model). Deliberately not the model
    # of the book's latest chapter, which goes stale over a break.
    "scrape_translation_model": ("SCRAPE_TRANSLATION_MODEL", "claudecode:claude-opus-5-5", str),
    "scrape_toc_spacing_seconds": ("SCRAPE_TOC_SPACING_SECONDS", 20,                 int),
    # Browser engine per job: "" (the scraper's default), "patchright" or
    # "extension" — which one draws fewer Cloudflare challenges is still open.
    "scrape_engine":           ("SCRAPE_ENGINE",           "",                        str),
    # Where the scraper POSTs a job's "blocked on a challenge" notice
    # (…/api/scraper-notify on this admin site), signed with SCRAPER_NOTIFY_SECRET
    # from .env; blank = jobs carry no callback. And the noVNC page where the
    # challenge gets solved: only a fallback — the scraper reports its own URL
    # with a blocked job, and that wins.
    "scrape_notify_url":       ("SCRAPE_NOTIFY_URL",       "",                        str),
    "scraper_vnc_url":         ("SCRAPER_VNC_URL",         "",                        str),
    # NovelUpdates posting (nu_sync.py, the NU lane in t9-scraper.service).
    # Off until switched on; which books post is per book (book_nu.mode).
    "nu_enabled":              ("NU_ENABLED",              False,                     bool),
    # Pause between two releases of one series.
    "nu_release_spacing_seconds": ("NU_RELEASE_SPACING_SECONDS", 60,              int),
    # After a series' first release: start group syncs this long after it,
    # sync this often, and alert when it still isn't approved after the timeout.
    "nu_approval_wait_hours":  ("NU_APPROVAL_WAIT_HOURS",  20,                        int),
    "nu_approval_timeout_hours": ("NU_APPROVAL_TIMEOUT_HOURS", 72,                   int),
    "nu_group_sync_hours":     ("NU_GROUP_SYNC_HOURS",     4,                         int),
    # Alerts: email (blank = EMAIL_FROM, the editor mailbox) and an ntfy topic
    # URL (blank = none; its token is NU_NTFY_TOKEN in .env).
    "nu_alert_email":          ("NU_ALERT_EMAIL",          "",                        str),
    "nu_ntfy_url":             ("NU_NTFY_URL",             "",                        str),
    # The operator's NovelUpdates Forum username, shown on the public About page
    # ("NUF username: …") — NU verifies a translation group by that line on its
    # site. Blank = the line isn't shown. Per deployment, never in code.
    "nu_forum_username":       ("NU_FORUM_USERNAME",       "",                        str),
    # Rate limit on backlog releases. NU auto-approves a release only when it
    # finds the chapter in the book's RSS feed (its newest nu_feed_window_book
    # chapters); anything older lands in NU's manual queue, so at most
    # nu_backlog_limit of those go per book per nu_backlog_window_hours, and
    # nu_backlog_limit_all across all books (0 = no limit). nu_feed_window_site
    # > 0 also requires a place in the site-wide feed's newest N.
    "nu_backlog_limit":        ("NU_BACKLOG_LIMIT",        150,                       int),
    "nu_backlog_window_hours": ("NU_BACKLOG_WINDOW_HOURS", 8,                         int),
    "nu_backlog_limit_all":    ("NU_BACKLOG_LIMIT_ALL",    0,                         int),
    "nu_feed_window_book":     ("NU_FEED_WINDOW_BOOK",     100,                       int),
    "nu_feed_window_site":     ("NU_FEED_WINDOW_SITE",     0,                         int),
}

# NOTE: DigitalOcean Spaces config (SPACES_ENABLED / SPACES_BUCKET / SPACES_REGION
# / SPACES_PREFIX / SPACES_CDN_BASE and the BUCKET_* credentials) is deploy-time
# infrastructure and lives ONLY in .env — like DB_BACKEND / MYSQL_* / CF_*. It is
# deliberately NOT in this SCHEMA: managed keys here get mirrored from
# settings.json into os.environ on load, which would clobber the .env values.
# config.py reads the SPACES_* vars directly via os.getenv with defaults.
#
# Amazon SES follows the same rule: the EMAIL_BACKEND toggle above is a
# non-secret runtime setting, but the SES credentials and region
# (SES_ACCESS_KEY_ID / SES_SECRET_ACCESS_KEY / SES_REGION) are secrets/infra
# and live ONLY in .env — do NOT add them to SCHEMA.

# realpath: when settings.json is a symlink (the Docker image points it into
# the /data volume), _save_locked's os.replace must replace the target, not
# the link — otherwise the first save detaches settings from the volume.
_PATH = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json"))
_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
_data = None  # cached dict; populated lazily by load()
_mtime_ns = None  # st_mtime_ns of settings.json when _data was last read


def persist_env(key, value):
    """Write or update a key=value in the project .env file so it survives restarts.

    Reserved for true secrets (provider API keys, WP app password). All
    non-secret user settings go through settings.json via update() instead.

    Values are validated (no newlines — a value containing "\\nADMIN_PASSWORD=x"
    would otherwise inject lines that take effect on next restart) and quoted,
    and the file is kept at 0600.
    """
    import re
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key or ""):
        raise ValueError(f"Invalid .env key: {key!r}")
    value = "" if value is None else str(value)
    if re.search(r"[\r\n\x00]", value):
        raise ValueError("Value must not contain newlines")
    # Double-quote so '#', spaces, and leading/trailing whitespace survive
    # python-dotenv's parser; escape backslashes and embedded quotes.
    quoted = '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    with _lock:
        lines = []
        if os.path.exists(_ENV_PATH):
            with open(_ENV_PATH, "r") as f:
                lines = f.readlines()

        pattern = re.compile(rf"^{re.escape(key)}=")
        for i, line in enumerate(lines):
            if pattern.match(line):
                lines[i] = f"{key}={quoted}\n"
                break
        else:
            lines.append(f"{key}={quoted}\n")

        fd = os.open(_ENV_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.writelines(lines)
        try:
            os.chmod(_ENV_PATH, 0o600)  # tighten pre-existing files too
        except OSError:
            pass


def _coerce_from_env(raw, t):
    if t is bool:
        return raw.lower() in ("1", "true", "yes")
    if t is int:
        return int(raw)
    if t is float:
        return float(raw)
    return raw


def _to_env_str(val, t):
    if t is bool:
        return "1" if val else "0"
    return str(val)


def _save_locked(data):
    """Atomic write. Caller must hold _lock."""
    tmp = _PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.replace(tmp, _PATH)


def _sync_to_env(data):
    """Mirror settings into os.environ so legacy os.getenv() callers see them."""
    for key, val in data.items():
        if key not in SCHEMA:
            continue
        env_var, _, t = SCHEMA[key]
        os.environ[env_var] = _to_env_str(val, t)


def _seed_from_env():
    """First-run migration: build initial dict from current os.environ values."""
    out = {}
    for key, (env_var, default, t) in SCHEMA.items():
        raw = os.getenv(env_var)
        if raw is None:
            out[key] = default
            continue
        try:
            out[key] = _coerce_from_env(raw, t)
        except (ValueError, AttributeError):
            out[key] = default
    return out


def strip_comments(text):
    """Drop whole-line // comments, so settings.json (and the example) can
    explain themselves. Only whole lines: JSON strings can't span lines, so a
    line starting with // is never inside a value — while a trailing // could
    be part of a URL. Rewrites by update() don't keep comments."""
    return "\n".join(line for line in text.split("\n") if not line.lstrip().startswith("//"))


def _file_mtime_ns():
    try:
        return os.stat(_PATH).st_mtime_ns
    except OSError:
        return None


def _load_locked():
    """(Re)read settings.json into the cache. Caller must hold _lock."""
    global _data, _mtime_ns
    if os.path.exists(_PATH):
        with open(_PATH, "r", encoding="utf-8") as f:
            stored = json.loads(strip_comments(f.read()))
        # Fill in any missing keys with defaults so newly-added settings
        # don't break existing deployments.
        data = {key: stored.get(key, default) for key, (_, default, _) in SCHEMA.items()}
    else:
        data = _seed_from_env()
        _save_locked(data)

    _sync_to_env(data)
    _data = data
    _mtime_ns = _file_mtime_ns()


def _refresh_locked():
    """Reload the cache when settings.json changed on disk. Caller holds _lock.

    The admin and public reader apps run as separate processes sharing one
    settings.json; without this, a process would never see the other's
    update() writes (cache toggles, site names, public_library, ...).
    _save_locked() writes via os.replace, so a concurrent read never sees a
    partial file. A file that fails to parse (hand-edited) keeps the cached
    values instead of taking the process down.
    """
    if _data is None:
        _load_locked()
    elif _file_mtime_ns() != _mtime_ns:
        try:
            _load_locked()
        except (ValueError, OSError):
            pass  # keep serving the cached settings


def load():
    """Load settings from settings.json (or seed from env on first run).

    Mirrors values into os.environ so legacy os.getenv() callers work.
    Returns a copy of the loaded dict, re-reading the file only when its
    mtime shows another process has rewritten it.
    """
    with _lock:
        _refresh_locked()
        return dict(_data)


def get(key, default=None):
    """Return a single setting value (fresh across processes)."""
    with _lock:
        _refresh_locked()
        return _data.get(key, default)


def all_settings():
    """Return a copy of the full settings dict (fresh across processes)."""
    with _lock:
        _refresh_locked()
        return dict(_data)


def update(updates):
    """Apply partial updates, persist to JSON, and sync to os.environ.

    Raises ValueError if any key is not in SCHEMA. Returns the updated dict.
    """
    global _mtime_ns
    with _lock:
        # Merge onto the latest on-disk state so concurrent writers
        # (admin + public processes) don't clobber each other's keys.
        _refresh_locked()

        for key in updates:
            if key not in SCHEMA:
                raise ValueError(f"Unknown setting: {key}")

        for key, val in updates.items():
            _data[key] = val

        _save_locked(_data)
        _mtime_ns = _file_mtime_ns()
        _sync_to_env(_data)
        return dict(_data)



EXAMPLE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json.example")
EXAMPLE_HEADER = """\
settings.json.example: every setting at its default, explained.

Optional. Without settings.json, the first start creates one from these defaults
plus any matching environment variables (.env, shown as "env:" below), and the
admin Settings page edits it after that. To start from this file instead:
cp settings.json.example settings.json, then edit. A copied file skips the .env
seeding, so set values here.

Secrets (API keys, passwords, tokens) never go here: they live in .env.
Whole-line // comments are allowed, but the Settings page rewrites the file
without them on its first save.

Generated from settings_store.SCHEMA: python3 settings_store.py --write-example"""


def _schema_comments():
    """{key: [comment lines]} from the # comments directly above each SCHEMA
    entry in this file, so the example explains each setting the way the code
    does and can't drift from it."""
    import re
    src = open(os.path.abspath(__file__), encoding="utf-8").read()
    block = src[src.index("SCHEMA = {"):]
    block = block[:block.index("\n}\n")]
    out, pending = {}, []
    for line in block.split("\n")[1:]:
        stripped = line.strip()
        if stripped.startswith("#"):
            pending.append(stripped.lstrip("#").strip())
            continue
        m = re.match(r'"(\w+)"\s*:', stripped)
        if m:
            out[m.group(1)] = pending
        pending = []
    return out


def render_example():
    """settings.json.example's text: every SCHEMA key at its default, in SCHEMA
    order (related settings stay together), each under its code comment."""
    comments = _schema_comments()
    lines = ["// " + l if l else "//" for l in EXAMPLE_HEADER.split("\n")]
    lines.append("{")
    keys = list(SCHEMA)
    for i, key in enumerate(keys):
        env_var, default, _ = SCHEMA[key]
        if comments.get(key):
            lines.append("")
            lines += [f"  // {c}" if c else "  //" for c in comments[key]]
        lines.append(f"  // env: {env_var}")
        comma = "," if i < len(keys) - 1 else ""
        lines.append(f"  {json.dumps(key)}: {json.dumps(default, ensure_ascii=False)}{comma}")
    lines.append("}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["--write-example"]:
        with open(EXAMPLE_PATH, "w", encoding="utf-8") as f:
            f.write(render_example())
        print(f"wrote {EXAMPLE_PATH}")
    else:
        print("usage: python3 settings_store.py --write-example", file=sys.stderr)
        sys.exit(2)
