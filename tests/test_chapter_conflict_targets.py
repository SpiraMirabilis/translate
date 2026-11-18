"""Out-of-sequence chapter conflicts offer the run's last chapter as the target.

Book 117, 2026-11-18: the run saved ch297, then the next queue item claimed
"290" (it was 298). "Append" stitched it onto ch290. The panel now offers
"Append to ch297" and "Translate as ch298", and a merge with a target chapter
appends there instead.
"""
import pytest

import settings_store
from test_chapter_conflict_gap_slide import FakeDB, FakeJob, make_ui as _make_ui

OLD = {"title": "Old 290", "untranslated": ["旧的二九零"], "content": ["Old 290."], "summary": "s290"}
LAST = {"title": "Last 297", "untranslated": ["二九七"], "content": ["Line 297."], "summary": "s297"}
INCOMING = ["寶藏在哪裡"]


class TargetDB(FakeDB):
    def max_translated_chapter(self, book_id):
        return max(self.stored) if self.stored else None


def make_ui(stored, decisions, queue=(), last_saved=None, current=290):
    ui = _make_ui(stored, queue, decisions, current=current)
    ui.entity_manager.__class__ = TargetDB
    if last_saved is not None:
        ui._last_saved_chapter = last_saved
    return ui


@pytest.fixture(autouse=True)
def no_auto(monkeypatch):
    # Neither Jev triage nor the gap slide may settle these conflicts.
    state = {"chapter_conflict_gap_slide": False, "jev_chapter_conflict": "off"}
    monkeypatch.setattr(settings_store, "get", lambda k, d=None: state.get(k, d))


def test_payload_offers_last_saved_chapter_and_next_number():
    ui = make_ui({290: OLD, 297: LAST}, [{"decision": "cancel"}], last_saved=297)
    assert ui.check_chapter_conflict(list(INCOMING)) is False
    p = ui.job_manager.pending_chapter_conflict
    assert p["append_target"] == 297
    assert p["append_target_title"] == "Last 297"
    assert p["next_number"] == 298


def test_falls_back_to_book_max_on_a_runs_first_item():
    ui = make_ui({290: OLD, 291: OLD, 297: LAST}, [{"decision": "cancel"}])
    ui.check_chapter_conflict(list(INCOMING))
    assert ui.job_manager.pending_chapter_conflict["append_target"] == 297


@pytest.mark.parametrize("last_saved", [289, 290])
def test_in_sequence_item_offers_nothing_extra(last_saved):
    """A retranslation run (last saved = N-1) keeps the plain append-to-N panel."""
    ui = make_ui({289: OLD, 290: OLD, 297: LAST}, [{"decision": "cancel"}], last_saved=last_saved)
    ui.check_chapter_conflict(list(INCOMING))
    p = ui.job_manager.pending_chapter_conflict
    assert "append_target" not in p and "next_number" not in p


def test_next_number_withheld_when_taken():
    queue = [{"id": 2, "book_id": 7, "chapter_number": 298, "position": 11}]
    ui = make_ui({290: OLD, 297: LAST}, [{"decision": "cancel"}], queue=queue, last_saved=297)
    ui.check_chapter_conflict(list(INCOMING))
    p = ui.job_manager.pending_chapter_conflict
    assert p["append_target"] == 297 and "next_number" not in p


def test_merge_into_target_appends_to_that_chapter():
    ui = make_ui({290: OLD, 297: LAST},
                 [{"decision": "merge", "new_chapter_number": 297}], last_saved=297)
    text = list(INCOMING)
    assert ui.check_chapter_conflict(text) is True
    assert ui.chapter_number == 297
    assert ui._current_queue_item["chapter_number"] == 297
    assert ui._merge_prefix["untranslated"] == LAST["untranslated"]
    assert ui._merge_prefix["translated"] == LAST["content"]
    assert ui._merge_prefix["title"] == "Last 297"
    assert text == INCOMING


def test_plain_merge_still_appends_to_the_conflicting_chapter():
    ui = make_ui({290: OLD, 297: LAST}, [{"decision": "merge"}], last_saved=297)
    assert ui.check_chapter_conflict(list(INCOMING)) is True
    assert ui.chapter_number == 290
    assert ui._merge_prefix["untranslated"] == OLD["untranslated"]


def test_merge_into_missing_chapter_reprompts():
    ui = make_ui({290: OLD, 297: LAST},
                 [{"decision": "merge", "new_chapter_number": 400}, {"decision": "cancel"}],
                 last_saved=297)
    assert ui.check_chapter_conflict(list(INCOMING)) is False
    assert "does not exist" in ui.job_manager.prompts[-1]["error"]
    assert ui.chapter_number == 290


def test_translate_as_next_number_uses_renumber_new():
    ui = make_ui({290: OLD, 297: LAST},
                 [{"decision": "renumber_new", "new_chapter_number": 298}], last_saved=297)
    assert ui.check_chapter_conflict(list(INCOMING)) is True
    assert ui.chapter_number == 298
    assert ui._merge_prefix is None


# -- sequence slide: settled without a prompt --------------------------------

@pytest.fixture
def slide_on(monkeypatch):
    state = {"chapter_conflict_gap_slide": True, "jev_chapter_conflict": "off"}
    monkeypatch.setattr(settings_store, "get", lambda k, d=None: state.get(k, d))
    return state


def test_new_item_after_last_saved_slides_to_next(slide_on):
    """Book 117: run saved 297, next item claims 290 -> translated as 298, no prompt."""
    ui = make_ui({290: OLD, 297: LAST}, [], last_saved=297)
    assert ui.check_chapter_conflict(list(INCOMING)) is True
    assert ui.chapter_number == 298
    assert ui._current_queue_item["chapter_number"] == 298
    assert ui.job_manager.pending_chapter_conflict is None
    assert ui.job_manager.prompts[0]["decision"] == "slide"
    assert "just saved chapter 297" in ui.job_manager.activity[0]["message"]


def test_retranslation_item_never_slides(slide_on):
    ui = make_ui({290: OLD, 297: LAST}, [{"decision": "cancel"}], last_saved=297)
    ui._current_queue_item["retranslation_reason"] = "Retranslation requested"
    assert ui.check_chapter_conflict(list(INCOMING)) is False
    assert ui.job_manager.pending_chapter_conflict["chapter_number"] == 290


def test_first_item_of_a_run_prompts(slide_on):
    ui = make_ui({290: OLD, 297: LAST}, [{"decision": "cancel"}])
    assert ui.check_chapter_conflict(list(INCOMING)) is False
    assert ui.job_manager.pending_chapter_conflict["append_target"] == 297


def test_taken_slot_prompts(slide_on):
    queue = [{"id": 2, "book_id": 7, "chapter_number": 298, "position": 11}]
    ui = make_ui({290: OLD, 297: LAST}, [{"decision": "cancel"}], queue=queue, last_saved=297)
    assert ui.check_chapter_conflict(list(INCOMING)) is False


def test_revised_repost_of_the_stored_chapter_prompts(slide_on):
    stored = {"title": "290", "untranslated": ["甲", "乙", "丙", "丁"], "content": ["x"], "summary": ""}
    ui = make_ui({290: stored, 297: LAST}, [{"decision": "cancel"}], last_saved=297)
    assert ui.check_chapter_conflict(["甲", "乙", "丙", "戊"]) is False
    assert ui.chapter_number == 290


def test_filler_incoming_never_slides(slide_on, monkeypatch):
    jev = {"incoming": {"choice": "filler", "confidence": 0.99}, "threshold": 0.9}
    ui = make_ui({290: OLD, 297: LAST}, [{"decision": "cancel"}], last_saved=297)
    monkeypatch.setattr(ui, "_jev_triage_conflict", lambda *a, **k: (None, jev))
    assert ui.check_chapter_conflict(list(INCOMING)) is False
