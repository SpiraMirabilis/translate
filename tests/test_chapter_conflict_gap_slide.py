"""Gap slide in WebInterface.check_chapter_conflict.

Chapter 300 is saved, the incoming queue item also claims 300 with different
text, 301 is neither saved nor queued, and the next queued item is 302: the
incoming item is the missing 301 and is renumbered there without a prompt.
"""
import pytest

import chapter_triage
import settings_store
from conftest import FakeLogger
from test_chapter_conflict_merge import FakeJob
from web.services.web_interface import WebInterface

STORED = {"title": "第300章", "untranslated": ["第300章 旧"], "content": ["Old."], "summary": ""}


class FakeDB:
    def __init__(self, stored, queue):
        self.stored = dict(stored)          # chapter_number -> chapter dict
        self.queue = [dict(q) for q in queue]
        self.renumbered = []

    def get_book(self, book_id=None):
        return {"title": "Test Book"}

    def get_chapter(self, book_id=None, chapter_number=None):
        return self.stored.get(chapter_number)

    def list_queue(self, book_id=None, include_content=False, include_processing=False):
        return sorted(self.queue, key=lambda q: q["position"])

    def update_queue_chapter_number(self, queue_id, new):
        self.renumbered.append((queue_id, new))
        for q in self.queue:
            if q["id"] == queue_id:
                q["chapter_number"] = new
        return True


def make_ui(stored, queue, decisions=(), current=300, current_pos=10):
    ui = WebInterface.__new__(WebInterface)
    ui.book_id = 7
    ui.chapter_number = current
    ui.logger = FakeLogger()
    cqi = {"id": 1, "book_id": 7, "chapter_number": current, "position": current_pos}
    ui.entity_manager = FakeDB(stored, [dict(cqi, status="processing")] + list(queue))
    ui._current_queue_item = cqi
    ui.job_manager = FakeJob(decisions)
    ui._merge_prefix = None
    return ui


def q(qid, ch, pos):
    return {"id": qid, "book_id": 7, "chapter_number": ch, "position": pos}


@pytest.fixture(autouse=True)
def slide_on(monkeypatch):
    state = {"chapter_conflict_gap_slide": True}
    monkeypatch.setattr(settings_store, "get", lambda k, d=None: state.get(k, d))
    return state


def test_slides_into_single_gap():
    ui = make_ui({300: STORED}, [q(2, 302, 11), q(3, 303, 12)])
    assert ui.check_chapter_conflict(["第300章 新"]) is True
    assert ui.chapter_number == 301
    assert ui._current_queue_item["chapter_number"] == 301
    assert ui.entity_manager.renumbered == [(1, 301)]
    assert ui.job_manager.pending_chapter_conflict is None   # never prompted
    msg = ui.job_manager.prompts[0]
    assert msg["type"] == "chapter_conflict_auto_resolved"
    assert msg["decision"] == "slide" and msg["new_chapter_number"] == 301
    assert "slid into 301" in ui.job_manager.activity[0]["message"]


@pytest.mark.parametrize("stored, queue", [
    ({300: STORED}, [q(2, 303, 11)]),                     # gap wider than one
    ({300: STORED}, []),                                  # nothing follows
    ({300: STORED, 301: STORED}, [q(2, 302, 11)]),        # 301 already saved
    ({300: STORED}, [q(2, 302, 11), q(3, 301, 12)]),      # 301 queued later
    ({300: STORED}, [q(2, 302, 5)]),                      # 302 sits *before* it
])
def test_no_slide_prompts_instead(stored, queue):
    ui = make_ui(stored, queue, [{"decision": "cancel"}])
    assert ui.check_chapter_conflict(["第300章 新"]) is False
    assert ui.entity_manager.renumbered == []
    assert ui.job_manager.pending_chapter_conflict["chapter_number"] == 300


def test_switch_off_prompts(slide_on):
    slide_on["chapter_conflict_gap_slide"] = False
    ui = make_ui({300: STORED}, [q(2, 302, 11)], [{"decision": "cancel"}])
    assert ui.check_chapter_conflict(["第300章 新"]) is False
    assert ui.entity_manager.renumbered == []


def test_pasted_chapter_never_slides():
    ui = make_ui({300: STORED}, [q(2, 302, 11)], [{"decision": "cancel"}])
    ui._current_queue_item = None
    assert ui.check_chapter_conflict(["第300章 新"]) is False


def _jev(monkeypatch, incoming, decision=None):
    monkeypatch.setattr(chapter_triage, "enabled", lambda: True)
    monkeypatch.setattr(chapter_triage, "mode", lambda: "auto")
    monkeypatch.setattr(chapter_triage, "triage", lambda *a, **k: {
        "existing": {"choice": "story", "confidence": 1.0, "probabilities": {}},
        "incoming": incoming, "threshold": 0.9,
        "decision": decision, "suggestion": decision})


def test_jev_filler_is_not_slid(monkeypatch):
    # Confident filler that Jev can't act on alone (e.g. existing side unsure).
    _jev(monkeypatch, {"choice": "filler", "confidence": 0.97, "probabilities": {}})
    ui = make_ui({300: STORED}, [q(2, 302, 11)], [{"decision": "cancel"}])
    assert ui.check_chapter_conflict(["求月票"]) is False
    assert ui.entity_manager.renumbered == []


def test_jev_decision_wins_over_slide(monkeypatch):
    _jev(monkeypatch, {"choice": "filler", "confidence": 0.99, "probabilities": {}}, "cancel")
    ui = make_ui({300: STORED}, [q(2, 302, 11)])
    assert ui.check_chapter_conflict(["求月票"]) is False
    assert ui.entity_manager.renumbered == []


def test_jev_story_still_slides(monkeypatch):
    _jev(monkeypatch, {"choice": "story", "confidence": 0.99, "probabilities": {}})
    ui = make_ui({300: STORED}, [q(2, 302, 11)])
    assert ui.check_chapter_conflict(["第300章 新"]) is True
    assert ui.chapter_number == 301
