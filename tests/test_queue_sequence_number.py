"""An item queued without a chapter number takes its place in the run it was
queued in: previous item's number + 1.

Two layers:
- add_to_queue: an unnumbered item queued right behind a numbered one for the
  same book (within QUEUE_SEQUENCE_WINDOW_SECONDS), neither a retranslation.
- the batch importers (EPUB, FB2, directory, batch upload): an unparseable
  title/filename continues the batch instead of taking its absolute position.
"""
import datetime

from db.queue_repo import QUEUE_SEQUENCE_WINDOW_SECONDS
from epub_processor import next_in_sequence


def _number(db, queue_id):
    for item in db.list_queue():
        if item["id"] == queue_id:
            return item["chapter_number"]
    raise AssertionError(f"queue item {queue_id} not found")


def test_unnumbered_item_continues_previous(db):
    book_id = db.create_book("Seq")
    db.add_to_queue(book_id, ["a"], chapter_number=300)
    second = db.add_to_queue(book_id, ["b"])
    third = db.add_to_queue(book_id, ["c"])
    assert _number(db, second) == 301
    assert _number(db, third) == 302  # chains


def test_not_max_plus_one(db):
    """A rescrape of an earlier stretch continues that stretch, not the head."""
    book_id = db.create_book("Seq")
    db.add_to_queue(book_id, ["head"], chapter_number=900)
    db.add_to_queue(book_id, ["rescrape"], chapter_number=40)
    nxt = db.add_to_queue(book_id, ["next"])
    assert _number(db, nxt) == 41


def test_explicit_number_untouched(db):
    book_id = db.create_book("Seq")
    db.add_to_queue(book_id, ["a"], chapter_number=10)
    qid = db.add_to_queue(book_id, ["b"], chapter_number=77)
    assert _number(db, qid) == 77


def test_incoming_retranslation_not_inferred(db):
    book_id = db.create_book("Seq")
    db.add_to_queue(book_id, ["a"], chapter_number=10)
    qid = db.add_to_queue(book_id, ["b"], retranslation_reason="fix names")
    assert _number(db, qid) is None


def test_previous_retranslation_not_an_anchor(db):
    book_id = db.create_book("Seq")
    db.add_to_queue(book_id, ["a"], chapter_number=10, retranslation_reason="fix names")
    qid = db.add_to_queue(book_id, ["b"])
    assert _number(db, qid) is None


def test_stale_previous_not_an_anchor(db):
    book_id = db.create_book("Seq")
    first = db.add_to_queue(book_id, ["a"], chapter_number=10)
    old = (datetime.datetime.now()
           - datetime.timedelta(seconds=QUEUE_SEQUENCE_WINDOW_SECONDS + 5)).isoformat()
    with db._conn() as conn:
        conn.cursor().execute("UPDATE queue SET created_date = ? WHERE id = ?", (old, first))
    qid = db.add_to_queue(book_id, ["b"])
    assert _number(db, qid) is None


def test_other_books_item_not_an_anchor(db):
    a = db.create_book("A")
    b = db.create_book("B")
    db.add_to_queue(a, ["a"], chapter_number=10)
    qid = db.add_to_queue(b, ["b"])
    assert _number(db, qid) is None


def test_unnumbered_previous_not_an_anchor(db):
    book_id = db.create_book("Seq")
    db.add_to_queue(book_id, ["a"], retranslation_reason=None)  # nothing to anchor on
    qid = db.add_to_queue(book_id, ["b"])
    assert _number(db, qid) is None


def test_next_in_sequence():
    numbers, prev = [], None
    # an EPUB of ch300+ with an unparseable title at position 3
    for pos, parsed in enumerate([300, 301, None, 303, None], 1):
        prev = next_in_sequence(parsed, prev, pos)
        numbers.append(prev)
    assert numbers == [300, 301, 302, 303, 304]


def test_next_in_sequence_first_falls_back_to_position():
    assert next_in_sequence(None, None, 1) == 1
    assert next_in_sequence(None, None, 7) == 7


# --- parsed numbers the batch's own sequence contradicts -------------------

from queue_sequence import is_repost, resequence_batch, resequence_for_book


def _batch(*numbers):
    return [{"number": n, "content": [f"prose of item {i} ({n})", f"more {i}"]}
            for i, n in enumerate(numbers)]


def test_sandwiched_collision_renumbered():
    items = _batch(50, 51, 40, 53)
    moved = resequence_batch(items, existing_numbers={40}, existing_lines=lambda n: ["old ch40"])
    assert [it["number"] for it in items] == [50, 51, 52, 53]
    assert items[2]["renumbered_from"] == 40
    assert moved == [(40, 52)]


def test_no_collision_left_alone():
    items = _batch(50, 51, 40, 53)
    resequence_batch(items, existing_numbers=set(), existing_lines=lambda n: None)
    assert [it["number"] for it in items] == [50, 51, 40, 53]


def test_collision_within_batch():
    items = _batch(40, 41, 50, 51, 40, 53)
    resequence_batch(items, existing_numbers=set())
    assert [it["number"] for it in items] == [40, 41, 50, 51, 52, 53]


def test_needs_both_neighbours():
    first = _batch(40, 51, 52)            # nothing before it
    last = _batch(50, 51, 40)             # nothing after it
    for items in (first, last):
        resequence_batch(items, existing_numbers={40}, existing_lines=lambda n: ["x"])
    assert [it["number"] for it in first] == [40, 51, 52]
    assert [it["number"] for it in last] == [50, 51, 40]


def test_slot_must_be_free():
    items = _batch(50, 51, 40, 53)
    resequence_batch(items, existing_numbers={40, 52}, existing_lines=lambda n: ["x"])
    assert items[2]["number"] == 40


def test_neighbours_must_leave_exactly_one_slot():
    items = _batch(50, 51, 40, 54)
    resequence_batch(items, existing_numbers={40}, existing_lines=lambda n: ["x"])
    assert items[2]["number"] == 40


def test_repost_not_renumbered():
    items = _batch(50, 51, 40, 53)
    items[2]["content"] = ["line a", "line b", "line c (revised)"]
    resequence_batch(items, existing_numbers={40},
                     existing_lines=lambda n: ["line a", "line b", "line c"])
    assert items[2]["number"] == 40


def test_unreadable_existing_text_not_renumbered():
    items = _batch(50, 51, 40, 53)
    resequence_batch(items, existing_numbers={40}, existing_lines=lambda n: None)
    assert items[2]["number"] == 40


def test_retranslation_not_renumbered():
    items = _batch(50, 51, 40, 53)
    items[2]["retranslation_reason"] = "fix names"
    resequence_batch(items, existing_numbers={40}, existing_lines=lambda n: ["x"])
    assert items[2]["number"] == 40


def test_is_repost():
    assert is_repost(["a", "b", "c"], ["a", "b", "z"])
    assert not is_repost(["a", "b", "c"], ["x", "y", "z"])
    assert not is_repost([], ["a"])


def test_resequence_for_book_reads_saved_and_queued_source(db):
    book_id = db.create_book("Seq")
    db.save_chapter(book_id, 40, "Chapter 40", ["saved forty"], ["translated forty"])
    items = _batch(50, 51, 40, 53)
    assert resequence_for_book(db, book_id, items) == [(40, 52)]

    # a queued ch60 the incoming "60" is a repost of: left alone
    db.add_to_queue(book_id, ["queued sixty", "second line"], chapter_number=60)
    items = _batch(70, 71, 60, 73)
    items[2]["content"] = ["queued sixty", "second line"]
    assert resequence_for_book(db, book_id, items) == []
    assert items[2]["number"] == 60


def test_directory_import_renumbers_and_records(db, tmp_path, monkeypatch):
    """End to end: the importer queues the mislabelled file into its slot,
    keeps the parsed number in the row's metadata, and logs one activity line."""
    import logging
    import os
    import modules.activity
    from directory_processor import DirectoryProcessor

    # A test that built the web app leaves its WS notifier installed; write
    # straight to this test's DB instead.
    monkeypatch.setattr(modules.activity, "_notifier", None)

    book_id = db.create_book("Dir")
    db.save_chapter(book_id, 40, "Chapter 40", ["the real forty"], ["translated"])
    for i, n in enumerate([50, 51, 40, 53]):  # "40" is really 52
        f = tmp_path / f"ch{n}.txt"
        f.write_text(f"body of file {i}\nsecond line {i}", encoding="utf-8")
        os.utime(f, (1_000_000 + i, 1_000_000 + i))

    proc = DirectoryProcessor(None, logging.getLogger("test"), db)
    ok, added, _msg = proc.process_directory(str(tmp_path), book_id=book_id,
                                             sort_strategy="modified", file_pattern="*.txt")
    assert ok and added == 4
    rows = {item["title"]: item for item in db.list_queue(book_id=book_id, include_content=True)}
    assert rows["ch40"]["chapter_number"] == 52
    assert rows["ch40"]["metadata"] == {"renumbered_from": 40}
    assert rows["ch50"]["metadata"] is None
    assert any("40 → 52" in e["message"] for e in db.get_activity_log())
