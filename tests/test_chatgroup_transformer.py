"""chatgroup_transformer: line-level wrap/unwrap, incl. bracketed usernames."""
from modules.chatgroup_transformer_module import _unwrap_line, _wrap_line

NAMES = {"Monkeys Have Dignity Too", "猴子也有尊严", "Umaru", "小埋", "Mr. Li (Admin)"}


class TestPlainUsername:
    def test_wraps_gated_name(self):
        assert _wrap_line("Umaru: hi", NAMES) == "【Umaru: hi】"

    def test_unknown_name_untouched(self):
        assert _wrap_line("Molly: hi", NAMES) == "Molly: hi"


class TestBracketedUsername:
    def test_english(self):
        line = "【Monkeys Have Dignity Too】: Who stole my banana?"
        assert _wrap_line(line, NAMES) == "【Monkeys Have Dignity Too: Who stole my banana?】"

    def test_chinese_fullwidth_colon(self):
        assert _wrap_line("【猴子也有尊严】：谁偷了我的香蕉？", NAMES) == "【猴子也有尊严：谁偷了我的香蕉？】"

    def test_name_with_punctuation(self):
        assert _wrap_line("【Mr. Li (Admin)】: Quiet.", NAMES) == "【Mr. Li (Admin): Quiet.】"

    def test_entity_stored_with_brackets(self):
        assert _wrap_line("【Bob】: yo", {"【Bob】"}) == "【Bob: yo】"

    def test_gate_rejects_non_entity_label(self):
        line = "【Food Appraisal】: An excellent chef must…"
        assert _wrap_line(line, NAMES) == line

    def test_bracketed_message_nests(self):
        assert _wrap_line("【Umaru】: 【Thumbs Up】", NAMES) == "【Umaru: 【Thumbs Up】】"

    def test_idempotent(self):
        once = _wrap_line("【Monkeys Have Dignity Too】: hi", NAMES)
        assert _wrap_line(once, NAMES) == once

    def test_no_gate_wraps_shape(self):
        assert _wrap_line("【Anyone】: hi", None) == "【Anyone: hi】"

    def test_empty_message_untouched(self):
        assert _wrap_line("【Umaru】:", NAMES) == "【Umaru】:"

    def test_reverse_drops_brackets(self):
        once = _wrap_line("【Mr. Li (Admin)】: Quiet.", NAMES)
        assert _unwrap_line(once, NAMES) == "Mr. Li (Admin): Quiet."

    def test_reverse_ungated_leaves_punctuated_notification(self):
        line = "【Mission Reward, Rare: Heart】"
        assert _unwrap_line(line, None) == line
