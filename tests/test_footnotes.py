"""Behavior lock-in tests for footnotes.py."""
import footnotes as fn


# ── content_to_list ────────────────────────────────────────────────


def test_content_to_list_passes_through_list():
    assert fn.content_to_list(["a", "b"]) == ["a", "b"]


def test_content_to_list_parses_json_array_string():
    assert fn.content_to_list('["a", "b"]') == ["a", "b"]


def test_content_to_list_splits_plain_string():
    assert fn.content_to_list("a\nb") == ["a", "b"]


def test_content_to_list_none_yields_single_empty_line():
    assert fn.content_to_list(None) == [""]


def test_content_to_list_json_scalar_string():
    # A JSON scalar parses, then is stringified and split.
    assert fn.content_to_list('"scalar"') == ["scalar"]


# ── split_prose_and_defs / strip_footnotes ─────────────────────────


def test_split_prose_and_defs():
    lines = ["prose[1] here", "", "[1] def one", "[2] def two"]
    prose, defs = fn.split_prose_and_defs(lines)
    assert prose == ["prose[1] here"]
    assert defs == {1: "def one", 2: "def two"}


def test_split_prose_and_defs_no_footnotes():
    lines = ["just prose", "more prose"]
    prose, defs = fn.split_prose_and_defs(lines)
    assert prose == ["just prose", "more prose"]
    assert defs == {}


def test_strip_footnotes_removes_defs_and_markers():
    lines = ["prose[1] here", "", "[1] def one"]
    assert fn.strip_footnotes(lines) == ["prose here"]


def test_strip_footnotes_clean_chapter_untouched():
    lines = ["clean prose", "second line"]
    assert fn.strip_footnotes(lines) == ["clean prose", "second line"]


# ── find_occurrence ────────────────────────────────────────────────


def test_find_occurrence_counts_across_lines():
    # 3rd occurrence of "ab" is on the second line; returns col just past it.
    assert fn.find_occurrence(["ab ab", "ab"], "ab", 3) == (1, 2)


def test_find_occurrence_first_match_position():
    assert fn.find_occurrence(["xx anchor yy"], "anchor", 1) == (0, 9)


def test_find_occurrence_missing_returns_none():
    assert fn.find_occurrence(["ab"], "ab", 2) is None
    assert fn.find_occurrence(["ab"], "zz", 1) is None


def test_find_occurrence_empty_anchor_returns_none():
    assert fn.find_occurrence(["ab"], "", 1) is None


# ── render_footnotes ───────────────────────────────────────────────

LINES = ["The Calabash Brothers rushed in.", "", "Later they left."]


def test_render_no_rows_passes_prose_through():
    out, orphans = fn.render_footnotes(LINES, [])
    assert out == LINES
    assert orphans == []


def test_render_single_footnote_appends_definition():
    rows = [{"anchor": "Calabash Brothers", "body": "a 1980s cartoon",
             "occurrence": 1}]
    out, orphans = fn.render_footnotes(LINES, rows)
    assert out == [
        "The Calabash Brothers[1] rushed in.",
        "",
        "Later they left.",
        "",
        "[1] a 1980s cartoon",
    ]
    assert orphans == []


def test_render_renumbers_in_reading_order():
    # Rows supplied out of document order are renumbered by position.
    rows = [
        {"anchor": "left", "body": "second note", "occurrence": 1, "id": 1},
        {"anchor": "rushed", "body": "first note", "occurrence": 1, "id": 2},
    ]
    out, orphans = fn.render_footnotes(LINES, rows)
    assert out == [
        "The Calabash Brothers rushed[1] in.",
        "",
        "Later they left[2].",
        "",
        "[1] first note",
        "[2] second note",
    ]
    assert orphans == []


def test_render_reports_orphans():
    rows = [{"anchor": "missing term", "body": "x"}]
    out, orphans = fn.render_footnotes(LINES, rows)
    assert out == LINES
    assert [r["anchor"] for r in orphans] == ["missing term"]


def test_render_discards_old_markers_table_is_authoritative():
    lines = ["Foo[1] bar.", "", "[1] old note"]
    out, orphans = fn.render_footnotes(lines, [{"anchor": "bar",
                                                "body": "new note"}])
    assert out == ["Foo bar[1].", "", "[1] new note"]
    assert orphans == []


def test_render_accepts_json_string_content():
    raw = '["Foo bar.", "", "Baz."]'
    out, _ = fn.render_footnotes(raw, [{"anchor": "Baz", "body": "note"}])
    assert out == ["Foo bar.", "", "Baz[1].", "", "[1] note"]


# ── renumber_chapter (legacy inline renumberer) ────────────────────


def test_renumber_inserts_new_footnote_before_existing_orphan_def():
    # An existing def with no marker in prose is re-appended after new ones.
    lines = ["Alpha beta gamma.", "", "[1] existing def"]
    items = [("beta", "new body", 0, 10)]
    out, final_for_item, changed = fn.renumber_chapter(lines, items)
    assert out == ["Alpha beta[1] gamma.", "", "[1] new body",
                   "[2] existing def"]
    assert final_for_item == {0: 1}
    assert changed is False


def test_renumber_continues_existing_numbering():
    lines = ["Alpha[1] beta gamma.", "", "[1] existing def"]
    items = [("gamma", "gamma note", 0, 19)]
    out, final_for_item, changed = fn.renumber_chapter(lines, items)
    assert out == ["Alpha[1] beta gamma[2].", "", "[1] existing def",
                   "[2] gamma note"]
    assert final_for_item == {0: 2}
    assert changed is False
