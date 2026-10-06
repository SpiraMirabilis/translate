"""add_to_queue converts the queue title as well as the content when the book's
trad→simp module is on.

The title is source text too. Book 106's Taiwanese-sourced chapters arrived with
simplified content under traditional titles, because only the content went
through the source transforms.
"""
import pytest


def _queue_row(db, queue_id):
    for item in db.list_queue():
        if item["id"] == queue_id:
            return item
    raise AssertionError(f"queue item {queue_id} not found")


def test_title_converted_when_trad_to_simp_on(db):
    pytest.importorskip("opencc")
    book_id = db.create_book("Trad Book")
    assert db.update_book(book_id, modules={"trad_to_simp": True})
    queue_id = db.add_to_queue(book_id, ["第252章：神庭節氣令", "他們去了萬妖之門"],
                               title="第252章：神庭節氣令，飛升傳說！",
                               chapter_number=252)
    row = _queue_row(db, queue_id)
    assert row["title"] == "第252章：神庭节气令，飞升传说！"


def test_title_untouched_when_trad_to_simp_off(db):
    book_id = db.create_book("Plain Book")
    queue_id = db.add_to_queue(book_id, ["第一章"], title="第一章 飛升", chapter_number=1)
    assert _queue_row(db, queue_id)["title"] == "第一章 飛升"


def test_title_none_is_fine(db):
    pytest.importorskip("opencc")
    book_id = db.create_book("Trad Book")
    assert db.update_book(book_id, modules={"trad_to_simp": True})
    queue_id = db.add_to_queue(book_id, ["他們"], title=None, chapter_number=1)
    assert queue_id
