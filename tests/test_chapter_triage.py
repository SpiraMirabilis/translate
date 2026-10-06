"""Jev triage of chapter conflicts (chapter_triage + WebInterface hook).

Never hits the live API: classify_chapter / triage are stubbed.
"""
import pytest

import chapter_triage
import jev_client
from test_chapter_conflict_merge import EXISTING, make_ui


def v(choice, confidence):
    return {"choice": choice, "confidence": confidence, "probabilities": {}}


@pytest.mark.parametrize("existing, incoming, expected", [
    (v("filler", 0.99), v("story", 0.99), "proceed"),   # old is a note -> overwrite
    (v("story", 0.99), v("filler", 0.99), "cancel"),    # new is a note -> skip it
    (v("story", 0.99), v("story", 0.99), None),         # a real conflict
    (v("filler", 0.99), v("filler", 0.99), None),
    (v("filler", 0.89), v("story", 0.99), None),        # one side below threshold
    (v("story", 0.99), v("filler", 0.55), None),
    (v("filler", 0.90), v("story", 0.90), "proceed"),   # threshold is inclusive
])
def test_decide_truth_table(existing, incoming, expected):
    assert chapter_triage.decide(existing, incoming, 0.9) == expected


def test_suggest_ignores_confidence():
    assert chapter_triage.suggest(v("story", 0.6), v("filler", 0.55)) == "cancel"


def test_triage_never_raises(monkeypatch):
    def boom(title, lines):
        raise jev_client.JevError("HTTP 401: bad key")
    monkeypatch.setattr(chapter_triage, "classify_chapter", boom)
    r = chapter_triage.triage("a", ["x"], "b", ["y"], 0.9)
    assert r["error"] == "HTTP 401: bad key"


def test_triage_classifies_each_side(monkeypatch):
    verdicts = {"note": v("filler", 1.0), "ch": v("story", 1.0)}
    monkeypatch.setattr(chapter_triage, "classify_chapter", lambda t, lines: verdicts[t])
    r = chapter_triage.triage("note", ["请假"], "ch", ["正文"], 0.9)
    assert r["decision"] == "proceed" and r["suggestion"] == "proceed"
    assert r["existing"]["choice"] == "filler" and r["incoming"]["choice"] == "story"


def test_state_truncates_long_chapters():
    state = chapter_triage._state("t", ["字" * 50000])
    assert len(state["text"]) < chapter_triage.MAX_STATE_CHARS + 50
    assert state["text"].endswith("[…truncated]")


def test_disabled_without_key(monkeypatch):
    monkeypatch.setattr(chapter_triage, "mode", lambda: "auto")
    assert chapter_triage.enabled() is False     # conftest drops TYPESAFE_KEY
    monkeypatch.setenv("TYPESAFE_KEY", "sk-test")
    assert chapter_triage.enabled() is True
    monkeypatch.setattr(chapter_triage, "mode", lambda: "off")
    assert chapter_triage.enabled() is False


# --- WebInterface.check_chapter_conflict ------------------------------------

@pytest.fixture
def jev(monkeypatch):
    """Turn triage on with a scripted result; returns a setter for mode/result."""
    state = {"mode": "auto", "result": None}
    monkeypatch.setattr(chapter_triage, "enabled", lambda: state["mode"] != "off")
    monkeypatch.setattr(chapter_triage, "mode", lambda: state["mode"])
    monkeypatch.setattr(chapter_triage, "triage", lambda *a, **k: state["result"])
    return state


def result(decision, suggestion=None):
    return {"existing": v("filler", 1.0), "incoming": v("story", 1.0), "threshold": 0.9,
            "decision": decision, "suggestion": suggestion or decision}


def test_auto_proceed_overwrites_without_prompting(jev):
    jev["result"] = result("proceed")
    ui = make_ui(EXISTING, [])          # no scripted decisions: must not prompt
    assert ui.check_chapter_conflict(["第134章 正文"]) is True
    assert ui.job_manager.status == "running"
    assert [m["type"] for m in ui.job_manager.prompts] == ["chapter_conflict_auto_resolved"]
    assert "resolved by Jev" in ui.job_manager.activity[0]["message"]


def test_auto_cancel_skips_the_queue_item(jev):
    jev["result"] = result("cancel")
    ui = make_ui(EXISTING, [])
    assert ui.check_chapter_conflict(["求月票"]) is False
    assert ui.job_manager.status == "running"


def test_undecided_prompts_with_verdict(jev):
    jev["result"] = result(None, suggestion="proceed")
    ui = make_ui(EXISTING, [{"decision": "cancel"}])
    assert ui.check_chapter_conflict(["第134章 正文"]) is False
    assert ui.job_manager.pending_chapter_conflict["jev"]["suggestion"] == "proceed"


def test_suggest_mode_prompts_even_when_confident(jev):
    jev["mode"] = "suggest"
    jev["result"] = result("proceed")
    ui = make_ui(EXISTING, [{"decision": "proceed"}])
    assert ui.check_chapter_conflict(["第134章 正文"]) is True
    assert ui.job_manager.pending_chapter_conflict["jev"]["decision"] == "proceed"
    assert ui.job_manager.prompts[0]["type"] == "chapter_conflict_needed"


def test_triage_error_still_prompts(jev):
    jev["result"] = {"error": "HTTP 529", "threshold": 0.9}
    ui = make_ui(EXISTING, [{"decision": "cancel"}])
    assert ui.check_chapter_conflict(["第134章 正文"]) is False
    assert ui.job_manager.pending_chapter_conflict["jev"]["error"] == "HTTP 529"


def test_off_prompts_without_verdict(jev):
    jev["mode"] = "off"
    ui = make_ui(EXISTING, [{"decision": "cancel"}])
    assert ui.check_chapter_conflict(["第134章 正文"]) is False
    assert "jev" not in ui.job_manager.pending_chapter_conflict
