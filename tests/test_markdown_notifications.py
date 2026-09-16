"""Tests for the markdown_notifications module's pure transforms — the
bracket-span scanner, multi-notification lines (several 【…】 / [...] packed into
one paragraph), single-line and multi-line notifications, the source-side
splitter, and reversal on disable."""

from modules.markdown_notifications_module import (
    MarkdownNotificationsModule,
    _bracket_spans,
    _fold_speaker_line,
    _fold_speaker_lines,
    _from_tables,
    _multi_notif,
    _notif,
    _split_notif_lines,
    _to_tables,
)

SPEAKERS = {"Molly", "茉莉", "Chen Yiran", "陳一然"}


class TestBracketSpans:
    def test_splits_two_ascii_spans(self):
        assert _bracket_spans("[A] [B]") == ["[A]", "[B]"]

    def test_splits_full_width_spans(self):
        assert _bracket_spans("【甲】【乙】") == ["【甲】", "【乙】"]

    def test_nested_span_is_one_span(self):
        assert _bracket_spans("【Li Yu: 【Video】】") == ["【Li Yu: 【Video】】"]

    def test_prose_around_a_span_is_none(self):
        assert _bracket_spans("Molty said, 【Relax.】") is None
        assert _bracket_spans("[A] and [B]") is None

    def test_unbalanced_is_none(self):
        assert _bracket_spans("【Mission issued: teach her") is None

    def test_blank_and_non_string_are_none(self):
        assert _bracket_spans("   ") is None
        assert _bracket_spans(None) is None


class TestMultiNotif:
    def test_two_notifications_yield_two_cells(self):
        line = "[System status: abnormal startup] [Detecting runtime environment...]"
        assert _multi_notif(line) == ["System status: abnormal startup",
                                      "Detecting runtime environment..."]

    def test_single_notification_is_not_multi(self):
        assert _multi_notif("[Scanning host memory index...]") is None

    def test_numeric_span_in_run_is_left_alone(self):
        # Footnote markers / danmaku counts, not notifications.
        assert _multi_notif("[1] [2]") is None
        assert _multi_notif("[Note] [3]") is None

    def test_greedy_single_match_would_have_swallowed_the_line(self):
        # Regression guard: _notif alone matches the whole line as ONE cell.
        line = "[A] [B]"
        assert _notif(line) == "A] [B"
        assert _multi_notif(line) == ["A", "B"]


class TestToTables:
    def test_multi_span_line_becomes_one_row_each(self):
        lines = ["[Status: ok] [Scanning...]"]
        assert _to_tables(lines) == [
            "| Status: ok |",
            "| --- |",
            "| Scanning... |",
        ]

    def test_multi_span_line_merges_with_following_notifications(self):
        lines = [
            "[Status: ok] [Scanning...]",
            "",
            "[Warning: low power]",
        ]
        assert _to_tables(lines) == [
            "| Status: ok |",
            "| --- |",
            "| Scanning... |",
            "| Warning: low power |",
        ]

    def test_single_notification_unchanged_behaviour(self):
        assert _to_tables(["【Mission Reward: Heart】"]) == [
            "| Mission Reward: Heart |",
            "| --- |",
        ]

    def test_prose_with_two_bracketed_terms_untouched(self):
        lines = ["Molty said, 【Relax.】 then 【Breathe.】 quietly."]
        assert _to_tables(lines) == lines

    def test_idempotent(self):
        once = _to_tables(["[Status: ok] [Scanning...]"])
        assert _to_tables(once) == once

    def test_multiline_block_still_matches(self):
        lines = [
            "【Mission Issued: teach her swordsmanship.",
            "",
            "Mission duration is fifty years.",
            "",
            "Upon settlement, the richer the rewards.】",
        ]
        assert _to_tables(lines) == [
            "| Mission Issued: teach her swordsmanship. |",
            "| --- |",
            "| Mission duration is fifty years. |",
            "| Upon settlement, the richer the rewards. |",
        ]


class TestSplitNotifLines:
    def test_splits_into_blank_separated_paragraphs(self):
        assert _split_notif_lines(["[A] [B]"]) == ["[A]", "", "[B]"]

    def test_preserves_trailing_hard_break(self):
        assert _split_notif_lines(["[甲] [乙]  "]) == ["[甲]  ", "", "[乙]  "]

    def test_leaves_single_notifications_and_prose_alone(self):
        lines = ["【Yes.】", "", "Molty said, 【Relax.】", "", "[1]"]
        assert _split_notif_lines(lines) is lines

    def test_leaves_table_rows_alone(self):
        rows = ["| Status: ok |", "| --- |", "| Scanning... |"]
        assert _split_notif_lines(rows) is rows

    def test_idempotent(self):
        once = _split_notif_lines(["[A] [B]"])
        assert _split_notif_lines(once) is once

    def test_non_list_passthrough(self):
        assert _split_notif_lines("not a list") == "not a list"

    def test_module_source_hook_splits(self):
        mod = MarkdownNotificationsModule()
        assert mod.transform_source_lines(["[A] [B]"], {}) == ["[A]", "", "[B]"]


class TestFoldSpeaker:
    def test_folds_english_label_keeping_colon_space(self):
        line = "Molly: 【Because you were tested twice, you took longer.】"
        assert _fold_speaker_line(line, SPEAKERS) == (
            "【Molly: Because you were tested twice, you took longer.】")

    def test_folds_chinese_label_keeping_full_width_colon(self):
        line = "茉莉：【因為你考了兩次，時間比其他人長。】"
        assert _fold_speaker_line(line, SPEAKERS) == (
            "【茉莉：因為你考了兩次，時間比其他人長。】")

    def test_folds_ascii_bracket_notification(self):
        assert _fold_speaker_line("Molly: [Status: ok]", SPEAKERS) == (
            "[Molly: Status: ok]")

    def test_preserves_trailing_hard_break(self):
        assert _fold_speaker_line("茉莉：【好。】  ", SPEAKERS) == "【茉莉：好。】  "

    def test_narration_clause_is_not_folded(self):
        # Same shape, but the prefix is not a speaker — the narration must stay
        # outside the box.
        for line in ("看完之後，茉莉說：【去吃飯吧。】",
                     "Molly popped up again, angling for attention: 【Are you ill?】",
                     "茉莉安慰道：【放心吧。】"):
            assert _fold_speaker_line(line, SPEAKERS) == line

    def test_unknown_speaker_is_not_folded(self):
        line = "Kevin: 【Hello.】"
        assert _fold_speaker_line(line, SPEAKERS) == line

    def test_no_gate_folds_nothing(self):
        line = "Molly: 【Hello.】"
        assert _fold_speaker_line(line, None) == line
        assert _fold_speaker_line(line, set()) == line

    def test_unbracketed_message_is_left_to_the_chatgroup_module(self):
        line = "Molly: Because you were tested twice."
        assert _fold_speaker_line(line, SPEAKERS) == line

    def test_already_folded_is_idempotent(self):
        once = _fold_speaker_line("Molly: 【Hello.】", SPEAKERS)
        assert _fold_speaker_line(once, SPEAKERS) == once

    def test_lines_helper_returns_original_on_no_op(self):
        lines = ["Just prose.", "【Yes.】"]
        assert _fold_speaker_lines(lines, SPEAKERS) is lines

    def test_folded_line_becomes_a_table_row(self):
        folded = _fold_speaker_lines(["Molly: 【Hello.】"], SPEAKERS)
        assert _to_tables(folded) == ["| Molly: Hello. |", "| --- |"]


class TestFromTables:
    def test_reversal_emits_one_paragraph_per_row(self):
        # Lossy by design: two notifications that shared a line come back as
        # two paragraphs, not one line.
        tables = _to_tables(["[Status: ok] [Scanning...]"])
        assert _from_tables(tables) == ["【Status: ok】", "", "【Scanning...】"]


class TestFoldAnyLabel:
    """The per-book "fold any label" setting: the entity gate is replaced by a
    structural one, for books whose notifications are introduced by a thing
    (`Bullet comments:`, `弹幕：`) rather than by a character."""

    BULLET = ("Bullet comments: 【Okay, got it, so it's grandma spoiling him. "
              "Then you've got to sort out things with your mother-in-law first, "
              "and get your husband to step in.】")

    def test_folds_a_non_entity_label(self):
        assert _fold_speaker_line(self.BULLET, None, True) == (
            "【Bullet comments: Okay, got it, so it's grandma spoiling him. Then "
            "you've got to sort out things with your mother-in-law first, and get "
            "your husband to step in.】")

    def test_gated_mode_leaves_the_same_line_alone(self):
        assert _fold_speaker_line(self.BULLET, SPEAKERS) == self.BULLET

    def test_folded_line_becomes_a_table(self):
        folded = _fold_speaker_lines(["弹幕：【卧槽，什么情况。】"], None, True)
        assert _to_tables(folded) == ["| 弹幕：卧槽，什么情况。 |", "| --- |"]

    def test_works_without_a_db_gate(self):
        # The entity gate is not consulted at all, so no-db context still folds.
        assert _fold_speaker_line("System: 【Online.】", None, True) == (
            "【System: Online.】")

    def test_long_label_is_folded_too(self):
        # Strictly looser by design: with the setting on, a narrative clause
        # ending in a colon goes into the box as well.
        assert _fold_speaker_line("茉莉安慰道：【放心吧。】", None, True) == (
            "【茉莉安慰道：放心吧。】")

    def test_idempotent(self):
        once = _fold_speaker_line(self.BULLET, None, True)
        assert _fold_speaker_line(once, None, True) == once

    def test_requires_the_colon(self):
        assert _fold_speaker_line("Bullet comments 【Yes.】", None, True) == (
            "Bullet comments 【Yes.】")

    def test_empty_label_is_not_folded(self):
        assert _fold_speaker_line("  : 【Yes.】", None, True) == "  : 【Yes.】"

    def test_unbracketed_remainder_is_not_folded(self):
        assert _fold_speaker_line("He said: something.", None, True) == (
            "He said: something.")

    def test_table_row_is_not_folded(self):
        row = "| Bullet comment: Master Zhang 6666666, want to join! |"
        assert _fold_speaker_line(row, None, True) == row

    def test_preserves_trailing_hard_break(self):
        assert _fold_speaker_line("弹幕：【好。】  ", None, True) == "【弹幕：好。】  "

    def test_setting_drives_the_transform_hook(self):
        mod = MarkdownNotificationsModule()
        ctx = {"module_settings": {mod.id: {"fold_any_label": True}}}
        assert mod.transform_translated_lines([self.BULLET], ctx)[0].startswith(
            "| Bullet comments: Okay")
        # Default (setting absent) keeps the entity-gated behaviour: no db in
        # ctx means no gate, so nothing is folded and the line is left as prose.
        assert mod.transform_translated_lines([self.BULLET], {}) == [self.BULLET]
