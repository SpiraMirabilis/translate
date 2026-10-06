"""entity_note_coverage.py — coverage judged at each entity's target chapter.

The point of the tool is that "has a note" and "the note is in force where it
is needed" are different questions under notes_as_of's rewind. These tests pin
the status rules against real DB state rather than hand-built rows.
"""
from entity_note_coverage import compute_coverage, note_in_force, worklist


def _save(db, book_id, num, source):
    return db.save_chapter(
        book_id=book_id, chapter_number=num, title=f"Chapter {num}",
        untranslated_content=source, translated_content=[f"Line {num}"])


def _rows(db, book_id, **kw):
    cov = compute_coverage(db, book_id, **kw)
    return cov, {r["untranslated"]: r for r in cov["rows"]}


def _seed(db):
    book_id = db.create_book("Coverage Book")
    # Convention whose only note was stamped late: in force from ch60 only.
    db.add_entity("places", "青云宗", "Azure Cloud Sect", book_id=book_id,
                  origin_chapter=1)
    # Convention with no note at all.
    db.add_entity("places", "北海", "Northern Sea", book_id=book_id, origin_chapter=1)
    # Character noted at an early chapter — still in force at its last appearance.
    db.add_entity("characters", "陈元", "Chen Yuan", book_id=book_id, origin_chapter=1,
                  gender="male", note="Protagonist.", note_author="model",
                  note_chapter=1)
    # Character, no note, gone quiet after ch2.
    db.add_entity("characters", "李四", "Li Si", book_id=book_id, origin_chapter=1,
                  gender="male")
    # Convention whose origin_chapter post-dates its first appearance.
    db.add_entity("titles", "宗主", "Sect Master", book_id=book_id, origin_chapter=50,
                  note="Head of a sect.", note_author="script", note_chapter=1)
    # Never appears in any chapter.
    db.add_entity("creatures", "火蛟", "Flame Dragon", book_id=book_id, origin_chapter=1)

    _save(db, book_id, 1, ["陈元走进青云宗，宗主在北海。李四跟着。"])
    _save(db, book_id, 2, ["陈元、李四。"])
    _save(db, book_id, 60, ["陈元回到青云宗，看见北海。"])

    eid = db.get_entity_id(book_id, "青云宗")
    db.set_entity_note(eid, "An orthodox sect.", author="script", chapter_number=60,
                       reason="test")
    return book_id


def test_note_in_force_mirrors_notes_as_of(db):
    book_id = _seed(db)
    cov, rows = _rows(db, book_id)
    for ch in (1, 2, 60):
        live = db.notes_as_of(book_id, ch)
        for r in cov["rows"]:
            with db._conn() as conn:
                cur = conn.cursor()
                cur.execute("SELECT chapter_number, previous_note FROM entity_note_revisions "
                            "WHERE entity_id = ? ORDER BY id", (r["id"],))
                revs = cur.fetchall()
            assert note_in_force(r["note"], r["origin_chapter"], revs, ch) == live.get(r["id"])


def test_statuses(db):
    book_id = _seed(db)
    cov, rows = _rows(db, book_id, quiet_window=50)

    assert cov["head_chapter"] == 60 and cov["quiet_before"] == 10

    sect = rows["青云宗"]
    assert sect["kind"] == "convention" and sect["target_chapter"] == 1
    assert sect["note"] and sect["note_at_target"] is None
    assert sect["status"] == "blocked"          # a ch1 stamp would sit behind ch60's
    assert sect["latest_revision_chapter"] == 60

    assert rows["北海"]["status"] == "todo"

    hero = rows["陈元"]
    assert hero["kind"] == "stateful" and hero["target_chapter"] == 60
    assert hero["status"] == "ok" and not hero["quiet"]

    li = rows["李四"]
    assert li["status"] == "todo" and li["quiet"] and li["last_seen"] == 2

    master = rows["宗主"]
    assert master["origin_after_first"] and master["status"] == "todo"

    unseen = rows["火蛟"]
    assert unseen["spread"] == 0 and unseen["status"] is None


def test_stateful_override(db):
    book_id = _seed(db)
    _, rows = _rows(db, book_id, stateful_categories=["places"])
    assert rows["青云宗"]["kind"] == "stateful"
    assert rows["青云宗"]["status"] == "ok"      # ch60 note is in force at last appearance
    assert rows["陈元"]["kind"] == "convention"


def test_worklist_ranks_quiet_first(db):
    book_id = _seed(db)
    cov, _ = _rows(db, book_id)
    work = worklist(cov["rows"], min_chapters=1)
    keys = [r["untranslated"] for r in work]
    assert keys[0] == "李四"                     # quiet beats a wider spread
    assert "陈元" not in keys and "火蛟" not in keys
    assert [r["untranslated"] for r in worklist(cov["rows"], min_chapters=1,
                                                 statuses=("blocked",))] == ["青云宗"]
