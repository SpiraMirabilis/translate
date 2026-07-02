"""Tests for chapter revision snapshots (db/revisions_repo.py)."""


def _make_book_chapter(db, title="Rev Book"):
    book_id = db.create_book(title=title, is_original=True)
    db.save_chapter(
        book_id=book_id, chapter_number=1, title="Chapter 1",
        untranslated_content=[], translated_content=["First line.", "", "Second paragraph."])
    return book_id


def test_add_and_list_revisions(db):
    book_id = _make_book_chapter(db)
    rev_id = db.add_chapter_revision(book_id, 1, "Chapter 1",
                                     ["First line.", "", "Second paragraph."], kind='manual')
    assert rev_id

    revisions = db.list_chapter_revisions(book_id, 1)
    assert len(revisions) == 1
    meta = revisions[0]
    assert meta["id"] == rev_id
    assert meta["kind"] == "manual"
    assert meta["title"] == "Chapter 1"
    assert meta["word_count"] == 4  # "First line." + "Second paragraph."
    assert meta["created_at"]
    assert "content" not in meta  # list is metadata-only


def test_get_revision_decodes_content(db):
    book_id = _make_book_chapter(db)
    lines = ["Alpha", "", "Beta gamma"]
    rev_id = db.add_chapter_revision(book_id, 1, "T", lines, kind='auto')

    rev = db.get_chapter_revision(rev_id)
    assert rev["content"] == lines
    assert rev["kind"] == "auto"
    assert rev["book_id"] == book_id
    assert rev["chapter_number"] == 1

    assert db.get_chapter_revision(999999) is None


def test_latest_revision_time(db):
    book_id = _make_book_chapter(db)
    assert db.latest_revision_time(book_id, 1) is None
    db.add_chapter_revision(book_id, 1, "T", ["x"], kind='auto')
    first = db.latest_revision_time(book_id, 1)
    assert first
    db.add_chapter_revision(book_id, 1, "T", ["y"], kind='manual')
    assert db.latest_revision_time(book_id, 1) >= first


def test_prune_keeps_newest_per_kind(db):
    book_id = _make_book_chapter(db)
    keep_auto = db.REVISIONS_KEEP_AUTO
    for i in range(keep_auto + 5):
        db.add_chapter_revision(book_id, 1, "T", [f"auto {i}"], kind='auto')
    # A couple of manuals must survive independently of the auto cap
    manual_ids = [db.add_chapter_revision(book_id, 1, "T", [f"manual {i}"], kind='manual')
                  for i in range(3)]

    revisions = db.list_chapter_revisions(book_id, 1, limit=200)
    autos = [r for r in revisions if r["kind"] == "auto"]
    manuals = [r for r in revisions if r["kind"] == "manual"]
    assert len(autos) == keep_auto
    assert {r["id"] for r in manuals} == set(manual_ids)
    # The survivors are the newest autos
    newest_auto = db.get_chapter_revision(autos[0]["id"])
    assert newest_auto["content"] == [f"auto {keep_auto + 4}"]


def test_revisions_scoped_per_chapter(db):
    book_id = _make_book_chapter(db)
    db.save_chapter(book_id=book_id, chapter_number=2, title="Chapter 2",
                    untranslated_content=[], translated_content=["ch2"])
    db.add_chapter_revision(book_id, 1, "T", ["one"], kind='manual')
    db.add_chapter_revision(book_id, 2, "T", ["two"], kind='manual')
    assert len(db.list_chapter_revisions(book_id, 1)) == 1
    assert len(db.list_chapter_revisions(book_id, 2)) == 1


def test_create_book_is_original_flag(db):
    book_id = db.create_book(title="Original Novel", is_original=True)
    book = db.get_book(book_id=book_id)
    assert book["is_original"] is True

    other = db.create_book(title="Translated Novel")
    assert db.get_book(book_id=other)["is_original"] is False

    books = {b["id"]: b for b in db.list_books()}
    assert books[book_id]["is_original"] is True
    assert books[other]["is_original"] is False

    # Toggleable via update_book
    assert db.update_book(other, is_original=True)
    assert db.get_book(book_id=other)["is_original"] is True
