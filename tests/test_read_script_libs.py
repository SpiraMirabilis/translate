"""Library functions pulled out of the read-side CLI scripts.

The MCP server imports these directly, so each must return data, raise
ValueError on bad input, and never print or exit. The CLIs are thin wrappers.
"""
import pytest

import backfill_origin_chapter as bo
import get_entities as ge
import get_entity_context as gec
import grep_book as gb
import note_revisions as nr
import search_entities as se


@pytest.fixture
def book(db):
    book_id = db.create_book("Lib Book")
    db.save_chapter(book_id, 1, "Chapter One",
                    ["许春娘走进村子。", "她看见了莫林。"],
                    ["Xu Chunniang walked into the village.", "She saw Mo Lin."])
    db.save_chapter(book_id, 2, "Chapter Two",
                    ["许春娘练剑。", "剑光一闪。", "许春娘笑了。"],
                    ["Xu Chunniang practised the sword.", "A flash of sword light.",
                     "Xu Chunniang smiled."])
    # A queued, not-yet-translated chapter: the only place 白虎 appears.
    db.add_to_queue(book_id, ["白虎出现了。", "许春娘后退。"], title="Chapter Three",
                    chapter_number=3)

    db.add_entity("characters", "许春娘", "Xu Chunniang", book_id=book_id,
                  last_chapter=1, origin_chapter=1, gender="female",
                  note="Eight years old, village girl.",
                  note_author="model", note_chapter=1)
    xu = db.get_entity_id(book_id, "许春娘")
    db.set_entity_note(xu, "Twelve; has a RIGHT ARM wound. Golden Core aspirant.",
                       author="model", chapter_number=2, reason="time skip")
    db.set_entity_note(xu, "Twelve; Golden Core aspirant.",
                       author="human", chapter_number=None, reason="arm healed")
    # Origin deliberately late / missing, for the backfill.
    db.add_entity("characters", "莫林", "Mo Lin", book_id=book_id,
                  last_chapter=5, origin_chapter=5)
    db.add_entity("creatures", "白虎", "White Tiger", book_id=book_id)
    db.add_entity("items", "龙珠", "Dragon Pearl", book_id=book_id)
    db.add_entity("items", "剑", "Sword", book_id=book_id, origin_chapter=9)
    return book_id


def _origins(db, book_id):
    with db._conn(dict_rows=True) as conn:
        cur = conn.cursor()
        cur.execute("SELECT untranslated, origin_chapter FROM entities WHERE book_id = ?",
                    (book_id,))
        return {r["untranslated"]: r["origin_chapter"] for r in cur.fetchall()}


# ---------------------------------------------------------------- get_entities


def test_build_entities_payload_and_text(db, book):
    b = ge.resolve_book(db, str(book))
    payload = ge.build_entities_payload(db, b, "1-1")
    assert set(payload) == {"book", "filter", "notes_as_of_chapter", "entities"}
    assert payload["book"] == {"id": book, "title": "Lib Book"}
    assert payload["filter"] == "1-1"
    assert payload["notes_as_of_chapter"] == 1          # upper bound of the filter
    xu = payload["entities"]["characters"][0]
    assert xu["untranslated"] == "许春娘"
    assert xu["note"] == "Eight years old, village girl."   # wound back to ch1

    text = ge.render_entities_text(payload)
    assert text.startswith("# Lib Book (id=%d)\n# origin_chapter filter: 1-1\n"
                           "# notes as of chapter 1\n" % book)
    assert "== characters (1) ==" in text
    assert ("     ch1  许春娘 -> Xu Chunniang  [female, note updated ch1, "
            "note=Eight years old, village girl.]") in text
    assert text.endswith("\n")


def test_build_entities_payload_options(db, book):
    b = {"id": book, "title": "Lib Book"}
    current = ge.build_entities_payload(db, b, "1-1", current_notes=True)
    assert current["notes_as_of_chapter"] is None
    assert current["entities"]["characters"][0]["note"] == "Twelve; Golden Core aspirant."

    # A note change at ch2 pulls 许春娘 into a ch2 filter unless origin_only.
    widened = ge.build_entities_payload(db, b, "2")
    assert [e["untranslated"] for e in widened["entities"]["characters"]] == ["许春娘"]
    assert widened["entities"]["characters"][0]["note_updated_chapters"] == [2]
    assert ge.build_entities_payload(db, b, "2", origin_only=True)["entities"] == {}

    everything = ge.build_entities_payload(db, b)
    assert everything["filter"] is None and everything["notes_as_of_chapter"] is None
    assert ge.build_entities_payload(db, b, as_of_chapter=1)["notes_as_of_chapter"] == 1

    with pytest.raises(ValueError):
        ge.build_entities_payload(db, b, "x1")


# ------------------------------------------------------------- search_entities


def test_search_entities_build_matcher():
    with pytest.raises(ValueError, match="invalid regex"):
        se.build_matcher("(", True, True)
    m = se.build_matcher("xu*", False, True)
    assert m("Xu Chunniang") and not m("Mo Lin")
    assert se.build_matcher("xu", False, False)("Xu") is False


# ---------------------------------------------------------- get_entity_context


def test_parse_mentions_and_chapter_filter_raise():
    assert gec.parse_mentions("1,2,$,2") == [1, 2, "$"]
    with pytest.raises(ValueError, match="invalid --mentions value"):
        gec.parse_mentions("x")
    with pytest.raises(ValueError, match="positive integers"):
        gec.parse_mentions("0")
    with pytest.raises(ValueError, match="invalid --chapters value"):
        gec.parse_chapter_filter("a-b")
    pred = gec.parse_chapter_filter("1-2,>5")
    assert pred(2) and pred(6) and not pred(3)


def test_contexts_for_entity_standalone(db, book):
    b = {"id": book, "title": "Lib Book"}
    res = gec.contexts_for_entity(db, b, "许春娘", "\n\n", [1, "$"])
    assert all(r["ok"] for r in res)
    assert res[0]["occurrences"] == [1] and "许春娘走进村子" in res[0]["body"]
    assert res[-1]["is_last"] and res[-1]["occurrences"] == [3]   # saved chapters only

    only_ch2 = gec.contexts_for_entity(db, b, "许春娘", "\n\n", [1],
                                       gec.parse_chapter_filter("2"))
    assert "chapter 2" in only_ch2[0]["header"]

    missing = gec.contexts_for_entity(db, b, "不存在", "\n\n", [1])
    assert missing == [{"header": f"# '不存在': NOT FOUND in book {book} ('Lib Book')",
                        "body": "", "occurrences": [1], "is_last": False, "ok": False}]


# -------------------------------------------------------------- note_revisions


def _revs(db, book):
    return nr.fetch_revisions(db, book)


def test_filter_revisions_introduced_dropped(db, book):
    rows = _revs(db, book)
    assert len(rows) == 3          # creation, ch2 update, hand edit

    intro = nr.filter_revisions(rows, introduced="golden core")   # case-insensitive
    assert [r["chapter_number"] for r in intro] == [2]

    dropped = nr.filter_revisions(rows, dropped="right arm")
    assert [r["reason"] for r in dropped] == ["arm healed"]

    # grep matches every row that merely carries the clause forward.
    assert len(nr.filter_revisions(rows, grep="Golden Core")) == 2
    assert nr.filter_revisions(rows, grep="golden core", ignore_case=False) == []
    assert len(nr.filter_revisions(rows, grep=r"^Twelve", regex=True)) == 2
    assert nr.filter_revisions(rows, limit=1) == rows[-1:]
    # Filters AND together.
    assert nr.filter_revisions(rows, grep="Twelve", introduced="arm") == \
        nr.filter_revisions(rows, introduced="arm")
    with pytest.raises(ValueError, match="invalid regex"):
        nr.filter_revisions(rows, grep="(", regex=True)
    assert nr.filter_revisions(rows) == rows


def test_render_revision(db, book):
    rows = _revs(db, book)
    first, second, third = rows
    out = nr.render_revision(first, show_entity=False)
    assert out.split("\n") == ["-" * 78, f"[{first['id']}] ch1  model",
                               "  note: Eight years old, village girl."]
    out = nr.render_revision(second, show_prev=True)
    assert f"[{second['id']}] ch2  model  许春娘 : Xu Chunniang" in out
    assert "  why : time skip" in out
    assert "  prev: Eight years old, village girl." in out
    out = nr.render_revision(third, diff=True)
    assert f"[{third['id']}] ch—  human" in out
    assert "  diff: " in out and "[-" in out
    assert nr.render_revision(first, diff=True).endswith(
        "  new : Eight years old, village girl.")


def test_print_revision_uses_render(db, book, capsys):
    import argparse
    row = _revs(db, book)[1]
    nr.print_revision(row, argparse.Namespace(diff=False, show_prev=True), True)
    assert capsys.readouterr().out == nr.render_revision(row, show_prev=True) + "\n"


# ------------------------------------------------------------------ grep_book


def test_grep_chapters_hits_and_queue(db, book):
    chapters = gb.collect_chapters(db, book, ["src"], include_queue=True)
    assert chapters[3]["queued"] is True
    matcher = gb.build_matcher("许春娘|白虎", False, False)
    hits = gb.grep_chapters(chapters, matcher, ["src"])
    assert [(h["chapter_number"], h["line_index"]) for h in hits] == \
        [(1, 0), (2, 0), (2, 4), (3, 0), (3, 2)]   # stored with blank separator lines
    queued_hit = hits[3]
    assert queued_hit["queued"] is True
    assert queued_hit["labels"] == ["白虎"]
    assert queued_hit["text"] == "白虎出现了。"
    assert queued_hit["field"] == "src" and queued_hit["tag"] == ""
    assert hits[0]["labels"] == ["许春娘"] and hits[0]["queued"] is False

    no_queue = gb.collect_chapters(db, book, ["src"], include_queue=False)
    assert 3 not in no_queue

    filtered = gb.grep_chapters(chapters, matcher, ["src"],
                                chapter_filter=gb.parse_chapter_filter("2"))
    assert {h["chapter_number"] for h in filtered} == {2}


def test_grep_chapters_titles_and_both_fields(db, book):
    chapters = gb.collect_chapters(db, book, ["src", "en"], include_queue=True)
    matcher = gb.build_matcher("chapter one|sword", False, True)
    hits = gb.grep_chapters(chapters, matcher, ["src", "en"], titles=True)
    title_hits = [h for h in hits if h["is_title"]]
    # A title is tested once per field, as the CLI always has.
    assert [(h["chapter_number"], h["tag"]) for h in title_hits] == \
        [(1, " src title"), (1, " en title")]
    assert title_hits[0]["line_index"] is None
    assert {h["tag"] for h in hits if not h["is_title"]} == {" en"}


def test_format_hits_modes(db, book):
    chapters = gb.collect_chapters(db, book, ["src"], include_queue=True)
    matcher = gb.build_matcher("许春娘|白虎", False, False)
    hits = gb.grep_chapters(chapters, matcher, ["src"])

    assert gb.format_hits(hits, chapters) == (
        "ch1 [许春娘] [0] 许春娘走进村子。\n"
        "ch2 [许春娘] [0] 许春娘练剑。\n"
        "ch2 [许春娘] [4] 许春娘笑了。\n"
        "ch3 (queued) [白虎] [0] 白虎出现了。\n"
        "ch3 (queued) [许春娘] [2] 许春娘后退。\n")
    assert gb.format_hits(hits, chapters, match_tag=False).splitlines()[0] == \
        "ch1 [0] 许春娘走进村子。"
    assert gb.format_hits(hits, chapters, count=True) == (
        "ch1: 1 [许春娘]\nch2: 2 [许春娘 ×2]\nch3 (queued): 2 [白虎, 许春娘]\n")
    assert gb.format_hits(hits, chapters, count=True, match_tag=False) == \
        "ch1: 1\nch2: 2\nch3 (queued): 2\n"
    assert gb.format_hits(hits, chapters, files_only=True) == \
        "ch1\nch2\nch3 (queued)\n"
    ctx = gb.format_hits(hits[:1], chapters, context=2)
    assert ctx == (">ch1 [许春娘] [0] 许春娘走进村子。\n ch1 [1] \n ch1 [2] 她看见了莫林。\n"
                   + "-" * 60 + "\n")
    assert gb.format_hits([], chapters) == ""


def test_grep_build_matcher_raises():
    with pytest.raises(ValueError, match="bad regex"):
        gb.build_matcher("(", False, False)
    assert gb.build_matcher("(", True, False).search("a(b")


# --------------------------------------------------- backfill_origin_chapter


@pytest.fixture(autouse=True)
def isolated_exclusions(tmp_path, monkeypatch):
    """Never read the real backfill_origin_exclusions.json (os.path.join with an
    absolute second part discards the script directory)."""
    path = tmp_path / "exclusions.json"
    monkeypatch.setattr(bo, "EXCLUSIONS_FILE", str(path))
    return path


def test_load_exclusions_collects_warnings(isolated_exclusions, capsys):
    isolated_exclusions.write_text('{"7": ["花花"]}', encoding="utf-8")
    assert bo.load_exclusions(7, ["小黑"]) == {"花花", "小黑"}
    isolated_exclusions.write_text("{not json", encoding="utf-8")
    warnings = []
    assert bo.load_exclusions(7, ["小黑"], warnings=warnings) == {"小黑"}
    assert len(warnings) == 1 and warnings[0].startswith("WARNING: could not read")
    assert capsys.readouterr().out == ""


def test_exclusions_file_honoured_under_recompute(db, book, isolated_exclusions):
    isolated_exclusions.write_text('{"%d": ["莫林"]}' % book, encoding="utf-8")
    plan = bo.compute_origin_backfill(db, book, recompute=True, skip_keys=["白虎"])
    assert {e["untranslated"] for e in plan.skipped_excluded} == {"莫林", "白虎"}
    assert plan.changes == []


def test_compute_origin_backfill_is_dry(db, book):
    before = _origins(db, book)
    plan = bo.compute_origin_backfill(db, book)          # missing origins only
    assert _origins(db, book) == before                  # wrote nothing
    assert plan.scope == "missing"
    assert plan.chapters_scanned == 3 and (plan.first_chapter, plan.last_chapter) == (1, 3)
    assert plan.entities_considered == 2
    assert [(c["untranslated"], c["old"], c["new"]) for c in plan.changes] == \
        [("白虎", None, 3)]                              # found only in the queue
    assert set(plan.changes[0]) == {"entity_id", "untranslated", "translation",
                                    "category", "old", "new"}
    assert [u["untranslated"] for u in plan.unmatched] == ["龙珠"]
    assert plan.overwrites == 0

    no_queue = bo.compute_origin_backfill(db, book, include_queue=False)
    assert no_queue.changes == [] and len(no_queue.unmatched) == 2


def test_apply_origin_backfill_writes(db, book):
    plan = bo.compute_origin_backfill(db, book)
    assert bo.apply_origin_backfill(db, plan) == 1
    assert _origins(db, book)["白虎"] == 3


def test_compute_origin_backfill_recompute(db, book):
    plan = bo.compute_origin_backfill(db, book, recompute=True)
    assert plan.scope == "recompute"
    moved = {c["untranslated"]: (c["old"], c["new"]) for c in plan.changes}
    assert moved == {"莫林": (5, 1), "白虎": (None, 3)}
    assert plan.overwrites == 1
    assert [e["untranslated"] for e in plan.skipped_short] == ["剑"]
    assert plan.kept_later == []

    with_short = bo.compute_origin_backfill(db, book, recompute=True, include_short=True)
    assert ("剑", 9, 2) in [(c["untranslated"], c["old"], c["new"]) for c in with_short.changes]

    skipped = bo.compute_origin_backfill(db, book, recompute=True, skip_keys=["莫林"])
    assert [e["untranslated"] for e in skipped.skipped_excluded] == ["莫林"]
    assert "莫林" not in {c["untranslated"] for c in skipped.changes}

    # skip_keys only apply under recompute, like the exclusions file.
    assert bo.compute_origin_backfill(db, book, skip_keys=["白虎"]).changes[0]["untranslated"] == "白虎"


def test_compute_origin_backfill_all_and_kept_later(db, book):
    bo.set_origin_chapter(db, db.get_entity_id(book, "莫林"), None)
    plan = bo.compute_origin_backfill(db, book, category="characters", all_=True)
    assert plan.scope == "all" and plan.category == "characters"
    assert [(c["untranslated"], c["new"]) for c in plan.changes] == [("莫林", 1)]

    # A recorded origin EARLIER than the first appearance is never raised by recompute.
    bo.set_origin_chapter(db, db.get_entity_id(book, "白虎"), 1)
    rec = bo.compute_origin_backfill(db, book, recompute=True, category="creatures")
    assert [(k["untranslated"], k["old"], k["derived"]) for k in rec.kept_later] == \
        [("白虎", 1, 3)]
    assert rec.changes == []
