"""Per-chapter translator instructions on a queued chapter (queue.chapter_instructions).

The Queue page attaches free text to one queued chapter — typically how to
handle a passage most models refuse — and it is appended to that chapter's
translation prompt as INSTRUCTIONS FOR THIS CHAPTER.
"""
import os
import re

from tests.test_prompt_contract import _FootnoteConfig, LEGACY_PROMPT
from prompt_contract import strip_legacy_contract


# ---------------------------------------------------------------- storage

def _item(db, queue_id):
    for item in db.list_queue(include_processing=True):
        if item["id"] == queue_id:
            return item
    raise AssertionError(f"queue item {queue_id} not found")


def test_set_and_clear_round_trip(db):
    book_id = db.create_book("Instr")
    qid = db.add_to_queue(book_id, ["正文"], chapter_number=5)
    assert _item(db, qid)["chapter_instructions"] is None

    assert db.set_queue_item_instructions(qid, "  Leave the bath scene in Chinese.  ")
    assert _item(db, qid)["chapter_instructions"] == "Leave the bath scene in Chinese."

    assert db.set_queue_item_instructions(qid, "   ")
    assert _item(db, qid)["chapter_instructions"] is None


def test_unknown_item_is_false(db):
    assert db.set_queue_item_instructions(999999, "x") is False


def test_instructions_ride_the_claim_and_survive_a_release(db):
    """A refused chapter is released back to the queue; its instructions must
    still be there for the next attempt, and the claim must carry them."""
    book_id = db.create_book("Instr")
    qid = db.add_to_queue(book_id, ["正文"], chapter_number=5)
    db.set_queue_item_instructions(qid, "Skip the passage.")

    assert db.get_next_queue_item(book_id)["chapter_instructions"] == "Skip the passage."
    claimed = db.claim_next_queue_item(book_id, worker_id="test")
    assert claimed["id"] == qid
    assert claimed["chapter_instructions"] == "Skip the passage."

    # Editable while processing (takes effect on the next attempt).
    assert db.set_queue_item_instructions(qid, "Skip it, and mark the gap.")
    assert db.release_queue_item(qid)
    again = db.claim_next_queue_item(book_id, worker_id="test")
    assert again["chapter_instructions"] == "Skip it, and mark the gap."


def test_instructions_do_not_make_a_row_a_retranslation(db):
    """Unlike retranslation_reason, instructions must not change numbering:
    an unnumbered item behind an instructed one still continues the run."""
    book_id = db.create_book("Instr")
    first = db.add_to_queue(book_id, ["a"], chapter_number=40)
    db.set_queue_item_instructions(first, "Skip the passage.")
    nxt = db.add_to_queue(book_id, ["b"])
    assert _item(db, nxt)["chapter_number"] == 41
    assert _item(db, first)["retranslation_reason"] is None


# ---------------------------------------------------------------- prompt

def _prompt(**kw):
    from tests.conftest import FakeLogger
    from translation_engine import TranslationEngine

    class _EM:
        entities = {}
        config = _FootnoteConfig()

        def entities_inside_text(self, *a, **k):
            return {"exact": {}, "similar": {}}

    eng = TranslationEngine(_FootnoteConfig(), FakeLogger(), _EM())
    return eng.generate_system_prompt(
        ["陈元看着刍狗。"], {"characters": {}}, do_count=False,
        book_prompt_template=strip_legacy_contract(LEGACY_PROMPT),
        chapter_number=7, gendered_categories=["characters"], **kw)


def test_absent_unless_supplied():
    assert "INSTRUCTIONS FOR THIS CHAPTER" not in _prompt()
    assert "INSTRUCTIONS FOR THIS CHAPTER" not in _prompt(chapter_instructions="  ")


def test_appended_after_the_contract_and_the_retranslation_note():
    out = _prompt(retranslation_reason="Names were wrong.",
                  chapter_instructions="Leave the passage in Chinese.")
    assert out.count("INSTRUCTIONS FOR THIS CHAPTER:") == 1
    assert "Leave the passage in Chinese." in out
    assert out.index("RESPONSE FORMAT:") < out.index("RETRANSLATION NOTE:") \
        < out.index("INSTRUCTIONS FOR THIS CHAPTER:")


def test_reaches_both_passes_of_two_pass():
    for mode in ("entity_only", "translate_only"):
        assert "Skip it." in _prompt(mode=mode, chapter_instructions="Skip it.")


def test_every_prompt_build_in_the_engine_forwards_them():
    """translate_chapter rebuilds the prompt for every chunk after the first;
    that rebuild once silently dropped the retranslation reason. Every call
    must pass both."""
    src = open(os.path.join(os.path.dirname(__file__), "..",
                            "translation_engine.py"), encoding="utf-8").read()
    calls = []
    for m in re.finditer(r"self\.generate_system_prompt\(", src):
        depth, i = 0, m.end() - 1
        while True:
            c = src[i]
            depth += c == "("
            depth -= c == ")"
            i += 1
            if depth == 0:
                break
        calls.append(src[m.start():i])
    assert len(calls) >= 3
    for call in calls:
        assert "chapter_instructions=" in call, call
        assert "retranslation_reason=" in call, call


# ---------------------------------------------------------------- API

def test_put_endpoint(admin_client, db):
    book_id = db.create_book("Instr")
    qid = db.add_to_queue(book_id, ["正文"], chapter_number=5)

    r = admin_client.put(f"/api/queue/{qid}/instructions",
                         json={"instructions": " Skip the passage. "})
    assert r.status_code == 200, r.text
    assert r.json()["chapter_instructions"] == "Skip the passage."

    listed = admin_client.get(f"/api/queue?book_id={book_id}").json()["items"]
    assert listed[0]["chapter_instructions"] == "Skip the passage."

    r = admin_client.put(f"/api/queue/{qid}/instructions", json={"instructions": None})
    assert r.status_code == 200
    assert admin_client.get(f"/api/queue?book_id={book_id}").json()["items"][0][
        "chapter_instructions"] is None


def test_put_endpoint_rejects(admin_client, db):
    assert admin_client.put("/api/queue/999999/instructions",
                            json={"instructions": "x"}).status_code == 404
    book_id = db.create_book("Instr")
    qid = db.add_to_queue(book_id, ["正文"], chapter_number=5)
    assert admin_client.put(f"/api/queue/{qid}/instructions",
                            json={"instructions": "x" * 4001}).status_code == 422


def test_put_endpoint_requires_auth(api_client):
    assert api_client.put("/api/queue/1/instructions",
                          json={"instructions": "x"}).status_code == 401
