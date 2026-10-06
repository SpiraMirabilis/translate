"""Tests for the markdown_notifications module's pure transforms — the
bracket-span scanner, multi-notification lines (several 【…】 / [...] packed into
one paragraph), single-line and multi-line notifications, the trailing-description
fold, stat panels, the source-side splitter, and reversal on disable."""

from modules.markdown_notifications_module import (
    MarkdownNotificationsModule,
    _bracket_spans,
    _fold_speaker_line,
    _fold_speaker_lines,
    _fold_trailing_line,
    _fold_trailing_lines,
    _from_tables,
    _multi_notif,
    _notif,
    _panel_section,
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


class TestFoldTrailingText:
    """The per-book "fold the description after the colon" setting — the mirror
    image of the speaker fold, for `【Skill】: description` lines."""

    LINE = ("【Food Appraisal】: An excellent chef must master the ability to "
            "select ingredients. You have obtained this ability.")

    def test_folds_the_description_in(self):
        assert _fold_trailing_line(self.LINE) == (
            "【Food Appraisal: An excellent chef must master the ability to "
            "select ingredients. You have obtained this ability.】")

    def test_preserves_the_full_width_colon_and_its_spacing(self):
        assert _fold_trailing_line("【財富商城】：財富值達到1000開啟。") == (
            "【財富商城：財富值達到1000開啟。】")

    def test_ascii_brackets_fold_too(self):
        assert _fold_trailing_line("[Skills]: Cooking, haggling.") == (
            "[Skills: Cooking, haggling.]")

    def test_empty_tail_is_left_for_the_panel_rule(self):
        assert _fold_trailing_line("【Professional Skills】:") == (
            "【Professional Skills】:")

    def test_bracketed_tail_is_two_notifications_not_a_description(self):
        assert _fold_trailing_line("【A】: 【B】") == "【A】: 【B】"

    def test_numeric_ascii_label_is_left_alone(self):
        # A footnote marker or a Markdown link reference definition.
        assert _fold_trailing_line("[1]: https://example.com") == (
            "[1]: https://example.com")

    def test_narration_before_the_bracket_is_untouched(self):
        line = "Zhou Yan looked: 【A pile of unfresh pork】"
        assert _fold_trailing_line(line) == line

    def test_prose_mentioning_a_bracketed_term_is_untouched(self):
        line = "With 【Food Appraisal】 running, every ingredient was laid bare."
        assert _fold_trailing_line(line) == line

    def test_unclosed_bracket_is_left_to_the_multiline_path(self):
        line = "【Mission issued: teach her swordsmanship"
        assert _fold_trailing_line(line) == line

    def test_nested_span_folds_through_the_outer_closer(self):
        assert _fold_trailing_line("【Li Yu: 【Video】】: watch it.") == (
            "【Li Yu: 【Video】: watch it.】")

    def test_preserves_trailing_hard_break(self):
        assert _fold_trailing_line("【財富商城】：開啟。  ") == "【財富商城：開啟。】  "

    def test_idempotent(self):
        once = _fold_trailing_line(self.LINE)
        assert _fold_trailing_line(once) == once

    def test_folded_line_becomes_a_table(self):
        folded = _fold_trailing_lines(["【Wealth Shop】: Unlocks at 1000 Wealth."])
        assert _to_tables(folded) == [
            "| Wealth Shop: Unlocks at 1000 Wealth. |", "| --- |"]

    def test_identity_on_no_op(self):
        lines = ["plain prose", "【Boxed】"]
        assert _fold_trailing_lines(lines) is lines

    def test_setting_drives_the_transform_hook(self):
        mod = MarkdownNotificationsModule()
        ctx = {"module_settings": {mod.id: {"fold_trailing_text": True}}}
        assert mod.transform_translated_lines([self.LINE], ctx)[0].startswith(
            "| Food Appraisal: An excellent chef")
        # Off by default: the label boxes up alone, the description stays prose.
        assert mod.transform_translated_lines([self.LINE], {}) == [self.LINE]


class TestStatPanel:
    """The per-book "absorb stat entries" setting: a 【Heading】: claims the
    unbracketed entries printed under it, so a system status panel boxes up
    whole instead of breaking at its first heading."""

    PANEL = [
        "【Player: Zhou Yan】",
        "",
        "【Profession: Chef】",
        "",
        "【Professional Skills】:",
        "",
        "Knife Work (Intermediate): 8604/10000 (Up to most dishes)",
        "",
        "Heat Control (Beginner): 668/1000 (Kid, you need more practice)",
        "",
        "【Dishes Mastered】:",
        "",
        "Twice-Cooked Pork (Beginner): 55/1000",
        "",
        "……",
        "",
        "【Main Quest: Become the God of Cookery!】",
        "",
        "The panel had changed noticeably since yesterday.",
    ]

    def test_whole_panel_becomes_one_table(self):
        assert _to_tables(self.PANEL, stat_panel=True) == [
            "| Player: Zhou Yan |",
            "| --- |",
            "| Profession: Chef |",
            "| Professional Skills: |",
            "| Knife Work (Intermediate): 8604/10000 (Up to most dishes) |",
            "| Heat Control (Beginner): 668/1000 (Kid, you need more practice) |",
            "| Dishes Mastered: |",
            "| Twice-Cooked Pork (Beginner): 55/1000 |",
            "| …… |",
            "| Main Quest: Become the God of Cookery! |",
            "",
            "The panel had changed noticeably since yesterday.",
        ]

    def test_off_by_default_the_panel_breaks_at_its_first_heading(self):
        out = _to_tables(self.PANEL)
        assert "| Professional Skills: |" not in out
        assert "【Professional Skills】:" in out
        assert "Knife Work (Intermediate): 8604/10000 (Up to most dishes)" in out

    def test_prose_after_the_panel_stays_outside(self):
        out = _to_tables(self.PANEL, stat_panel=True)
        assert out[-1] == "The panel had changed noticeably since yesterday."

    def test_section_ends_at_the_first_non_entry(self):
        lines = ["【Skills】:", "", "Knife Work (Intermediate): 8604/10000",
                 "", "He closed the panel and went back to the stove."]
        assert _panel_section(lines, 0) == (
            ["Skills:", "Knife Work (Intermediate): 8604/10000"], 2)

    def test_heading_with_no_entries_is_not_a_panel(self):
        lines = ["【Skills】:", "", "He closed the panel."]
        assert _panel_section(lines, 0) is None
        # …and so is left to the other matchers, which leave it as prose.
        assert _to_tables(lines, stat_panel=True) == lines

    def test_unbracketed_subheading_is_absorbed_when_an_entry_follows(self):
        lines = ["【Skills】:", "", "Knife Work (Intermediate): 8604/10000",
                 "", "Special Skills:", "",
                 "Food Appraisal (Master): 999999/1000000 (Cannot be upgraded.)"]
        cells, last = _panel_section(lines, 0)
        assert cells[2:] == [
            "Special Skills:",
            "Food Appraisal (Master): 999999/1000000 (Cannot be upgraded.)"]
        assert last == 6

    def test_dangling_subheading_is_dropped_and_ends_the_section(self):
        lines = ["【Skills】:", "", "Knife Work (Intermediate): 8604/10000",
                 "", "Then he thought:", "", "this was going to be hard."]
        assert _panel_section(lines, 0) == (
            ["Skills:", "Knife Work (Intermediate): 8604/10000"], 2)

    def test_entry_name_may_carry_its_own_colon(self):
        lines = ["【Skills】:", "",
                 "Special Skill: Food Appraisal (Master): 999999/1000000"]
        assert _panel_section(lines, 0)[0][1] == (
            "Special Skill: Food Appraisal (Master): 999999/1000000")

    def test_leading_ellipsis_does_not_start_a_panel(self):
        lines = ["【Skills】:", "", "……", "", "prose"]
        assert _panel_section(lines, 0) is None

    def test_entries_need_a_bracketed_heading_to_be_claimed(self):
        lines = ["Knife Work (Intermediate): 8604/10000", "", "prose"]
        assert _to_tables(lines, stat_panel=True) == lines

    def test_reversal_restores_the_panel(self):
        table = _to_tables(self.PANEL, stat_panel=True)
        assert _from_tables(table, stat_panel=True) == [
            "【Player: Zhou Yan】",
            "",
            "【Profession: Chef】",
            "",
            "【Professional Skills】:",
            "",
            "Knife Work (Intermediate): 8604/10000 (Up to most dishes)",
            "",
            "Heat Control (Beginner): 668/1000 (Kid, you need more practice)",
            "",
            "【Dishes Mastered】:",
            "",
            "Twice-Cooked Pork (Beginner): 55/1000",
            "",
            "……",
            "",
            "【Main Quest: Become the God of Cookery!】",
            "",
            "The panel had changed noticeably since yesterday.",
        ]

    def test_reversal_brackets_an_unbracketed_subheading(self):
        # One-way: the table stores 【Skills】: and a bare "Special Skills:"
        # identically, so the reversal picks the common (bracketed) form. It
        # re-converts to the same table, so the normalization is stable.
        lines = ["【Skills】:", "", "Knife Work (Intermediate): 8604/10000",
                 "", "Special Skills:", "",
                 "Food Appraisal (Master): 999999/1000000"]
        table = _to_tables(lines, stat_panel=True)
        back = _from_tables(table, stat_panel=True)
        assert back[4] == "【Special Skills】:"
        assert _to_tables(back, stat_panel=True) == table

    def test_a_lone_stat_shaped_notification_keeps_its_brackets(self):
        # No heading above it, so un-bracketing would not re-convert.
        table = _to_tables(["【Knife Work (Intermediate): 8604/10000】"],
                           stat_panel=True)
        back = _from_tables(table, stat_panel=True)
        assert back == ["【Knife Work (Intermediate): 8604/10000】"]
        assert _to_tables(back, stat_panel=True) == table

    def test_a_heading_shaped_notification_with_no_entries_keeps_its_brackets(self):
        table = _to_tables(["【Warning:】"], stat_panel=True)
        back = _from_tables(table, stat_panel=True)
        assert back == ["【Warning:】"]
        assert _to_tables(back, stat_panel=True) == table

    def test_reversal_without_the_setting_brackets_every_row(self):
        table = _to_tables(self.PANEL, stat_panel=True)
        assert "【Knife Work (Intermediate): 8604/10000 (Up to most dishes)】" in (
            _from_tables(table))

    def test_forward_conversion_is_idempotent(self):
        once = _to_tables(self.PANEL, stat_panel=True)
        assert _to_tables(once, stat_panel=True) == once

    def test_setting_drives_the_transform_hook(self):
        mod = MarkdownNotificationsModule()
        ctx = {"module_settings": {mod.id: {"stat_panel": True}}}
        out = mod.transform_translated_lines(self.PANEL, ctx)
        assert "| Knife Work (Intermediate): 8604/10000 (Up to most dishes) |" in out
        assert "| Professional Skills: |" in out
