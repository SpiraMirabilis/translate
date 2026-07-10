"""replace_in_chapters must also rewrite chapter titles.

Chapter titles live in their own column. For a long time `replace_in_chapters`
touched only `translated_content`, so a book-wide terminology sweep left the
title stale while the prose was clean — caught twice in book 69 ("Pictographic
Fist" in the ch74 title, "Cotton-Cloth Town" in the ch142 title).
"""
import json

import pytest


def _mk_book(db, title="T", chapters=()):
    book_id = db.create_book(title, "author")
    for num, ch_title, lines in chapters:
        db.save_chapter(book_id=book_id, chapter_number=num, title=ch_title,
                        untranslated_content=["src"], translated_content=list(lines))
    return book_id


def _get(db, book_id, num):
    ch = db.get_chapter(book_id=book_id, chapter_number=num)
    return ch["title"], ch["content"]


def test_replace_rewrites_title_and_body(db):
    book_id = _mk_book(db, chapters=[(1, "Escape from Cotton-Cloth Town",
                                      ["He fled Cotton-Cloth Town at dawn."])])
    res = db.replace_in_chapters(book_id, "Cotton-Cloth Town", "Commoner Town")
    title, body = _get(db, book_id, 1)
    assert title == "Escape from Commoner Town"
    assert body == ["He fled Commoner Town at dawn."]
    assert res["total_replacements"] == 1
    assert res["title_replacements"] == 1


def test_title_only_match_still_counts_as_affected(db):
    """A chapter whose title matches but whose body does not must still be updated."""
    book_id = _mk_book(db, chapters=[(1, "The Pictographic Fist", ["No match here."])])
    res = db.replace_in_chapters(book_id, "Pictographic", "Beast-Mimicry")
    title, body = _get(db, book_id, 1)
    assert title == "The Beast-Mimicry Fist"
    assert body == ["No match here."]
    assert res["affected_chapters"] == 1
    assert res["total_replacements"] == 0
    assert res["title_replacements"] == 1


def test_include_titles_false_leaves_title_alone(db):
    book_id = _mk_book(db, chapters=[(1, "Cotton-Cloth Town", ["Cotton-Cloth Town"])])
    res = db.replace_in_chapters(book_id, "Cotton-Cloth Town", "Commoner Town",
                                 include_titles=False)
    title, body = _get(db, book_id, 1)
    assert title == "Cotton-Cloth Town"
    assert body == ["Commoner Town"]
    assert res["title_replacements"] == 0


def test_undo_restores_both_title_and_body(db):
    book_id = _mk_book(db, chapters=[(1, "Old Title", ["Old body"])])
    db.replace_in_chapters(book_id, "Old", "New")
    assert _get(db, book_id, 1) == ("New Title", ["New body"])

    db.undo_replace(book_id)
    assert _get(db, book_id, 1) == ("Old Title", ["Old body"])


def test_regex_applies_to_title(db):
    book_id = _mk_book(db, chapters=[(1, "Chapter 12 Helm", ["the helm"])])
    db.replace_in_chapters(book_id, r"\bhelms?\b", "Branch", is_regex=True)
    title, body = _get(db, book_id, 1)
    assert title == "Chapter 12 Branch"
    assert body == ["the Branch"]


def test_empty_title_is_safe(db):
    """`chapters.title` is NOT NULL, but it may be empty; replacement must not blow up."""
    book_id = _mk_book(db, chapters=[(1, "tmp", ["Cotton-Cloth Town"])])
    with db._conn() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE chapters SET title = '' WHERE book_id = ? AND chapter_number = ?",
                    (book_id, 1))
        conn.commit()

    res = db.replace_in_chapters(book_id, "Cotton-Cloth Town", "Commoner Town")
    assert res["title_replacements"] == 0
    assert res["total_replacements"] == 1

    with db._conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT title, translated_content FROM chapters WHERE book_id = ? AND chapter_number = ?",
                    (book_id, 1))
        title, content = cur.fetchone()
    assert title == ""
    assert json.loads(content) == ["Commoner Town"]


def test_chapter_scoping_still_honoured(db):
    book_id = _mk_book(db, chapters=[(1, "Helm", ["helm"]), (2, "Helm", ["helm"])])
    db.replace_in_chapters(book_id, "Helm", "Branch", chapter_numbers=[2])
    assert _get(db, book_id, 1) == ("Helm", ["helm"])
    assert _get(db, book_id, 2) == ("Branch", ["Branch"])
