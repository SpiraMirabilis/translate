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


# ---------------------------------------------------------------------------
# Bracket-aware marker placement: a marker must never split a bracketed or
# quoted title (《Title》[1], not 《Tit[1]le》), while ordinary trailing
# punctuation is left alone.
# ---------------------------------------------------------------------------
def _place(line, term):
    i = line.find(term)
    pos = fn.marker_position(line, i, i + len(term))
    return line[:pos] + "[1]" + line[pos:]


def test_marker_hops_cjk_book_brackets():
    line = "Xiao Mo imitated 《Rhapsody on the Epang Palace》, then wrote his own."
    assert _place(line, "Rhapsody on the Epang Palace") == (
        "Xiao Mo imitated 《Rhapsody on the Epang Palace》[1], then wrote his own.")


def test_marker_hops_single_quotes():
    line = "The song 'King of Qin Shattering Battle Lines' spread."
    assert _place(line, "King of Qin Shattering Battle Lines") == (
        "The song 'King of Qin Shattering Battle Lines'[1] spread.")


def test_marker_hops_nested_pairs():
    assert _place('read 《"Nested Title"》 aloud', "Nested Title") == 'read 《"Nested Title"》[1] aloud'


def test_marker_hops_parentheses():
    assert _place("(Parenthetical Term) follows", "Parenthetical Term") == "(Parenthetical Term)[1] follows"


def test_marker_hops_lenticular_brackets():
    # Item/system-message brackets in Chinese web novels — book 8's food cards
    # are 〘…〙, and 【…】 is the usual system-prompt wrapper.
    assert _place("I drew a 〘Food Card — Snail Noodles〙!", "Food Card — Snail Noodles") == (
        "I drew a 〘Food Card — Snail Noodles〙[1]!")
    assert _place("【Skill Acquired】 flashed past", "Skill Acquired") == "【Skill Acquired】[1] flashed past"


def test_marker_does_not_hop_on_partial_anchor():
    # A partial anchor ends mid-name with no closer to hop, so the marker stays
    # put rather than jumping a bracket it was never inside.
    assert _place("I drew a 〘Food Card — Snail Noodles〙!", "Snail Noodles") == (
        "I drew a 〘Food Card — Snail Noodles[1]〙!")


def test_marker_does_not_hop_trailing_period():
    # A period is not a closer: the marker belongs before it.
    line = 'He wrote "Decree Extending Grace." Then he slept.'
    assert _place(line, "Decree Extending Grace") == 'He wrote "Decree Extending Grace[1]." Then he slept.'


def test_marker_does_not_hop_unpaired_closer():
    # Closer present but no matching opener before the term -> no hop.
    assert _place("sword Non-Aggression) in hand", "Non-Aggression") == "sword Non-Aggression[1]) in hand"


def test_marker_plain_term_unchanged():
    assert _place("the long sword Non-Aggression in hand", "Non-Aggression") == (
        "the long sword Non-Aggression[1] in hand")


def test_render_footnotes_places_marker_outside_brackets():
    lines = ["Xiao Mo wrote 《Prelude to Water Melody》, tweaking it."]
    rows = [{"id": 1, "anchor": "Prelude to Water Melody", "body": "Su Shi's ci.", "occurrence": 1}]
    out, orphans = fn.render_footnotes(lines, rows)
    assert orphans == []
    assert out[0] == "Xiao Mo wrote 《Prelude to Water Melody》[1], tweaking it."


def test_occurrence_at_is_inverse_of_find_occurrence_with_brackets():
    # occurrence_at must apply the same hop, or a marker sitting outside 》 would
    # fail to map back to the term inside it.
    prose = ["a 《Title》 here", "and 《Title》 again"]
    for occ in (1, 2):
        li, col = fn.find_occurrence(prose, "Title", occ)
        assert fn.occurrence_at(prose, "Title", li, col) == occ
