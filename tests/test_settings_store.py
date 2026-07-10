"""Behavior lock-in tests for settings_store.py.

The module keeps a module-level cache (_data) and file path (_PATH); each test
points _PATH at tmp_path and resets the cache so nothing touches the real
settings.json or the process environment beyond monkeypatch's cleanup.
"""
import json
import os

import pytest

import settings_store


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Redirect the store to a tmp file and reset the module cache."""
    path = str(tmp_path / "settings.json")
    monkeypatch.setattr(settings_store, "_PATH", path)
    monkeypatch.setattr(settings_store, "_data", None)
    monkeypatch.setattr(settings_store, "_mtime_ns", None)
    # Clear every managed env var so seeding is deterministic.
    for env_var, _default, _t in settings_store.SCHEMA.values():
        monkeypatch.delenv(env_var, raising=False)
    return path


# ── type coercion ──────────────────────────────────────────────────


def test_coerce_bool_from_env():
    assert settings_store._coerce_from_env("1", bool) is True
    assert settings_store._coerce_from_env("true", bool) is True
    assert settings_store._coerce_from_env("YES", bool) is True
    assert settings_store._coerce_from_env("0", bool) is False
    assert settings_store._coerce_from_env("false", bool) is False


def test_coerce_int_and_str_from_env():
    assert settings_store._coerce_from_env("300", int) == 300
    assert settings_store._coerce_from_env("abc", str) == "abc"


def test_to_env_str():
    assert settings_store._to_env_str(True, bool) == "1"
    assert settings_store._to_env_str(False, bool) == "0"
    assert settings_store._to_env_str(300, int) == "300"
    assert settings_store._to_env_str("x", str) == "x"


# ── load() seeding from env ────────────────────────────────────────


def test_load_seeds_from_env_when_file_missing(store, monkeypatch):
    monkeypatch.setenv("TRANSLATION_MODEL", "claude:test-model")
    monkeypatch.setenv("OVERLOAD_RETRY_WAIT_SECONDS", "42")
    monkeypatch.setenv("DEBUG", "1")

    data = settings_store.load()

    assert data["translation_model"] == "claude:test-model"
    assert data["overload_retry_wait_seconds"] == 42
    assert data["debug_mode"] is True
    # Unset keys fall back to schema defaults.
    assert data["site_name"] == "T9"
    assert data["public_library"] is True
    # The seed is persisted to disk.
    assert os.path.exists(store)
    on_disk = json.load(open(store))
    assert on_disk["translation_model"] == "claude:test-model"


def test_load_seed_bad_int_falls_back_to_default(store, monkeypatch):
    monkeypatch.setenv("OVERLOAD_RETRY_WAIT_SECONDS", "not-a-number")
    data = settings_store.load()
    assert data["overload_retry_wait_seconds"] == 300


def test_load_mirrors_values_into_environ(store, monkeypatch):
    settings_store.load()
    # Every schema key is mirrored, bools as "1"/"0".
    assert os.environ["SITE_NAME"] == "T9"
    assert os.environ["T9_PUBLIC_LIBRARY"] == "1"
    assert os.environ["TRAD_TO_SIMP"] == "0"
    assert os.environ["OVERLOAD_RETRY_WAIT_SECONDS"] == "300"


def test_corrupt_file_keeps_cached_values(store):
    first = settings_store.load()
    # Corrupt the file on disk (hand-edit gone wrong) — the reload attempt
    # must fail soft and keep serving the cached values.
    with open(store, "w") as f:
        f.write("{ this is not json")
    assert settings_store.load() == first
    assert settings_store.get("site_name") == first["site_name"]


# ── cross-process reload (mtime-based) ─────────────────────────────


def test_external_write_is_picked_up(store):
    """Another process rewriting settings.json is seen without a restart."""
    settings_store.load()
    assert settings_store.get("site_name") == "T9"

    on_disk = json.load(open(store))
    on_disk["site_name"] = "OtherProcess"
    on_disk["disable_content_cache"] = True
    with open(store, "w") as f:
        json.dump(on_disk, f)
    # Guarantee a different mtime even on coarse-timestamp filesystems.
    st = os.stat(store)
    os.utime(store, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))

    assert settings_store.get("site_name") == "OtherProcess"
    assert settings_store.get("disable_content_cache") is True
    # The reload re-mirrors into os.environ for legacy os.getenv() callers.
    assert os.environ["SITE_NAME"] == "OtherProcess"


def test_update_merges_onto_latest_disk_state(store):
    """update() must not clobber keys another process changed since our read."""
    settings_store.load()

    on_disk = json.load(open(store))
    on_disk["public_library"] = False
    with open(store, "w") as f:
        json.dump(on_disk, f)
    st = os.stat(store)
    os.utime(store, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))

    settings_store.update({"site_name": "Merged"})

    result = json.load(open(store))
    assert result["site_name"] == "Merged"
    assert result["public_library"] is False  # other process's write survives


# ── save / load round trip ─────────────────────────────────────────


def test_update_round_trips_through_file(store, monkeypatch):
    settings_store.load()
    settings_store.update({"site_name": "TestSite", "debug_mode": True,
                           "overload_retry_wait_seconds": 60})

    # Force a fresh load from disk.
    monkeypatch.setattr(settings_store, "_data", None)
    data = settings_store.load()

    assert data["site_name"] == "TestSite"
    assert data["debug_mode"] is True
    assert data["overload_retry_wait_seconds"] == 60
    # Mirrored to env after update + reload.
    assert os.environ["SITE_NAME"] == "TestSite"
    assert os.environ["DEBUG"] == "1"


def test_update_unknown_key_raises(store):
    settings_store.load()
    with pytest.raises(ValueError):
        settings_store.update({"nonexistent_setting": 1})


def test_get_and_all_settings(store):
    settings_store.load()
    assert settings_store.get("site_name") == "T9"
    assert settings_store.get("missing-key", "fallback") == "fallback"
    everything = settings_store.all_settings()
    assert set(everything.keys()) == set(settings_store.SCHEMA.keys())


def test_load_fills_missing_keys_from_defaults(store, monkeypatch):
    # A stored file from an older deployment lacking newer keys.
    with open(store, "w") as f:
        json.dump({"site_name": "OldSite"}, f)
    data = settings_store.load()
    assert data["site_name"] == "OldSite"
    assert data["public_site_name"] == "Boonnovels"  # filled from SCHEMA
