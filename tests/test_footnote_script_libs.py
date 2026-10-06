"""Library functions pulled out of the footnote CLIs (add_footnotes,
delete_footnote, list_footnotes, footnote_scan) so they can be imported
without argparse. All on the tmp SQLite `db` fixture; no model calls."""
import pytest

import add_footnotes as af
import delete_footnote as dfn
import footnote_scan as fs
import list_footnotes as lf
from footnotes import content_to_list


def _content(db, book_id, n, key="content"):
    return content_to_list(db.get_chapter(book_id=book_id, chapter_number=n)[key])


@pytest.fixture
def book(db):
    book_id = db.create_book("Footnote Libs", "Author")
    db.save_chapter(book_id, 1, "One", ["第一章", "他读了《阿房宫赋》。"],
                    ["Chapter 1: Beginnings", "He read 《Rhapsody》 aloud.",
                     "Later, the Rhapsody again."])
    db.save_chapter(book_id, 2, "Two", ["第二章", "观音土。"],
                    ["Chapter 2: Calabash Brothers", "The Rhapsody and Guanyin clay.",
                     "The Calabash Brothers rushed in."])
    return db, book_id


# ── add_footnotes ────────────────────────────────────────────────────────────

def test_load_book_text_shapes(book):
    db, book_id = book
    text = af.load_book_text(db, book_id)
    assert text["chapters"] == [1, 2]
    assert text["content"][1][1] == "He read 《Rhapsody》 aloud."
    assert text["full_text"][2] == "\n".join(text["content"][2])
    assert set(text["chapter_id_by_num"]) == {1, 2}
    src = af.load_book_text(db, book_id, source=True)
    # The source side goes through save_chapter's paragraph spacing.
    assert [l for l in src["content"][2] if l] == ["第二章", "观音土。"]


def test_plan_first_mention_book_wide_and_apply(book):
    db, book_id = book
    text = af.load_book_text(db, book_id)
    plan, skipped, not_found = af.plan_footnotes(
        {"Rhapsody": "Rhapsody: a fu poem.", "Nowhere": "Nowhere: absent."},
        text["chapters"], text["content"], text["full_text"])
    assert skipped == [] and not_found == ["Nowhere"]
    assert len(plan) == 1
    item = plan[0]
    assert (item["chapter"], item["anchor"], item["body"]) == (1, "Rhapsody", "Rhapsody: a fu poem.")
    assert item["line_idx"] == 1 and item["number"] == 1
    assert item["warning"] is None and item["renumbers_existing"] is False
    # Marker hops the 》 closer.
    line = text["content"][1][1]
    assert line[:item["insert_at"]].endswith("》")

    n = af.apply_footnote_plan(db, book_id, plan, text["chapter_id_by_num"], 0)
    assert n == 1
    lines = _content(db, book_id, 1)
    assert lines[1] == "He read 《Rhapsody》[1] aloud."
    assert lines[-1] == "[1] Rhapsody: a fu poem."
    assert _content(db, book_id, 2)[1] == "The Rhapsody and Guanyin clay."

    # Idempotent re-run: the exact body is already in the book.
    text2 = af.load_book_text(db, book_id)
    plan2, skipped2, not_found2 = af.plan_footnotes(
        {"Rhapsody": "Rhapsody: a fu poem."},
        text2["chapters"], text2["content"], text2["full_text"])
    assert plan2 == [] and skipped2 == ["Rhapsody"] and not_found2 == []


def test_plan_forced_chapter(book):
    db, book_id = book
    text = af.load_book_text(db, book_id)
    plan, _, _ = af.plan_footnotes({"Rhapsody": "Rhapsody: forced."}, text["chapters"],
                                   text["content"], text["full_text"], chapter=2)
    assert [(p["chapter"], p["line_idx"]) for p in plan] == [(2, 1)]
    with pytest.raises(ValueError):
        af.plan_footnotes({"Rhapsody": "Rhapsody: forced."}, text["chapters"], text["content"],
                          text["full_text"], chapter=99)
    with pytest.raises(ValueError):
        af.plan_footnotes({}, text["chapters"], text["content"], text["full_text"])


def test_plan_warns_when_first_occurrence_is_the_heading(book):
    db, book_id = book
    text = af.load_book_text(db, book_id)
    plan, _, _ = af.plan_footnotes(
        {"Calabash Brothers": "Calabash Brothers (葫芦兄弟): a cartoon."},
        text["chapters"], text["content"], text["full_text"])
    # Placement is unchanged (still the heading line); only a warning is added.
    assert plan[0]["chapter"] == 2 and plan[0]["line_idx"] == 0
    assert plan[0]["warning"]


def test_plan_numbers_new_footnote_ahead_of_existing(book):
    db, book_id = book
    text = af.load_book_text(db, book_id)
    plan, _, _ = af.plan_footnotes({"Guanyin clay": "Guanyin clay: famine food."},
                                   text["chapters"], text["content"], text["full_text"])
    af.apply_footnote_plan(db, book_id, plan, text["chapter_id_by_num"], 0)
    text = af.load_book_text(db, book_id)
    plan, _, _ = af.plan_footnotes({"Rhapsody": "Rhapsody: poem."}, text["chapters"],
                                   text["content"], text["full_text"], chapter=2)
    assert plan[0]["number"] == 1 and plan[0]["renumbers_existing"] is True
    af.apply_footnote_plan(db, book_id, plan, text["chapter_id_by_num"], 0)
    lines = _content(db, book_id, 2)
    assert lines[1] == "The Rhapsody[1] and Guanyin clay[2]."
    assert lines[-2:] == ["[1] Rhapsody: poem.", "[2] Guanyin clay: famine food."]


def test_apply_rejects_unknown_chapter(book):
    db, book_id = book
    with pytest.raises(ValueError):
        af.apply_footnote_plan(db, book_id, [{"chapter": 7, "anchor": "a", "body": "b"}],
                               {1: 1}, 0)


# ── delete_footnote ──────────────────────────────────────────────────────────

@pytest.fixture
def footnoted(book):
    db, book_id = book
    text = af.load_book_text(db, book_id)
    plan, _, _ = af.plan_footnotes(
        {"Rhapsody": "Rhapsody: a fu poem.", "Later": "Later: a later note."},
        text["chapters"], text["content"], text["full_text"], chapter=1)
    af.apply_footnote_plan(db, book_id, plan, text["chapter_id_by_num"], 0)
    chapter_id = text["chapter_id_by_num"][1]
    return db, book_id, chapter_id


def test_resolve_targets_selectors(footnoted):
    db, book_id, chapter_id = footnoted
    chapter = db.get_chapter(chapter_id=chapter_id)
    rows = db.get_chapter_footnotes(chapter_id, is_source=0)
    by_anchor = {r["anchor"]: r for r in rows}

    def ids(**kw):
        return [r["id"] for r in dfn.resolve_targets(rows, chapter, 0, **kw)]

    assert ids(all_=True) == [r["id"] for r in rows]
    assert ids(anchor="Later") == [by_anchor["Later"]["id"]]
    assert ids(body_contains="FU POEM") == [by_anchor["Rhapsody"]["id"]]
    assert ids(footnote_id=by_anchor["Later"]["id"]) == [by_anchor["Later"]["id"]]
    # [1] is Rhapsody (line 1), [2] is Later (line 2).
    assert ids(number=2) == [by_anchor["Later"]["id"]]
    assert ids(number=9) == []
    assert ids(anchor="nope") == []
    with pytest.raises(ValueError):
        dfn.resolve_targets(rows, chapter, 0)
    with pytest.raises(ValueError):
        dfn.resolve_targets(rows, chapter, 0, anchor="Later", number=1)
    with pytest.raises(ValueError):
        dfn.resolve_targets(rows, chapter, 0, all_=True, footnote_id=1)


def test_deleting_the_last_footnote_rerenders_clean(footnoted):
    db, book_id, chapter_id = footnoted
    chapter = db.get_chapter(chapter_id=chapter_id)
    rows = db.get_chapter_footnotes(chapter_id, is_source=0)
    for r in dfn.resolve_targets(rows, chapter, 0, all_=True):
        assert db.delete_footnote(r["id"])
    dfn.rerender_side(db, chapter_id, book_id, 0)
    assert _content(db, book_id, 1) == ["Chapter 1: Beginnings",
                                        "He read 《Rhapsody》 aloud.",
                                        "Later, the Rhapsody again."]


# ── list_footnotes.reanchor_footnote ─────────────────────────────────────────

def test_reanchor_returns_dict(footnoted):
    db, book_id, chapter_id = footnoted
    fid = next(r["id"] for r in db.get_chapter_footnotes(chapter_id)
               if r["anchor"] == "Later")
    res = lf.reanchor_footnote(db, book_id, fid, "again")
    assert res == {"ok": True, "footnote_id": fid, "chapter": 1,
                   "old_anchor": "Later", "new_anchor": "again",
                   "status": "active", "error": None}
    assert _content(db, book_id, 1)[2] == "Later, the Rhapsody again[2]."

    res = lf.reanchor_footnote(db, book_id, fid, "absent term")
    assert res["ok"] and res["status"] == "orphaned"

    res = lf.reanchor_footnote(db, book_id, 999999, "x")
    assert not res["ok"] and "not found" in res["error"] and res["status"] is None
    res = lf.reanchor_footnote(db, book_id, fid, "")
    assert not res["ok"] and res["error"]


# ── footnote_scan ────────────────────────────────────────────────────────────

def _cand(zh, en, body="b", status=None):
    c = {"term_zh": zh, "term_en": en, "body": body, "sentence": ""}
    if status:
        c["status"] = status
    return c


@pytest.fixture
def candidates(book):
    db, book_id = book
    assert db.record_footnote_scan(book_id, 1, "One", "m", "h1", [
        _cand("阿房宫赋", "Rhapsody", "Rhapsody (阿房宫赋): a fu.", "accepted"),
        _cand("白蛇传", "Legend of the White Snake", "invented"),   # not in source
    ])
    assert db.record_footnote_scan(book_id, 2, "Two", "m", "h2", [
        _cand("观音土", "Guanyin clay", "Guanyin clay (观音土): famine food."),
        _cand("阿房宫赋", "Rhapsody", "repeat of ch1"),
        _cand("画皮", "Painted Skin", "rejected one", "rejected"),
    ])
    return db, book_id


def test_candidate_report(candidates):
    db, book_id = candidates
    rep = fs.build_candidate_report(db, book_id, None)
    assert rep["total"] == 5 and rep["rejected"] == 1
    assert rep["shown"] == 4 == len(rep["rows"])  # ch2 Rhapsody is a repeat
    first = rep["rows"][0]
    assert first["term_en"] == "Rhapsody" and first["also_chapters"] == [2]
    assert first["status"] == "accepted" and first["already_footnoted"] is False
    assert "[KEEP, also ch2]" in rep["text"]
    assert rep["text"].startswith("\nch1 — One")
    assert rep["text"].endswith("5 candidate row(s), 4 shown (first mentions; "
                                "--all for every row), 1 rejected.")

    full = fs.build_candidate_report(db, book_id, "2", all_=True)
    assert full["total"] == full["shown"] == 3
    assert all(r["chapter"] == 2 for r in full["rows"])

    empty = fs.build_candidate_report(db, book_id, ">50")
    assert empty["total"] == 0 and empty["text"] == "No candidates collected yet."
    with pytest.raises(ValueError):
        fs.build_candidate_report(db, book_id, "garbage")


def test_find_unverified_and_prune(candidates):
    db, book_id = candidates
    res = fs.find_unverified_candidates(db, book_id, None)
    assert res["checked"] == 5 and res["chapters"] == 2 and res["no_source"] == []
    # 白蛇传 is nowhere in ch1's source; 阿房宫赋 isn't in ch2's; nor is 画皮.
    doomed = sorted(r["term_zh"] for r in res["doomed"])
    assert doomed == sorted(["白蛇传", "画皮", "阿房宫赋"])
    assert res["by_status"] == {"pending": 2, "rejected": 1}

    assert fs.prune_candidates(db, book_id, res["doomed"]) == 3
    left = sorted(r["term_zh"] for r in fs.load_candidates(db, book_id))
    assert left == ["观音土", "阿房宫赋"]
    assert db.get_footnote_scans(book_id)[2]["n_found"] == 1


def test_build_export_map(candidates):
    db, book_id = candidates
    mapping, warn = fs.build_export_map(db, book_id, None)
    # Rejected excluded, repeats collapse to the first mention, pending kept.
    assert mapping == {
        "Rhapsody": "Rhapsody (阿房宫赋): a fu.",
        "Legend of the White Snake": "invented",
        "Guanyin clay": "Guanyin clay (观音土): famine food.",
    }
    assert warn["not_in_translation"] == ["Legend of the White Snake"]
    assert warn["skipped_already"] == [] and warn["no_term"] == []

    # Once Rhapsody is really footnoted it drops out of the export.
    text = af.load_book_text(db, book_id)
    plan, _, _ = af.plan_footnotes({"Rhapsody": "x"}, text["chapters"],
                                   text["content"], text["full_text"])
    af.apply_footnote_plan(db, book_id, plan, text["chapter_id_by_num"], 0)
    mapping, warn = fs.build_export_map(db, book_id, "1-5")
    assert "Rhapsody" not in mapping and warn["skipped_already"] == ["Rhapsody"]


def test_build_scan_jobs(book):
    db, book_id = book
    db.save_chapter(book_id, 3, "Three", [], ["Chapter 3"])
    jobs, no_source = fs.build_scan_jobs(db, book_id, None)
    assert [j["chapter"] for j in jobs] == [1, 2] and no_source == [3]
    assert set(jobs[0]) == {"chapter", "title", "lines", "content_hash"}
    assert [l for l in jobs[0]["lines"] if l] == ["第一章", "他读了《阿房宫赋》。"]
    jobs, no_source = fs.build_scan_jobs(db, book_id, fs.parse_chapter_spec(">1"))
    assert [j["chapter"] for j in jobs] == [2] and no_source == [3]
    # Feeds plan_scan unchanged.
    to_scan, skipped, _ = fs.plan_scan(jobs, db.get_footnote_scans(book_id), False)
    assert to_scan == jobs and skipped == []
