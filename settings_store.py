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
    "site_name":               ("SITE_NAME",               "T9",                      str),
    "public_site_name":        ("PUBLIC_SITE_NAME",        "Boonnovels",              str),
    "site_base_url":           ("SITE_BASE_URL",           "",                        str),
    "email_from":              ("EMAIL_FROM",              "",                        str),
    "debug_mode":              ("DEBUG",                   False,                     bool),
    "public_library":          ("T9_PUBLIC_LIBRARY",       True,                      bool),
    "trad_to_simp":            ("TRAD_TO_SIMP",            False,                     bool),
    "wp_url":                  ("WP_URL",                  "",                        str),
    "wp_username":             ("WP_USERNAME",             "",                        str),
}

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")
_data = None  # cached dict; populated lazily by load()


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
