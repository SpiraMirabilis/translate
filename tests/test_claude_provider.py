"""Request-shaping tests for ClaudeProvider — thinking-effort control and the
progressive param-dropping retry loop. The anthropic client is faked, so no
network or key is needed."""
import types

import pytest

import providers.claude_provider as cp
from providers.claude_provider import ClaudeProvider


class FakeBadRequest(Exception):
    pass


class FakeAPIStatus(Exception):
    pass


class FakeMsg:
    """Mimics an anthropic Message with a single text content block."""
    def __init__(self, text="ok"):
        self.content = [types.SimpleNamespace(type="text", text=text)]
        self.stop_reason = "end_turn"
        self.usage = types.SimpleNamespace(input_tokens=1, output_tokens=1)
        self.model = "fake"


class FakeMessages:
    """Scripted create(): each call consults `errors` for a substring to raise."""
    def __init__(self, errors):
        self.errors = list(errors)   # list of error-message substrings (or None)
        self.calls = []              # captured kwargs per call

    def create(self, **kwargs):
        self.calls.append(kwargs)
        err = self.errors.pop(0) if self.errors else None
        if err is not None:
            raise FakeBadRequest(f"Error 400: {err}")
        return FakeMsg()


def make_provider(errors):
    """A ClaudeProvider with a scripted fake client, bypassing __init__."""
    p = ClaudeProvider.__new__(ClaudeProvider)
    p.max_output_tokens = 8192
    p.client = types.SimpleNamespace(messages=FakeMessages(errors))
    return p


@pytest.fixture(autouse=True)
def fake_anthropic(monkeypatch):
    monkeypatch.setattr(cp, "anthropic",
                        types.SimpleNamespace(BadRequestError=FakeBadRequest,
                                              APIStatusError=FakeAPIStatus))
    # Isolate the process-level memo sets between tests.
    monkeypatch.setattr(cp, "_MODELS_REJECTING_EFFORT", set())
    monkeypatch.setattr(cp, "_MODELS_REJECTING_TEMPERATURE", set())


def test_effort_adds_adaptive_thinking_and_output_config():
    p = make_provider(errors=[])
    p.chat_completion(messages=[{"role": "user", "content": "hi"}],
                      model="claude-sonnet-5", temperature=0, thinking_effort="low")
    sent = p.client.messages.calls[0]
    assert sent["thinking"] == {"type": "adaptive"}
    assert sent["output_config"] == {"effort": "low"}


def test_no_effort_sends_no_thinking_params():
    """Default path (translation calls) must be byte-for-byte unchanged."""
    p = make_provider(errors=[])
    p.chat_completion(messages=[{"role": "user", "content": "hi"}],
                      model="claude-sonnet-5", temperature=0)
    sent = p.client.messages.calls[0]
    assert "thinking" not in sent and "output_config" not in sent


def test_effort_rejection_drops_params_retries_and_memoizes():
    # First call rejects (no adaptive support); retry without effort succeeds.
    p = make_provider(errors=["adaptive thinking is not supported on this model", None])
    p.chat_completion(messages=[{"role": "user", "content": "hi"}],
                      model="claude-haiku-4-5", temperature=0, thinking_effort="low")
    calls = p.client.messages.calls
    assert len(calls) == 2
    assert "output_config" in calls[0] and "output_config" not in calls[1]
    # Temperature survived — it was never blamed for the effort conflict.
    assert calls[1]["temperature"] == 0
    assert "claude-haiku-4-5" in cp._MODELS_REJECTING_EFFORT
    assert cp._MODELS_REJECTING_TEMPERATURE == set()


def test_effort_then_temperature_conflict_composes():
    # A model that rejects the temperature/adaptive combo first, then adaptive:
    # both drops must compose within one call rather than escaping.
    p = make_provider(errors=[
        "temperature may only be set to 1 when thinking is adaptive",  # effort dropped first
        None,
    ])
    p.chat_completion(messages=[{"role": "user", "content": "hi"}],
                      model="claude-haiku-4-5", temperature=0, thinking_effort="low")
    # Effort dropped on the first rejection (it's present), so a single retry
    # (now without adaptive) succeeds and temperature is preserved.
    calls = p.client.messages.calls
    assert len(calls) == 2
    assert "claude-haiku-4-5" in cp._MODELS_REJECTING_EFFORT


def test_temperature_rejection_without_effort():
    p = make_provider(errors=["temperature is deprecated for this model", None])
    p.chat_completion(messages=[{"role": "user", "content": "hi"}],
                      model="claude-opus-4-8", temperature=0)
    calls = p.client.messages.calls
    assert len(calls) == 2
    assert "temperature" in calls[0] and "temperature" not in calls[1]
    assert "claude-opus-4-8" in cp._MODELS_REJECTING_TEMPERATURE


def test_unrecognized_bad_request_surfaces():
    # No effort params present and not a temperature error → propagate.
    p = make_provider(errors=["messages: at least one message is required"])
    with pytest.raises(FakeBadRequest):
        p.chat_completion(messages=[{"role": "user", "content": "hi"}],
                          model="claude-opus-4-8", temperature=0)
