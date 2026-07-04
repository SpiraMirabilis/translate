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

# Schema: setting_key -> (env_var_name, default, type)
# Each managed setting maps to a legacy env var (for the os.environ compat
# shim) and carries a typed default for first-run migration / forward compat.
SCHEMA = {
    "translation_model":       ("TRANSLATION_MODEL",       "oai:o3-mini",             str),
    "advice_model":            ("ADVICE_MODEL",            "oai:o3-mini",             str),
    "pronoun_repair_model":    ("PRONOUN_REPAIR_MODEL",    "claude:claude-haiku-4-5", str),
    "comment_automod_enabled": ("COMMENT_AUTOMOD_ENABLED", False,                     bool),
    "comment_automod_model":   ("COMMENT_AUTOMOD_MODEL",   "claude:claude-haiku-4-5", str),
    "unit_cleaning_model":     ("UNIT_CLEANING_MODEL",     "claude:claude-haiku-4-5", str),
    "character_fix_model":     ("CHARACTER_FIX_MODEL",     "claude:claude-opus-4-8",  str),
    "overload_retry_wait_seconds": ("OVERLOAD_RETRY_WAIT_SECONDS", 300,             int),
    "json_fix_timeout_seconds":    ("JSON_FIX_TIMEOUT_SECONDS",    300,             int),
    "site_name":               ("SITE_NAME",               "T9",                      str),
    "public_site_name":        ("PUBLIC_SITE_NAME",        "Boonnovels",              str),
    "site_base_url":           ("SITE_BASE_URL",           "",                        str),
    "email_from":              ("EMAIL_FROM",              "",                        str),
    "debug_mode":              ("DEBUG",                   False,                     bool),
    "public_library":          ("T9_PUBLIC_LIBRARY",       True,                      bool),
    "trad_to_simp":            ("TRAD_TO_SIMP",            False,                     bool),
    "disable_content_cache":   ("DISABLE_CONTENT_CACHE",   False,                     bool),
    "disable_media_cache":     ("DISABLE_MEDIA_CACHE",     False,                     bool),
    "wp_url":                  ("WP_URL",                  "",                        str),
    "wp_username":             ("WP_USERNAME",             "",                        str),
    "grammar_check_enabled":   ("GRAMMAR_CHECK_ENABLED",   False,                     bool),
    "languagetool_url":        ("LANGUAGETOOL_URL",        "http://127.0.0.1:8081",   str),
    "grammar_language":        ("GRAMMAR_LANGUAGE",        "en-US",                   str),
    "polish_model":            ("POLISH_MODEL",            "claude:claude-sonnet-4-6", str),
}

# NOTE: DigitalOcean Spaces config (SPACES_ENABLED / SPACES_BUCKET / SPACES_REGION
# / SPACES_PREFIX / SPACES_CDN_BASE and the BUCKET_* credentials) is deploy-time
# infrastructure and lives ONLY in .env — like DB_BACKEND / MYSQL_* / CF_*. It is
# deliberately NOT in this SCHEMA: managed keys here get mirrored from
# settings.json into os.environ on load, which would clobber the .env values.
# config.py reads the SPACES_* vars directly via os.getenv with defaults.

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")
_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
_data = None  # cached dict; populated lazily by load()


def persist_env(key, value):
    """Write or update a key=value in the project .env file so it survives restarts.

    Reserved for true secrets (provider API keys, WP app password). All
    non-secret user settings go through settings.json via update() instead.
    """
    import re
    with _lock:
        lines = []
        if os.path.exists(_ENV_PATH):
            with open(_ENV_PATH, "r") as f:
                lines = f.readlines()

        pattern = re.compile(rf"^{re.escape(key)}=")
        for i, line in enumerate(lines):
            if pattern.match(line):
                lines[i] = f"{key}={value}\n"
                break
        else:
            lines.append(f"{key}={value}\n")

        with open(_ENV_PATH, "w") as f:
            f.writelines(lines)


def _coerce_from_env(raw, t):
    if t is bool:
        return raw.lower() in ("1", "true", "yes")
    if t is int:
        return int(raw)
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


def load():
    """Load settings from settings.json (or seed from env on first run).

    Mirrors values into os.environ so legacy os.getenv() callers work.
    Returns a copy of the loaded dict. Idempotent — subsequent calls return
    the cached dict without re-reading the file.
    """
    global _data
    with _lock:
        if _data is not None:
            return dict(_data)

        if os.path.exists(_PATH):
            with open(_PATH, "r", encoding="utf-8") as f:
                stored = json.load(f)
            # Fill in any missing keys with defaults so newly-added settings
            # don't break existing deployments.
            data = {key: stored.get(key, default) for key, (_, default, _) in SCHEMA.items()}
        else:
            data = _seed_from_env()
            _save_locked(data)

        _sync_to_env(data)
        _data = data
        return dict(_data)


def get(key, default=None):
    """Return a single setting value."""
    if _data is None:
        load()
    return _data.get(key, default)


def all_settings():
    """Return a copy of the full settings dict."""
    if _data is None:
        load()
    return dict(_data)


def update(updates):
    """Apply partial updates, persist to JSON, and sync to os.environ.

    Raises ValueError if any key is not in SCHEMA. Returns the updated dict.
    """
    global _data
    with _lock:
        if _data is None:
            # Inline first-load to avoid releasing+reacquiring the lock.
            if os.path.exists(_PATH):
                with open(_PATH, "r", encoding="utf-8") as f:
                    stored = json.load(f)
                _data = {key: stored.get(key, default) for key, (_, default, _) in SCHEMA.items()}
            else:
                _data = _seed_from_env()
            _sync_to_env(_data)

        for key in updates:
            if key not in SCHEMA:
                raise ValueError(f"Unknown setting: {key}")

        for key, val in updates.items():
            _data[key] = val

        _save_locked(_data)
        _sync_to_env(_data)
        return dict(_data)
