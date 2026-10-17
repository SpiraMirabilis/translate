"""Inline footnote candidates placed straight into the chapter (auto-apply).

The translator that flagged a referent also wrote the English it hangs on, so
in scan_mode "translation" its candidates become real footnotes ("applied")
without a reviewer locating the anchor. Anything that can't be placed cleanly
stays a pending candidate, and the review curates the placed ones: keep leaves
the footnote, reject takes it back out.
"""
import pytest

import footnote_scan_core as core
from footnote_scan_core import CandidateActionError


SOURCE = "第三章\n陈元看着刍狗，心中一动。\n他想起了唐僧肉。\n铁公鸡一毛不拔。"
STRAW = {"term_zh": "刍狗", "term_en": "straw dogs", "body": "From the Daodejing.",
         "sentence": "陈元看着刍狗，心中一动。"}
TANG = {"term_zh": "唐僧肉", "term_en": "Tang Monk's flesh",
        "body": "Journey to the West.", "sentence": "他想起了唐僧肉。"}
ROOSTER = {"term_zh": "铁公鸡", "term_en": "iron rooster", "body": "A skinflint.",
           "sentence": "铁公鸡一毛不拔。"}

TRANSLATION = [
    "Chapter 3",
    "Chen Yuan looked at the straw dogs and felt something stir.",
    "He remembered Tang Monk's flesh.",
    "The iron rooster would not part with a single feather.",
]


def _chapter(db, title="Book", translation=TRANSLATION, number=3):
    book_id = db.create_book(title, "Author")
    chapter_id = db.save_chapter(book_id, number, "Chapter 3",
                                 SOURCE.split("\n"), list(translation))
    return book_id, chapter_id


def _store(db, book_id, chapter_id, found, cap=4, number=3):
    core.persist_inline_candidates(
        db, book_id, number, "Chapter 3", "test:model", SOURCE, found)
    # As ui.py does: the re-anchor step sees the translator's whole list.
    return core.apply_inline_candidates(db, book_id, number, chapter_id,
                                        incoming=found, cap=cap)


def _content(db, chapter_id):
    return db.get_chapter(chapter_id=chapter_id)["content"]


def _rows(db, book_id):
    return {r["term_zh"]: r for r in db.list_footnote_candidates(book_id)}


# ── placement ────────────────────────────────────────────────────────────────

def test_a_candidate_whose_term_is_in_the_prose_is_placed(db):
    book_id, chapter_id = _chapter(db)
    res = _store(db, book_id, chapter_id, [STRAW])
    assert [r["term_zh"] for r in res["placed"]] == ["刍狗"]

    row = _rows(db, book_id)["刍狗"]
    assert row["status"] == "applied" and row["footnote_id"]
    fn = db.get_book_footnotes(book_id)[0]
    assert fn["id"] == row["footnote_id"]
    assert fn["anchor"] == "straw dogs" and fn["source_term"] == "刍狗"
    assert fn["status"] == "active"
    content = _content(db, chapter_id)
    assert "straw dogs[1]" in "\n".join(content)
    assert content[-1] == "[1] From the Daodejing."


def test_a_term_missing_from_the_translation_stays_pending(db):
    book_id, chapter_id = _chapter(db)
    res = _store(db, book_id, chapter_id, [dict(STRAW, term_en="Straw Dogs")])
    assert res["placed"] == []
    assert res["unplaced"][0][1] == "term not found in the translation"
    row = _rows(db, book_id)["刍狗"]
    assert row["status"] == "pending" and row["footnote_id"] is None
    assert db.get_book_footnotes(book_id) == []


def test_the_heading_never_takes_the_marker(db):
    translation = ["Chapter 3: straw dogs"] + TRANSLATION[1:]
    book_id, chapter_id = _chapter(db, translation=translation)
    _store(db, book_id, chapter_id, [STRAW])
    fn = db.get_book_footnotes(book_id)[0]
    assert fn["occurrence"] == 2
    content = _content(db, chapter_id)
    assert content[0] == "Chapter 3: straw dogs"
    assert "the straw dogs[1] and" in content[1]


def test_a_term_only_in_the_heading_is_not_placed(db):
    translation = ["Chapter 3: straw dogs", "Nothing here."]
    book_id, chapter_id = _chapter(db, translation=translation)
    res = _store(db, book_id, chapter_id, [STRAW])
    assert res["placed"] == []
    assert _rows(db, book_id)["刍狗"]["status"] == "pending"


def test_a_prose_line_0_takes_the_marker(db):
    # Most chapters store no heading line: line 0 is the first paragraph.
    translation = ["Chen Yuan looked at the straw dogs.", "Nothing here."]
    book_id, chapter_id = _chapter(db, translation=translation)
    res = _store(db, book_id, chapter_id, [STRAW])
    assert [r["term_zh"] for r in res["placed"]] == ["刍狗"]
    assert db.get_book_footnotes(book_id)[0]["occurrence"] == 1
    assert _content(db, chapter_id)[0] == "Chen Yuan looked at the straw dogs[1]."


def test_the_cap_places_in_reading_order_and_leaves_the_rest_pending(db):
    book_id, chapter_id = _chapter(db)
    # Model order is the reverse of reading order.
    res = _store(db, book_id, chapter_id, [ROOSTER, TANG, STRAW], cap=2)
    assert [r["term_zh"] for r in res["placed"]] == ["刍狗", "唐僧肉"]
    rows = _rows(db, book_id)
    assert rows["铁公鸡"]["status"] == "pending"
    assert "cap" in res["unplaced"][0][1]
    assert "[1]" in _content(db, chapter_id)[1] and "[2]" in _content(db, chapter_id)[2]


def test_cap_zero_places_nothing(db):
    book_id, chapter_id = _chapter(db)
    res = _store(db, book_id, chapter_id, [STRAW], cap=0)
    assert res["placed"] == [] and db.get_book_footnotes(book_id) == []


def test_a_hand_placed_footnote_on_the_same_anchor_is_never_overwritten(db):
    """add_footnote upserts on (chapter, anchor, occurrence) — placing over an
    existing anchor would silently rewrite a human's note."""
    book_id, chapter_id = _chapter(db)
    db.add_footnote(book_id, chapter_id, "iron rooster", "Hand-written note.")
    db.rerender_chapter_footnotes(chapter_id)
    res = _store(db, book_id, chapter_id, [ROOSTER])
    assert res["placed"] == []
    assert res["unplaced"][0][1] == "already footnoted in this book"
    assert db.get_book_footnotes(book_id)[0]["body"] == "Hand-written note."


def test_the_same_body_already_in_the_book_is_not_placed_again(db):
    book_id, chapter_id = _chapter(db)
    db.add_footnote(book_id, chapter_id, "Chen Yuan", STRAW["body"])
    res = _store(db, book_id, chapter_id, [STRAW])
    assert res["unplaced"][0][1] == "the same note is already in this book"


def test_auto_apply_off_leaves_everything_pending(db, monkeypatch):
    book_id, chapter_id = _chapter(db)
    monkeypatch.setattr(core, "auto_apply_settings", lambda *a, **k: (False, 4))
    kept = core.persist_inline_candidates(
        db, book_id, 3, "Chapter 3", "test:model", SOURCE, [STRAW])
    res = core.apply_inline_candidates(db, book_id, 3, chapter_id, incoming=kept)
    assert res == {"placed": [], "unplaced": [], "reanchored": []}
    assert _rows(db, book_id)["刍狗"]["status"] == "pending"
    assert db.get_book_footnotes(book_id) == []


def test_settings_default_on_with_a_cap_of_four(db):
    book_id = db.create_book("Settings", "Author")
    ctx = {"module_settings": {"footnote_scan": {}}}
    assert core.auto_apply_settings(db, book_id, ctx=ctx) == (True, 4)
    ctx = {"module_settings": {"footnote_scan": {"auto_apply": False,
                                                 "auto_apply_max": "2"}}}
    assert core.auto_apply_settings(db, book_id, ctx=ctx) == (False, 2)


# ── retranslation ────────────────────────────────────────────────────────────

def test_retranslation_keeps_a_placed_row_and_does_not_place_twice(db):
    book_id, chapter_id = _chapter(db)
    _store(db, book_id, chapter_id, [STRAW])
    db.save_chapter(book_id, 3, "Chapter 3", SOURCE.split("\n"), list(TRANSLATION))
    res = _store(db, book_id, chapter_id, [dict(STRAW, body="Rewritten.")])
    assert res["placed"] == []
    rows = db.list_footnote_candidates(book_id)
    assert len(rows) == 1 and rows[0]["status"] == "applied"
    assert len(db.get_book_footnotes(book_id)) == 1


def test_retranslation_reanchors_an_orphaned_placement(db):
    book_id, chapter_id = _chapter(db)
    _store(db, book_id, chapter_id, [STRAW])
    reworded = list(TRANSLATION)
    reworded[1] = "Chen Yuan looked at the straw effigies and felt something stir."
    db.save_chapter(book_id, 3, "Chapter 3", SOURCE.split("\n"), reworded)
    assert db.get_book_footnotes(book_id)[0]["status"] == "orphaned"

    res = _store(db, book_id, chapter_id, [dict(STRAW, term_en="straw effigies")])
    assert [r["term_zh"] for r in res["reanchored"]] == ["刍狗"]
    fn = db.get_book_footnotes(book_id)[0]
    assert fn["anchor"] == "straw effigies" and fn["status"] == "active"
    assert "straw effigies[1]" in _content(db, chapter_id)[1]
    assert _rows(db, book_id)["刍狗"]["term_en"] == "straw effigies"


def test_a_replace_all_rescan_never_drops_a_placed_row(db):
    """Without preserve_reviewed a rescan replaces the chapter's rows — but a
    row that owns a footnote must survive, or nothing could take it back out."""
    book_id, chapter_id = _chapter(db)
    _store(db, book_id, chapter_id, [STRAW, TANG], cap=1)
    db.record_footnote_scan(book_id, 3, "t", "test:model", "h2", [STRAW, ROOSTER])
    rows = _rows(db, book_id)
    assert set(rows) == {"刍狗", "铁公鸡"}
    assert rows["刍狗"]["status"] == "applied"


# ── curation ─────────────────────────────────────────────────────────────────

def _placed(db):
    book_id, chapter_id = _chapter(db)
    _store(db, book_id, chapter_id, [STRAW])
    return book_id, chapter_id, _rows(db, book_id)["刍狗"]


def test_keep_leaves_the_footnote(db):
    book_id, chapter_id, row = _placed(db)
    assert core.decide_candidates(db, [row["id"]], "accepted") == 1
    after = db.get_footnote_candidate(row["id"])
    assert after["status"] == "accepted" and after["footnote_id"] == row["footnote_id"]
    assert "[1]" in _content(db, chapter_id)[1]


def test_reject_removes_the_footnote_from_the_chapter(db):
    book_id, chapter_id, row = _placed(db)
    core.decide_candidates(db, [row["id"]], "rejected")
    after = db.get_footnote_candidate(row["id"])
    assert after["status"] == "rejected" and after["footnote_id"] is None
    assert db.get_book_footnotes(book_id) == []
    assert _content(db, chapter_id) == TRANSLATION


def test_unmark_on_a_placed_row_returns_it_to_applied(db):
    book_id, chapter_id, row = _placed(db)
    core.decide_candidates(db, [row["id"]], "accepted")
    core.decide_candidates(db, [row["id"]], "pending")
    assert db.get_footnote_candidate(row["id"])["status"] == "applied"


def test_applied_is_not_a_decision(db):
    _book, _ch, row = _placed(db)
    with pytest.raises(CandidateActionError):
        core.decide_candidates(db, [row["id"]], "applied")


def test_a_footnote_deleted_by_hand_reads_as_missing(db):
    book_id, chapter_id, row = _placed(db)
    db.delete_footnote(row["footnote_id"])
    by_id = {f["id"]: f for f in db.get_book_footnotes(book_id)}
    assert core.footnote_link_status(row, by_id) == "missing"
    core.decide_candidates(db, [row["id"]], "rejected")
    assert db.get_footnote_candidate(row["id"])["footnote_id"] is None


def test_editing_the_body_rewrites_the_footnote(db):
    book_id, chapter_id, row = _placed(db)
    core.edit_candidate(db, row["id"], body="Laozi, chapter 5.")
    assert db.get_book_footnotes(book_id)[0]["body"] == "Laozi, chapter 5."
    assert _content(db, chapter_id)[-1] == "[1] Laozi, chapter 5."
    assert db.get_footnote_candidate(row["id"])["body"] == "Laozi, chapter 5."


def test_editing_the_term_reanchors_or_refuses(db):
    book_id, chapter_id, row = _placed(db)
    core.edit_candidate(db, row["id"], term_en="Chen Yuan")
    fn = db.get_book_footnotes(book_id)[0]
    assert fn["anchor"] == "Chen Yuan" and fn["status"] == "active"
    assert _content(db, chapter_id)[1].startswith("Chen Yuan[1] looked")

    with pytest.raises(CandidateActionError):
        core.edit_candidate(db, row["id"], term_en="nowhere in the text")
    assert db.get_book_footnotes(book_id)[0]["anchor"] == "Chen Yuan"
    assert db.get_footnote_candidate(row["id"])["term_en"] == "Chen Yuan"


def test_place_puts_a_corrected_candidate_in(db):
    book_id, chapter_id = _chapter(db)
    _store(db, book_id, chapter_id, [dict(STRAW, term_en="Straw Dogs")])
    row = _rows(db, book_id)["刍狗"]
    with pytest.raises(CandidateActionError, match="not found"):
        core.place_candidate(db, row["id"])
    core.edit_candidate(db, row["id"], term_en="straw dogs")
    placed = core.place_candidate(db, row["id"])
    assert placed["status"] == "accepted" and placed["footnote_id"]
    assert "straw dogs[1]" in _content(db, chapter_id)[1]
    with pytest.raises(CandidateActionError, match="already placed"):
        core.place_candidate(db, row["id"])


def test_export_skips_placed_rows(db):
    from footnote_scan import build_export_map
    book_id, chapter_id = _chapter(db)
    _store(db, book_id, chapter_id, [STRAW, ROOSTER], cap=1)
    mapping, warnings = build_export_map(db, book_id)
    assert mapping == {"iron rooster": ROOSTER["body"]}
    assert warnings["skipped_already"] == []


# ── web API ──────────────────────────────────────────────────────────────────

def test_api_lists_link_status_and_rejects_through_the_helper(db, admin_client):
    book_id, chapter_id, row = _placed(db)
    data = admin_client.get(f"/api/books/{book_id}/footnote-candidates?status=applied").json()
    [listed] = data["candidates"]
    assert listed["footnote_status"] == "active" and listed["already"] is False

    r = admin_client.put(f"/api/footnote-candidates/{row['id']}", json={"status": "applied"})
    assert r.status_code == 422
    r = admin_client.put(f"/api/footnote-candidates/{row['id']}", json={"term_en": "absent"})
    assert r.status_code == 422

    r = admin_client.post(f"/api/books/{book_id}/footnote-candidates/batch",
                          json={"ids": [row["id"]], "status": "rejected"})
    assert r.json()["updated"] == 1
    assert db.get_book_footnotes(book_id) == []


def test_api_place(db, admin_client):
    book_id, chapter_id = _chapter(db)
    _store(db, book_id, chapter_id, [STRAW], cap=0)
    row = _rows(db, book_id)["刍狗"]
    r = admin_client.post(f"/api/footnote-candidates/{row['id']}/place")
    assert r.status_code == 200 and r.json()["candidate"]["status"] == "accepted"
    r = admin_client.post(f"/api/footnote-candidates/{row['id']}/place")
    assert r.status_code == 422
