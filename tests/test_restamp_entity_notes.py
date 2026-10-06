"""restamp_entity_notes.py — moving a late note's history to first appearance.

The failure it fixes: a convention note created at a late chapter does not
exist before that chapter under notes_as_of. Each test checks the point-in-time
answer, not just the rows, since that is what the reader panel and a
retranslation see.
"""
from restamp_entity_notes import apply_entry, check_entry, verify


def _seed(db):
    book_id = db.create_book("Restamp Book")
    db.add_entity("titles", "宗主", "Sect Master", book_id=book_id, origin_chapter=3)
    eid = db.get_entity_id(book_id, "宗主")
    db.set_entity_note(eid, "Render 'Sect Master'. Xiong's sect burned at ch702.",
                       author="human", chapter_number=709, reason="ch709 pass")
    return book_id, eid


def _at(db, book_id, eid, ch):
    return db.notes_as_of(book_id, ch, entity_ids=[eid]).get(eid)


def test_late_stamp_is_invisible_before_the_fix(db):
    book_id, eid = _seed(db)
    assert _at(db, book_id, eid, 100) is None


def test_split_gives_early_text_then_late_text(db):
    book_id, eid = _seed(db)
    p, why = check_entry(db, book_id, {"untranslated": "宗主", "chapter": 3,
                                       "early_note": "Render 'Sect Master'."})
    assert why is None
    apply_entry(db, p)
    assert verify(db, book_id, p) == []

    assert _at(db, book_id, eid, 2) is None                    # before first appearance
    assert _at(db, book_id, eid, 3) == "Render 'Sect Master'."
    assert _at(db, book_id, eid, 708) == "Render 'Sect Master'."  # no ch702 spoiler
    assert _at(db, book_id, eid, 709).endswith("ch702.")

    revs = db.list_note_revisions(book_id=book_id, entity_id=eid)
    chapters = sorted((r["id"], r["chapter_number"]) for r in revs)
    assert [c for _, c in chapters] == [3, 709]                 # ids ascend with chapters


def test_move_keeps_text(db):
    book_id, eid = _seed(db)
    p, _ = check_entry(db, book_id, {"untranslated": "宗主", "chapter": 3})
    apply_entry(db, p)
    assert _at(db, book_id, eid, 3).startswith("Render")
    assert len(db.list_note_revisions(book_id=book_id, entity_id=eid)) == 1


def test_guards(db):
    book_id, eid = _seed(db)
    # Before origin_chapter: the floor would hide it.
    assert "origin_chapter" in check_entry(db, book_id, {"untranslated": "宗主",
                                                         "chapter": 2})[1]
    # Not earlier than the existing history.
    assert "not earlier" in check_entry(db, book_id, {"untranslated": "宗主",
                                                      "chapter": 709})[1]
    # Unknown entity.
    assert check_entry(db, book_id, {"untranslated": "无名", "chapter": 3})[1]
    # Older history: earliest row is not a creation.
    db.add_entity("titles", "长老", "Elder", book_id=book_id, origin_chapter=1,
                  note="Legacy note.")
    e2 = db.get_entity_id(book_id, "长老")
    with db._conn() as conn:
        conn.cursor().execute("DELETE FROM entity_note_revisions WHERE entity_id = ?", (e2,))
    db.set_entity_note(e2, "Revised.", author="model", chapter_number=50)
    assert "not a creation" in check_entry(db, book_id, {"untranslated": "长老",
                                                         "chapter": 1})[1]


def test_stamps_build_a_chapter_ordered_history(db):
    book_id, eid = _seed(db)
    entry = {"untranslated": "宗主", "stamps": [
        {"chapter": 3, "note": "Stamp A."},
        {"chapter": 200, "note": "Stamp B."},
        {"chapter": 500, "note": "Stamp C."}]}
    p, why = check_entry(db, book_id, entry)
    assert why is None
    apply_entry(db, p)
    assert verify(db, book_id, p) == []

    assert _at(db, book_id, eid, 2) is None
    assert _at(db, book_id, eid, 3) == "Stamp A."
    assert _at(db, book_id, eid, 199) == "Stamp A."
    assert _at(db, book_id, eid, 200) == "Stamp B."
    assert _at(db, book_id, eid, 708) == "Stamp C."
    assert _at(db, book_id, eid, 709).endswith("ch702.")

    revs = sorted(db.list_note_revisions(book_id=book_id, entity_id=eid),
                  key=lambda r: r["id"])
    assert [r["chapter_number"] for r in revs] == [3, 200, 500, 709]
    assert [r["previous_note"] for r in revs][:2] == [None, "Stamp A."]


def test_stamps_guards(db):
    book_id, _ = _seed(db)
    bad_order = {"untranslated": "宗主", "stamps": [{"chapter": 200, "note": "B"},
                                                   {"chapter": 3, "note": "A"}]}
    assert "ascending" in check_entry(db, book_id, bad_order)[1]
    too_late = {"untranslated": "宗主", "stamps": [{"chapter": 3, "note": "A"},
                                                  {"chapter": 709, "note": "B"}]}
    assert "not before" in check_entry(db, book_id, too_late)[1]
    same = {"untranslated": "宗主", "stamps": [{"chapter": 3, "note": "A"},
                                              {"chapter": 9, "note": "A"}]}
    assert "same text" in check_entry(db, book_id, same)[1]


def test_stamps_replace_legacy_only_when_asked(db):
    book_id = db.create_book("Legacy Book")
    db.add_entity("characters", "白蛇", "White Snake", book_id=book_id, origin_chapter=5,
                  note="Spoiler: secretly a dragon.")
    eid = db.get_entity_id(book_id, "白蛇")
    with db._conn() as conn:     # a note from before the revision log existed
        conn.cursor().execute("DELETE FROM entity_note_revisions WHERE entity_id = ?", (eid,))
    db.set_entity_note(eid, "Revealed as a dragon.", author="model", chapter_number=600)
    assert _at(db, book_id, eid, 100) == "Spoiler: secretly a dragon."

    entry = {"untranslated": "白蛇", "stamps": [{"chapter": 5, "note": "A white snake."}]}
    assert "predates the log" in check_entry(db, book_id, entry)[1]

    p, why = check_entry(db, book_id, dict(entry, replace_legacy=True))
    assert why is None and p["discarded_legacy"] == "Spoiler: secretly a dragon."
    apply_entry(db, p)
    assert verify(db, book_id, p) == []
    assert _at(db, book_id, eid, 4) is None
    assert _at(db, book_id, eid, 100) == "A white snake."
    assert _at(db, book_id, eid, 600) == "Revealed as a dragon."


def test_insert_fills_a_gap_between_stamps(db):
    book_id, eid = _seed(db)
    p, _ = check_entry(db, book_id, {"untranslated": "宗主", "stamps": [
        {"chapter": 3, "note": "Early."}, {"chapter": 100, "note": "Mid."}]})
    apply_entry(db, p)

    p, why = check_entry(db, book_id, {"untranslated": "宗主", "insert": [
        {"chapter": 400, "note": "Gap B."}, {"chapter": 250, "note": "Gap A."}]})
    assert why is None
    apply_entry(db, p)
    assert verify(db, book_id, p) == []

    assert _at(db, book_id, eid, 99) == "Early."
    assert _at(db, book_id, eid, 249) == "Mid."
    assert _at(db, book_id, eid, 250) == "Gap A."
    assert _at(db, book_id, eid, 399) == "Gap A."
    assert _at(db, book_id, eid, 400) == "Gap B."
    assert _at(db, book_id, eid, 708) == "Gap B."
    assert _at(db, book_id, eid, 709).endswith("ch702.")

    revs = sorted(db.list_note_revisions(book_id=book_id, entity_id=eid), key=lambda r: r["id"])
    assert [r["chapter_number"] for r in revs] == [3, 100, 250, 400, 709]
    for a, b in zip(revs, revs[1:]):              # the chain is intact
        assert b["previous_note"] == a["new_note"]


def test_insert_guards(db):
    book_id, _ = _seed(db)
    p, _ = check_entry(db, book_id, {"untranslated": "宗主", "stamps": [
        {"chapter": 3, "note": "Early."}]})
    apply_entry(db, p)
    for ch, want in ((2, "strictly between"), (800, "strictly between")):
        assert want in check_entry(db, book_id, {"untranslated": "宗主", "insert": [
            {"chapter": ch, "note": "X."}]})[1]
    assert "repeats" in check_entry(db, book_id, {"untranslated": "宗主", "insert": [
        {"chapter": 50, "note": "Early."}]})[1]
