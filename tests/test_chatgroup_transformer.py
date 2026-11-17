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


class TestUserIdSuffix:
    IDS = {"吴军", "Wu Jun"}

    def test_source_fullwidth(self):
        line = "吴军（20）：“好家伙，大佬装逼真是越来越熟练了。”"
        assert _wrap_line(line, self.IDS) == "【" + line + "】"

    def test_translated_halfwidth(self):
        line = 'Wu Jun (20): "Wow, the boss is getting smoother at showing off."'
        assert _wrap_line(line, self.IDS) == "【" + line + "】"

    def test_no_space_before_paren(self):
        assert _wrap_line("Wu Jun(20): hi", self.IDS) == "【Wu Jun(20): hi】"

    def test_unknown_name_with_id_untouched(self):
        line = "李四（20）：“你好。”"
        assert _wrap_line(line, self.IDS) == line

    def test_non_numeric_suffix_not_stripped(self):
        assert _wrap_line("吴军（群主）：你好", self.IDS) == "吴军（群主）：你好"

    def test_bracketed_with_id(self):
        assert _wrap_line("【吴军（20）】：你好", self.IDS) == "【吴军（20）：你好】"

    def test_reverse(self):
        line = "吴军（20）：“好家伙。”"
        assert _unwrap_line(_wrap_line(line, self.IDS), self.IDS) == line

    def test_idempotent(self):
        once = _wrap_line("吴军（20）：“好家伙。”", self.IDS)
        assert _wrap_line(once, self.IDS) == once


class TestMention:
    IDS = {"方云", "Fang Yun", "吴军"}

    def test_translated(self):
        line = ('Fang Yun (3) @Wang Ping (66): "So how\'d it go, newcomer? '
                'What cheat ability did you draw?"')
        assert _wrap_line(line, self.IDS) == "【" + line + "】"

    def test_source(self):
        line = "方云（3）@王平（66）：“新人，抽到了什么金手指？”"
        assert _wrap_line(line, self.IDS) == "【" + line + "】"

    def test_fullwidth_at(self):
        line = "方云（3）＠王平（66）：你好"
        assert _wrap_line(line, self.IDS) == "【" + line + "】"

    def test_target_need_not_be_entity(self):
        assert _wrap_line("方云@全体成员：开会", self.IDS) == "【方云@全体成员：开会】"

    def test_multiple_mentions(self):
        line = "Fang Yun (3) @Wang Ping (66) @Li Si (7): hi"
        assert _wrap_line(line, self.IDS) == "【" + line + "】"

    def test_unknown_speaker_untouched(self):
        line = "Wang Ping (66) @Fang Yun (3): hi"
        assert _wrap_line(line, self.IDS) == line

    def test_empty_target_untouched(self):
        assert _wrap_line("方云@：你好", self.IDS) == "方云@：你好"

    def test_reverse_and_idempotent(self):
        line = "方云（3）@王平（66）：“你好。”"
        once = _wrap_line(line, self.IDS)
        assert _wrap_line(once, self.IDS) == once
        assert _unwrap_line(once, self.IDS) == line
