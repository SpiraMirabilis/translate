"""Tests for trad_simp.convert_text (OpenCC t2s + guardrails).

The guardrails exist because character-level trad→simp cannot resolve two
morpheme splits on its own:

  * 乾 = gān "dry" → 干, but 乾 = qián (trigram) stays 乾.
  * 著 = aspect particle → 着, but 著 = zhù "notable/author" stays 著.

Regression guard: the converter used to be OpenCC ``tw2sp``, whose Taiwan
vocabulary layer corrupted mainland text (什么→什幺, 核心→内核, 位元婴→比特婴).
"""
import pytest

opencc = pytest.importorskip("opencc")

from trad_simp import convert_text


@pytest.mark.parametrize("src,expected", [
    # Plain traditional → simplified.
    ("崎嶇通道", "崎岖通道"),
    ("龍鬚掛麵", "龙须挂面"),
    ("崑崙城", "昆仑城"),
    ("錕鋙山", "锟铻山"),
    ("考核指標", "考核指标"),
    ("模擬各种动物", "模拟各种动物"),
])
def test_basic_conversion(src, expected):
    assert convert_text(src) == expected


@pytest.mark.parametrize("src", [
    # tw2sp regressions: mainland vocabulary must survive untouched.
    "什么", "怎么", "那么",
    "陆阳抬头",
    "核心弟子",
    "那位元婴老怪",   # 位元 must not be read as the computing term "bit"
    "智慧、灵根",
    "程序化",
    "水火不相容",
    "建立了问道宗",
    "物件上不使用钉子",
    "本月新增教徒",
    "宣告这间仓库",
])
def test_mainland_vocabulary_untouched(src):
    assert convert_text(src) == src


@pytest.mark.parametrize("src,expected", [
    # 乾 as gān "dry" → 干.
    ("乾净", "干净"),
    ("乾脆", "干脆"),
    ("一乾二净", "一干二净"),
    ("口乾舌燥", "口干舌燥"),
    ("乾燥", "干燥"),
    # 乾 as qián (trigram) stays 乾.
    ("大乾王朝", "大乾王朝"),
    ("大乾时期", "大乾时期"),
    ("乾坤袋", "乾坤袋"),
])
def test_qian_vs_gan(src, expected):
    assert convert_text(src) == expected


@pytest.mark.parametrize("src,expected", [
    # 著 as aspect particle → 着.
    ("看著书", "看着书"),
    ("背负著", "背负着"),
    ("雨哗啦啦的下著", "雨哗啦啦的下着"),
    # 著 as zhù stays 著.
    ("著名的", "著名的"),
    ("他的著作", "他的著作"),
    ("成绩显著", "成绩显著"),
    ("土著居民", "土著居民"),
    # Both readings in one line.
    ("看著著名的原著", "看着著名的原著"),
])
def test_zhe_vs_zhu(src, expected):
    assert convert_text(src) == expected


@pytest.mark.parametrize("src,expected", [
    # A zhù bigram must not be protected when it straddles a word boundary:
    # the 著 belongs to the preceding verb, and the next char starts a new word.
    ("比划著名什么", "比划着名什么"),      # 比划+着, not 著名
    ("暗中谋划著名什么", "暗中谋划着名什么"),
    ("古籍写著作者的名字", "古籍写着作者的名字"),  # 写+着, not 著作
    ("他笑著称赞", "他笑着称赞"),           # 笑+着, not 著称
    ("讨论著事情", "讨论着事情"),           # 讨论+着, not 论著
    ("都是编著玩的", "都是编着玩的"),        # 编+着, not 编著
    # …while the genuine zhù readings still survive.
    ("以御力著称", "以御力著称"),
    ("这可是仙人著作的", "这可是仙人著作的"),
    ("理解经文著作者的心境", "理解经文著作者的心境"),
    ("抢她著作权", "抢她著作权"),
    ("效果显著", "效果显著"),
    ("臭名昭著的盗贼", "臭名昭著的盗贼"),
])
def test_zhu_word_boundary_straddle(src, expected):
    assert convert_text(src) == expected


@pytest.mark.parametrize("src,expected", [
    # 著 closing a 《book title》 is the verb zhù, "authored by" — never the particle.
    ("《结丹心得——慎独道人著》", "《结丹心得——慎独道人著》"),
    ("《丹经·某某真人著》", "《丹经·某某真人著》"),
    # ...but a particle elsewhere in the same line still converts.
    ("他看著《结丹心得——慎独道人著》", "他看着《结丹心得——慎独道人著》"),
])
def test_zhu_before_closing_bracket(src, expected):
    assert convert_text(src) == expected


def test_idempotent():
    src = "看著書，乾净的大乾王朝，什么著名原著，乾坤袋"
    once = convert_text(src)
    assert convert_text(once) == once


def test_shapes_preserved():
    assert convert_text(None) is None
    assert convert_text(["乾净", "看著"]) == ["干净", "看着"]
    assert convert_text(123) == 123


def test_no_sentinel_leakage():
    """Private-use masking chars must never survive into output."""
    out = convert_text("大乾的著名原著，看著乾坤")
    assert "" not in out and "" not in out
    assert out == "大乾的著名原著，看着乾坤"
