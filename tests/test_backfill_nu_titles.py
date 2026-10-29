"""backfill_nu_titles.py --apply: titles, series records, and what it leaves alone.
The search side needs the live scraper and isn't exercised here."""
import json

import backfill_nu_titles as bnt


def _proposals(tmp_path, rows):
    path = tmp_path / "proposals.json"
    path.write_text(json.dumps({"proposals": rows}, ensure_ascii=False), encoding="utf-8")
    return str(path)


def _row(book_id, title, slug=None, ours=False):
    return {"book_id": book_id, "title": title, "slug": slug, "in_our_group": ours,
            "url": f"https://www.novelupdates.com/series/{slug}/" if slug else None,
            "series_id": "1", "names": ["原名"]}


def test_apply_titles_and_series(db, tmp_path):
    ours = db.create_book("Ours")
    theirs = db.create_book("Theirs")
    tracked = db.create_book("Tracked")
    skipped = db.create_book("Skipped")
    db.update_book_nu(tracked, state="active", nu_slug="kept")
    before = db.get_book(book_id=ours)["modified_date"]

    path = _proposals(tmp_path, [
        _row(ours, " NU Ours ", "nu-ours", ours=True),
        _row(theirs, "NU Theirs", "nu-theirs"),
        _row(tracked, "NU Tracked", "other-slug", ours=True),
        _row(skipped, None),
    ])
    bnt.cmd_apply(db, path)

    assert db.get_book(book_id=ours)["nu_title"] == "NU Ours"
    assert db.get_book(book_id=ours)["modified_date"] == before   # no ebook rebuild
    assert db.get_book_nu(ours)["state"] == "active"
    assert db.get_book_nu(theirs)["state"] == "linked"
    assert db.get_book_nu(theirs)["nu_slug"] == "nu-theirs"
    # a book the lane already tracks keeps its own series record
    assert db.get_book_nu(tracked)["nu_slug"] == "kept"
    assert db.get_book(book_id=tracked)["nu_title"] == "NU Tracked"
    assert db.get_book(book_id=skipped)["nu_title"] is None
    assert db.get_book_nu(ours)["mode"] == "off"                   # nothing posts


def test_apply_no_link(db, tmp_path):
    book = db.create_book("Book")
    bnt.cmd_apply(db, _proposals(tmp_path, [_row(book, "NU Book", "nu-book")]), link=False)
    assert db.get_book(book_id=book)["nu_title"] == "NU Book"
    assert db.get_book_nu(book)["exists"] is False
