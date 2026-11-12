"""Site Ad Stripper (modules/twkan.py) with its default patterns.

The ad lines are real ones from the scraper's cross-source check (chapter 1 of
我在精神病院學斬神 and 天官賜福 from twkan, uukanshu and novels.com.tw). Each
case runs in Traditional and in Simplified, because Trad→Simp runs before this
module for books that convert.
"""
import pytest

from modules.twkan import TwkanModule, _parse_patterns
from trad_simp import convert_text


def strip(lines):
    """Run the module with no stored settings, i.e. the schema defaults."""
    return TwkanModule().transform_source_lines(list(lines), {"module_settings": {}})


SCRIPTS = [pytest.param(lambda s: s, id="trad"), pytest.param(convert_text, id="simp")]


@pytest.mark.parametrize("script", SCRIPTS)
@pytest.mark.parametrize("ad, cleaned", [
    # twkan: 69shux marker spliced into a story line, with a trailing 閱讀
    ("炎炎八月。𝟨𝟫𝙨𝙝𝙪𝙭.𝙘𝙤𝙢閱讀", "炎炎八月。"),
    # twkan: marker inside the closing quote
    ("「哦。𝟨𝟫𝕤𝕙𝕦𝕩.𝕔𝕠𝕞」", "「哦。」"),
    # uukanshu: literal backslash-n plus a full-width COM after the sentence
    ("炎炎八月。.\\nＣＯＭ", "炎炎八月。"),
])
def test_inline_markers_are_cut_and_the_sentence_kept(script, ad, cleaned):
    assert strip(["前一行。", script(ad), "後一行。"]) == ["前一行。", script(cleaned), "後一行。"]


@pytest.mark.parametrize("script", SCRIPTS)
@pytest.mark.parametrize("ad", [
    "【記住本站域名 讀台灣好書上台灣小說網，ⓣⓦⓚⓐⓝ.ⓒⓞⓜ超省心 】",  # twkan banner
    "這章沒有結束，請點擊下一頁繼續閱讀！",                          # twkan page-break notice
    "繁體小說網首發",                                                # novels.com.tw watermark
])
def test_ad_lines_are_deleted(script, ad):
    assert strip(["前一行。", script(ad), "後一行。"]) == ["前一行。", "後一行。"]


@pytest.mark.parametrize("script", SCRIPTS)
@pytest.mark.parametrize("line", [
    "他繼續閱讀這本書，直到深夜。",
    "“我懂了，他在cos盲僧！”",
    "這章的內容還沒有結束。",
    "他打開網頁，輸入了一個.com結尾的網址。",
    "這本繁體小說很好看。",
])
def test_real_text_is_left_alone(script, line):
    lines = ["前一行。", script(line)]
    assert strip(lines) == lines


def test_idempotent():
    once = strip(["炎炎八月。𝟨𝟫𝙨𝙝𝙪𝙭.𝙘𝙤𝙢閱讀", "繁體小說網首發", "正文。"])
    assert strip(once) == once == ["炎炎八月。", "正文。"]


def test_default_comment_lines_are_not_patterns():
    for field in TwkanModule.settings_schema:
        default = field["default"]
        assert any(l.startswith("#") for l in default.splitlines()), field["key"]
        patterns = _parse_patterns(default)
        assert patterns and not any(p.startswith("#") for p in patterns), field["key"]
