"""Behavior lock-in tests for providers/base.py."""
import json
from datetime import datetime, timezone

import pytest

from providers.base import (
    ModelProvider,
    OverloadedError,
    SessionLimitError,
    looks_overloaded,
    looks_session_limited,
    limit_kind,
    parse_session_reset_seconds,
)


class DummyProvider(ModelProvider):
    """Minimal concrete subclass so instance helpers can be exercised."""

    def chat_completion(self, messages, model, **kwargs):
        return {}

    def get_response_content(self, response):
        return ""

    def get_streaming_content(self, chunk):
        return None

    def is_stream_complete(self, chunk):
        return True

    @property
    def provider_name(self):
        return "dummy"

    @property
    def supported_features(self):
        return []


@pytest.fixture
def provider():
    return DummyProvider(api_key="test-key")


# ── _strip_markdown_fences ─────────────────────────────────────────


def test_strip_fences_json_block(provider):
    fenced = '```json\n{"a": 1}\n```'
    assert provider._strip_markdown_fences(fenced) == '{"a": 1}'


def test_strip_fences_no_language_tag(provider):
    fenced = '```\n{"a": 1}\n```'
    assert provider._strip_markdown_fences(fenced) == '{"a": 1}'


def test_strip_fences_passthrough_unfenced(provider):
    assert provider._strip_markdown_fences('  {"a": 1}  ') == '{"a": 1}'


def test_strip_fences_open_fence_no_newline(provider):
    # Fence with no newline: only the leading ``` is dropped.
    assert provider._strip_markdown_fences("```abc") == "abc"


# ── validate_json_response ─────────────────────────────────────────


def test_validate_json_plain(provider):
    assert provider.validate_json_response('{"translation": ["hi"]}') == {
        "translation": ["hi"]
    }


def test_validate_json_fenced(provider):
    content = '```json\n{"entities": {"characters": {}}}\n```'
    assert provider.validate_json_response(content) == {
        "entities": {"characters": {}}
    }


def test_validate_json_embedded_in_prose(provider):
    content = 'Sure! Here is the result:\n{"a": {"b": 2}}\nHope that helps.'
    assert provider.validate_json_response(content) == {"a": {"b": 2}}


def test_validate_json_failure_raises(provider):
    with pytest.raises(json.JSONDecodeError):
        provider.validate_json_response("not json at all")


def test_validate_json_top_level_array_falls_through(provider):
    # NOTE: documents current behavior; see improvement plan.
    # The brace-scan fallback only looks for '{', so a bare top-level JSON
    # array parses fine as-is (first json.loads attempt succeeds).
    assert provider.validate_json_response("[1, 2, 3]") == [1, 2, 3]


# ── looks_overloaded ───────────────────────────────────────────────


def test_overloaded_claude_code_cli_format():
    assert looks_overloaded("API Error: 529 Overloaded") is True


def test_overloaded_cli_format_with_tail():
    msg = ("API Error: 529 Overloaded. This is a server-side issue - "
           "check https://status.claude.com.")
    assert looks_overloaded(msg) is True


def test_overloaded_sdk_exception_format():
    sdk = ('Error code: 529 - {"type": "error", "error": '
           '{"type": "overloaded_error", "message": "Overloaded"}}')
    assert looks_overloaded(sdk) is True


def test_overloaded_strict_rejects_json_shaped_payload():
    # strict=True refuses to match inside a JSON-shaped payload — a real
    # translation could legitimately contain "overloaded" in prose.
    payload = '{"translation": ["the array was overloaded_error text"]}'
    assert looks_overloaded(payload) is False
    assert looks_overloaded(payload, strict=False) is True


def test_overloaded_strict_rejects_long_text():
    long_text = "overloaded_error " + "x" * 300
    assert looks_overloaded(long_text) is False
    assert looks_overloaded(long_text, strict=False) is True


def test_overloaded_non_matching_prose():
    assert looks_overloaded("The gates were overloaded with refugees.") is False


def test_overloaded_empty_and_none():
    assert looks_overloaded("") is False
    assert looks_overloaded(None) is False


def test_overloaded_error_is_exception():
    assert issubclass(OverloadedError, Exception)


# ── looks_session_limited / parse_session_reset_seconds ────────────


def test_session_limited_notice():
    notice = "You've hit your session limit · resets 10:40pm (UTC)"
    assert looks_session_limited(notice) is True


def test_session_limited_non_match():
    assert looks_session_limited("All good, carry on") is False
    assert looks_session_limited("") is False
    assert looks_session_limited(None) is False


def test_session_limit_error_keeps_reset_text():
    err = SessionLimitError("boom", reset_text="resets 3pm")
    assert err.reset_text == "resets 3pm"
    err2 = SessionLimitError("resets 4pm")
    assert err2.reset_text == "resets 4pm"


def test_parse_reset_seconds_same_day():
    now = datetime(2026, 7, 1, 12, 0, 0)
    # 1:00pm is an hour ahead + 60s grace
    assert parse_session_reset_seconds("resets 1:00pm", now=now) == 3660


def test_parse_reset_seconds_rolls_to_tomorrow():
    now = datetime(2026, 7, 1, 12, 0, 0)
    # 11:00am already passed -> tomorrow: 23h + 60s grace
    assert parse_session_reset_seconds("resets 11:00am", now=now) == 82860


def test_parse_reset_seconds_utc():
    now = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert parse_session_reset_seconds("resets 12:30pm (UTC)", now=now) == 1860


def test_parse_reset_seconds_24_hour_form():
    now = datetime(2026, 7, 1, 12, 0, 0)
    assert parse_session_reset_seconds("resets 22:40", now=now) == 38460


def test_weekly_limited_notice():
    notice = "You've hit your weekly limit · resets Aug 21 2pm (UTC)"
    assert looks_session_limited(notice) is True
    assert limit_kind(notice) == "weekly"


def test_limit_kind_distinguishes_session_and_weekly():
    assert limit_kind("You've hit your session limit · resets 10:40pm (UTC)") == "session"
    assert limit_kind("You've hit your 5-hour limit · resets 3pm") == "5-hour"
    assert limit_kind("All good, carry on") is None
    assert limit_kind("") is None


def test_parse_reset_seconds_weekly_within_24h():
    # "resets 2pm (UTC) (within 24 hrs)" carries no date -> next 2pm.
    now = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)
    notice = "You've hit your weekly limit · resets 2pm (UTC) (within 24 hrs)"
    assert parse_session_reset_seconds(notice, now=now) == 2 * 3600 + 60


def test_parse_reset_seconds_weekly_dated():
    now = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)
    notice = "You've hit your weekly limit · resets Aug 21 2pm (UTC)"
    # Aug 21 2pm is 2 days 2 hours out, + 60s grace.
    assert parse_session_reset_seconds(notice, now=now) == (2 * 86400) + (2 * 3600) + 60


def test_parse_reset_seconds_dated_today():
    now = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)
    assert parse_session_reset_seconds("resets Aug 19 2pm (UTC)", now=now) == 7260


def test_parse_reset_seconds_dated_rolls_to_next_year():
    now = datetime(2026, 12, 30, 12, 0, 0, tzinfo=timezone.utc)
    # Jan 2 has already passed this year -> 2027.
    assert parse_session_reset_seconds("resets Jan 2 9am (UTC)", now=now) == (2 * 86400) + (21 * 3600) + 60


def test_parse_reset_seconds_dated_variants():
    now = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)
    expected = (2 * 86400) + (2 * 3600) + 60
    for form in ("resets Aug 21 2pm (UTC)",
                 "resets August 21st, 2pm (UTC)",
                 "resets 21 Aug 2026 at 2pm (UTC)",
                 "resets on Aug 21 14:00 (UTC)"):
        assert parse_session_reset_seconds(form, now=now) == expected, form


def test_parse_reset_seconds_absurd_date_returns_none():
    # Beyond the 8-day ceiling a weekly reset can have -> treat as unparseable
    # so the caller falls back to its retry interval.
    now = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)
    assert parse_session_reset_seconds("resets Dec 25 9am (UTC)", now=now) is None


def test_parse_reset_seconds_unknown_wording_before_clock():
    now = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)
    assert parse_session_reset_seconds("resets tomorrow at 3pm (UTC)", now=now) == 10860


def test_parse_reset_seconds_unparseable_returns_none():
    assert parse_session_reset_seconds("no clock in here") is None
    assert parse_session_reset_seconds("resets Smarch 4 2pm (UTC)") is None
    assert parse_session_reset_seconds("") is None
    assert parse_session_reset_seconds(None) is None
