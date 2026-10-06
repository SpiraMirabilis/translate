"""Comment automod via Jev (COMMENT_AUTOMOD_MODEL="jev" / "jev:<model>").

Never hits the live API: jev_client.choice is stubbed.
"""
import pytest

import jev_client
from web.services import automod

CTX = {"chapter_number": 12, "book_title": "Test Book", "source_language": "zh"}


@pytest.fixture
def jev(monkeypatch):
    """Route automod to Jev and script the answer; records each call."""
    calls, answer = [], {}

    def fake_choice(state, instructions, criteria, **kw):
        calls.append({"state": state, "criteria": criteria, **kw})
        if isinstance(answer.get("raise"), Exception):
            raise answer["raise"]
        return {"choice": answer["choice"], "confidence": answer["confidence"],
                "probabilities": {}}

    monkeypatch.setenv("COMMENT_AUTOMOD_MODEL", "jev")
    monkeypatch.delenv("COMMENT_AUTOMOD_JEV_CONFIDENCE", raising=False)
    monkeypatch.setattr(jev_client, "choice", fake_choice)
    return calls, answer


@pytest.mark.parametrize("spec, expected", [
    ("jev", True), ("JEV", True), ("jev:jev-1.13.0", True), (" jev ", True),
    ("claude:claude-haiku-4-5", False), ("jevons:model", False), ("", False),
])
def test_uses_jev(spec, expected):
    assert automod._uses_jev(spec) is expected


@pytest.mark.parametrize("choice, confidence, verdict", [
    ("genuine", 0.99, "genuine"),
    ("spam", 0.95, "spam"),
    ("spam", 0.89, "unsure"),      # below the default 0.9: a human decides
    ("genuine", 0.02, "unsure"),   # confidence is not the option's probability
    ("genuine", 0.90, "genuine"),  # inclusive
])
def test_verdict_from_choice_and_confidence(jev, choice, confidence, verdict):
    calls, answer = jev
    answer.update(choice=choice, confidence=confidence)
    result = automod.classify("great chapter", "reader", CTX)
    assert result["verdict"] == verdict
    assert result["reason"].startswith(f"jev: {choice} ({confidence:.2f})")


def test_threshold_setting(jev, monkeypatch):
    calls, answer = jev
    answer.update(choice="spam", confidence=0.85)
    monkeypatch.setenv("COMMENT_AUTOMOD_JEV_CONFIDENCE", "0.8")
    assert automod.classify("buy now", "x", CTX)["verdict"] == "spam"


def test_state_is_the_untrusted_payload(jev):
    calls, answer = jev
    answer.update(choice="genuine", confidence=1.0)
    automod.classify("b" * 5000, "n" * 100, CTX)
    state = calls[0]["state"]
    assert set(state) == {"display_name", "book", "chapter", "body"}
    assert len(state["body"]) == 3500 and len(state["display_name"]) == 40
    assert set(calls[0]["criteria"]) == {"genuine", "spam"}
    assert calls[0]["model"] is None


def test_model_suffix_is_passed(jev, monkeypatch):
    calls, answer = jev
    answer.update(choice="genuine", confidence=1.0)
    monkeypatch.setenv("COMMENT_AUTOMOD_MODEL", "jev:jev-1.13.0")
    automod.classify("hi", "x", CTX)
    assert calls[0]["model"] == "jev-1.13.0"


def test_jev_error_fails_closed(jev):
    calls, answer = jev
    answer["raise"] = jev_client.JevError("HTTP 401: bad key")
    result = automod.classify("hi", "x", CTX)
    assert result["verdict"] == "error"
    assert "401" in result["reason"]


def test_llm_path_untouched(monkeypatch):
    """A non-Jev spec never reaches jev_client."""
    monkeypatch.setenv("COMMENT_AUTOMOD_MODEL", "claude:claude-haiku-4-5")
    monkeypatch.setattr(jev_client, "choice",
                        lambda *a, **k: pytest.fail("jev called for an LLM spec"))

    class FakeProvider:
        def chat_completion(self, **kw):
            return "resp"

        def get_response_content(self, resp):
            return '{"verdict": "genuine", "reason": "ok"}'

    import config
    monkeypatch.setattr(config.TranslationConfig, "get_client",
                        lambda self, spec: (FakeProvider(), "claude-haiku-4-5"))
    assert automod.classify("hi", "x", CTX) == {"verdict": "genuine", "reason": "ok"}
