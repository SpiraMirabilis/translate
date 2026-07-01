"""Tests for web/services/public_guard.py (shared public-API guards)."""
import time

import pytest
from fastapi import HTTPException

from web.services.public_guard import SlidingWindowLimiter, TTLSet


class TestSlidingWindowLimiter:
    def test_allows_up_to_limit(self):
        lim = SlidingWindowLimiter(60, 3)
        for _ in range(3):
            lim.check("1.2.3.4")

    def test_raises_429_over_limit(self):
        lim = SlidingWindowLimiter(60, 3)
        for _ in range(3):
            lim.check("1.2.3.4")
        with pytest.raises(HTTPException) as exc:
            lim.check("1.2.3.4")
        assert exc.value.status_code == 429

    def test_custom_detail(self):
        lim = SlidingWindowLimiter(60, 1, "Too many comments. Slow down.")
        lim.check("k")
        with pytest.raises(HTTPException) as exc:
            lim.check("k")
        assert exc.value.detail == "Too many comments. Slow down."

    def test_keys_independent(self):
        lim = SlidingWindowLimiter(60, 1)
        lim.check("a")
        lim.check("b")  # different key, not limited

    def test_window_expiry(self, monkeypatch):
        lim = SlidingWindowLimiter(60, 1)
        base = time.time()
        monkeypatch.setattr(time, "time", lambda: base)
        lim.check("a")
        # Advance past the window: the old hit no longer counts
        monkeypatch.setattr(time, "time", lambda: base + 61)
        lim.check("a")

    def test_prune_evicts_stale_keys(self, monkeypatch):
        lim = SlidingWindowLimiter(60, 5, prune_every=10)
        base = time.time()
        monkeypatch.setattr(time, "time", lambda: base)
        for i in range(9):
            lim.check(f"ip-{i}")
        assert len(lim._hits) == 9
        # Jump past the window; the 10th check triggers the prune sweep
        monkeypatch.setattr(time, "time", lambda: base + 120)
        lim.check("fresh")
        assert set(lim._hits) == {"fresh"}

    def test_reset(self):
        lim = SlidingWindowLimiter(60, 1)
        lim.check("a")
        lim.reset()
        lim.check("a")  # no raise — state cleared


class TestTTLSet:
    def test_add_and_contains(self):
        s = TTLSet(3600)
        s.add("uuid-1")
        assert "uuid-1" in s
        assert "uuid-2" not in s

    def test_falsy_keys_never_members(self):
        s = TTLSet(3600)
        s.add(None)
        s.add("")
        assert None not in s
        assert "" not in s

    def test_expiry(self, monkeypatch):
        s = TTLSet(10)
        base = time.time()
        monkeypatch.setattr(time, "time", lambda: base)
        s.add("k")
        assert "k" in s
        monkeypatch.setattr(time, "time", lambda: base + 11)
        assert "k" not in s
        # expired key was evicted on access
        assert "k" not in s._expiry
