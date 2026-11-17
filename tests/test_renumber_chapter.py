"""DatabaseManager.renumber_chapter moves everything stamped with the chapter.

It used to move only the chapter, comments, logs, revisions and NU releases,
leaving the glossary's chapter stamps behind (book 166, 2026-11-16: shifting
ch233-247 up by one to make room for a chapter the scrape had dropped). A note
revision left at the old number is replayed by notes_as_of() as if the chapter
now holding that number made it.
"""


def _count(db, table, book_id, chapter, col="chapter_number"):
    with db._conn() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) FROM {table} WHERE book_id = ? AND {col} = ?",
                    (book_id, chapter))
        return cur.fetchone()[0]


def test_renumber_moves_chapter_stamped_rows(db):
    book = db.create_book("Book")
    db.save_chapter(book, 5, "Five", ["源文"], ["Text"])
    db.add_entity("characters", "张三", "Zhang San", book_id=book,
                  origin_chapter=5, last_chapter=5, gender="male",
                  note="first", note_author="model", note_chapter=5)
    eid = db.get_entity_id(book, "张三")
    db.set_entity_note(eid, "second", author="model", chapter_number=5)
    db.set_entity_gender(eid, "female", author="model", chapter_number=5)
    db.create_error_report({"book_id": book, "chapter_number": 5,
                            "report_type": "typo", "problem": "x"})
    db.record_footnote_scan(book, 5, "Five", "m", "h",
                            [{"term_zh": "张三", "term_en": "Zhang San",
                              "body": "b", "sentence": "s"}])
    # A stale scan row at the target number must not block the move.
    db.record_footnote_scan(book, 6, "Gone", "m", "h", [])

    ok, reason = db.renumber_chapter(book, 5, 6)
    assert ok, reason

    for table in ("entity_note_revisions", "entity_gender_revisions",
                  "error_reports", "footnote_candidates", "footnote_scans"):
        assert _count(db, table, book, 5) == 0, table
        assert _count(db, table, book, 6) >= 1, table
    assert _count(db, "footnote_scans", book, 6) == 1
    ent = db.get_entity_by_id(eid)
    assert int(ent["origin_chapter"]) == 6 and int(ent["last_chapter"]) == 6


def test_renumber_leaves_other_books_alone(db):
    a, b = db.create_book("A"), db.create_book("B")
    for book in (a, b):
        db.save_chapter(book, 5, "Five", ["源文"], ["Text"])
        db.add_entity("characters", "李四", "Li Si", book_id=book,
                      origin_chapter=5, last_chapter=5)
    ok, _ = db.renumber_chapter(a, 5, 9)
    assert ok
    other = db.get_entity_by_id(db.get_entity_id(b, "李四"))
    assert int(other["origin_chapter"]) == 5 and int(other["last_chapter"]) == 5
