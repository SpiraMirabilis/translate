"""Chapter-conflict resolution in WebInterface.check_chapter_conflict.

"Append & translate new part" combines both sides into one chapter at the same
number: the incoming source is appended to the existing source and its
translation is appended to the existing translation. Two regressions these
guard:

* The incoming text must never *replace* the existing chapter. Book 69 lost
  story chapters 134 and 179 that way, back when a merge whose incoming text
  shared no lines with the stored source fell through to translating the full
  incoming text with no stashed prefix.
* Sharing no lines is not an error — it's the ordinary case for an
  independently queued continuation (part 2 of a split chapter, an author's
  note). Merge must append it whole, not refuse and demand overwrite/renumber.
"""
import pytest

from conftest import FakeLogger
from web.services.web_interface import WebInterface


class FakeJob:
    """Feeds a scripted sequence of conflict decisions and records prompts."""

    def __init__(self, decisions):
        self._decisions = list(decisions)
        self.prompts = []          # payload per re-prompt
        self.activity = []
        self.pending_chapter_conflict = None
        self.status = "running"

    def await_prompt(self, kind, payload):
        # Mirrors Job.await_prompt: payload and status move as one, so no
        # snapshot can describe a job as running with nothing pending.
        assert kind == "chapter_conflict"
        self.pending_chapter_conflict = payload
        self.status = "awaiting_chapter_conflict"

    def send_message_sync(self, msg):
        self.prompts.append(msg)

    def log_activity(self, **kwargs):
        self.activity.append(kwargs)

    def wait_for_chapter_conflict(self):
        if not self._decisions:
            raise AssertionError("ran out of scripted decisions — loop did not settle")
        return self._decisions.pop(0)


class FakeEntityManager:
    def __init__(self, existing):
        self._existing = existing

    def get_book(self, book_id=None):
        return {"title": "Test Book"}

    def get_chapter(self, book_id=None, chapter_number=None):
        return self._existing


def make_ui(existing, decisions):
    """Build a WebInterface without running its dependency-heavy __init__."""
    ui = WebInterface.__new__(WebInterface)
    ui.book_id = 69
    ui.chapter_number = 134
    ui.logger = FakeLogger()
    ui.entity_manager = FakeEntityManager(existing)
    ui.job_manager = FakeJob(decisions)
    ui._merge_prefix = None
    return ui


EXISTING = {
    "title": "The Tables Turned",
    "untranslated": ["第一行", "", "第二行"],
    "content": ["Line one.", "", "Line two."],
    "summary": "s",
}


def test_diverged_merge_appends_the_incoming_item_whole():
    """Incoming text sharing no lines is appended, not refused and not swapped in."""
    part_two = ["12月4日，阴天", "", "请假一天。"]
    ui = make_ui(EXISTING, [{"decision": "merge"}])

    chapter_text = list(part_two)
    assert ui.check_chapter_conflict(chapter_text) is True

    # The whole incoming item is what gets translated...
    assert chapter_text == part_two
    # ...and the existing chapter rides along as the prefix, so ui.py stitches
    # the two together instead of replacing anything.
    assert ui._merge_prefix["untranslated"] == EXISTING["untranslated"]
    assert ui._merge_prefix["translated"] == EXISTING["content"]
    # One prompt only — divergence is not an error worth re-asking about.
    assert len(ui.job_manager.prompts) == 1


def test_overwrite_replaces_without_stashing_a_prefix():
    """Overwrite is the deliberate replace — it must not stitch the old text on."""
    incoming = ["完全不同的内容"]
    ui = make_ui(EXISTING, [{"decision": "proceed"}])

    chapter_text = list(incoming)
    assert ui.check_chapter_conflict(chapter_text) is True
    assert chapter_text == incoming      # overwrite keeps the incoming text
    assert ui._merge_prefix is None      # and stashes no prefix


def test_clean_append_merge_still_isolates_the_new_segment():
    """The genuine append case must keep working: translate only the remainder."""
    incoming = ["第一行", "", "第二行", "", "新的一行"]
    ui = make_ui(EXISTING, [{"decision": "merge"}])

    chapter_text = list(incoming)
    assert ui.check_chapter_conflict(chapter_text) is True

    # Only the appended tail is handed to the translator...
    assert [l for l in chapter_text if l.strip()] == ["新的一行"]
    # ...and the already-translated body is stashed for re-stitching at save.
    assert ui._merge_prefix["translated"] == EXISTING["content"]
    assert len(ui.job_manager.prompts) == 1


def test_identical_source_proceeds_without_prompting():
    """A legitimate retranslation of unchanged source must not prompt at all."""
    ui = make_ui(EXISTING, [])
    chapter_text = list(EXISTING["untranslated"])

    assert ui.check_chapter_conflict(chapter_text) is True
    assert ui.job_manager.prompts == []
