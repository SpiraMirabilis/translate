"""Traditional → Simplified Chinese conversion via OpenCC.

The OpenCC import is lazy so the optional dependency is only required when
the feature is actually enabled. The converter instance is cached for the
process lifetime (construction is expensive, conversion is cheap).

Config choice: ``t2s``, not ``tw2sp``. The raws this feature exists for are
mainland novels that some upstream mirror mechanically rendered into
traditional glyphs — the *vocabulary* underneath is already mainland, so there
are no Taiwan phrases (軟體/螢幕/解析度) to undo. ``tw2sp``'s phrase layer
therefore has nothing legitimate to do and actively corrupts correct text:
什么→什幺, 抬→擡, 核心→内核, 智慧→智能, 程序→进程, 建立→创建, and 位元婴→比特婴
(matching 位元 as the computing term "bit" straight across a word boundary).

Two things ``t2s`` cannot get right on its own, both handled below:

* **乾** is two morphemes. As *gān* ("dry") it simplifies to 干 (乾净→干净);
  as the *qián* trigram it stays 乾 in simplified Chinese. OpenCC's phrase
  dictionary protects 乾坤 but not proper nouns like the dynasty 大乾.
* **著** splits by reading. The aspect particle is written 着 in simplified
  (看著→看着); as *zhù* ("notable", "to author") it stays 著 (著名, 著作).
  ``t2s`` leaves every 著 alone, so we convert and spare the *zhù* words.
"""

import re

_converter = None

# Proper nouns whose 乾 is the qián trigram, not gān "dry". Simplified keeps 乾.
# These are shielded from OpenCC entirely.
PROTECTED_TERMS = (
    "大乾",
)

# 著 stays 著 (zhù) in these; everywhere else it is the aspect particle 着.
#
# Two shapes, because they fail differently. Where 著 is the *second* char the
# bigram cannot straddle a word boundary — its first char is part of the word.
# Where 著 is the *first* char, any preceding verb can produce a false match:
# 比划著名什么 is 比划+着 followed by 名什么, not the adjective 著名; likewise
# 写著作者 (写+着 作者) and 笑著称赞 (笑+着 称赞). Those are guarded by a
# negative lookbehind on verbs that take the aspect particle.
#
# 论著 and 编著 are deliberately absent: they straddle on their own first char
# (讨论+著事情, 编+著玩的) so no lookbehind can save them. They are rare enough
# that losing the protection costs less than the false positives.
ZHU_MEDIAL = (
    "显著", "土著", "巨著", "原著", "名著", "昭著", "卓著", "专著", "遗著",
)
ZHU_INITIAL = ("著名", "著作", "著称", "著述", "著者", "著录")

# A 著 closing a 《book title》 is the verb zhù, "authored by" (《结丹心得——慎独道人著》),
# never the aspect particle — a particle cannot precede 》.
_ZHU_BEFORE_BRACKET = re.compile("著(?=》)")

# Verb-final characters that take the aspect particle 着. Not exhaustive — a
# heuristic, and the residue is a cosmetic 著/着 choice, never a meaning change.
_PARTICLE_VERBS = (
    "划写论编笑说讲念想拿带看跟随靠沿顺朝举指喊叫哭忙等望盯坐站躺趴走跑"
    "抱握提挂穿戴摸摆摇睁闭张闻听数捧扛背拖拉推顶托捏抓踩踏骑抬瞧搂拽"
)
_ZHU_INITIAL_RE = re.compile(
    "(?<![" + _PARTICLE_VERBS + "])(" + "|".join(ZHU_INITIAL) + ")"
)

# Private-use codepoints: OpenCC passes them through untouched.
_SENTINEL = "{}"
_SENTINEL_RE = re.compile("(\\d+)")


def _get_converter():
    global _converter
    if _converter is None:
        try:
            from opencc import OpenCC
        except ImportError as e:
            raise ImportError(
                "OpenCC is required for traditional→simplified conversion. "
                "Install with: pip install opencc-python-reimplemented"
            ) from e
        _converter = OpenCC('t2s')
    return _converter


def _mask(text, terms, table):
    """Swap each term for a sentinel, recording it in ``table``."""
    for term in terms:
        if term in text:
            table.append(term)
            text = text.replace(term, _SENTINEL.format(len(table) - 1))
    return text


def _unmask(text, table):
    return _SENTINEL_RE.sub(lambda m: table[int(m.group(1))], text)


def _mask_re(text, pattern, table):
    """Swap each regex match for a sentinel, recording it in ``table``."""
    def sub(m):
        table.append(m.group(0))
        return _SENTINEL.format(len(table) - 1)
    return pattern.sub(sub, text)


def _convert_one(text):
    table = []
    # Shield 大乾 before OpenCC sees it — t2s would fold 乾 to 干.
    text = _mask(text, PROTECTED_TERMS, table)
    text = _get_converter().convert(text)
    # Shield the zhù words (in their post-conversion form), then normalise the
    # remaining 著 to the simplified aspect particle 着.
    text = _mask(text, ZHU_MEDIAL, table)
    text = _mask_re(text, _ZHU_INITIAL_RE, table)
    text = _mask_re(text, _ZHU_BEFORE_BRACKET, table)
    text = text.replace("著", "着")
    return _unmask(text, table)


def convert_text(value):
    """Convert traditional Chinese characters to simplified.

    Accepts a string or a list of strings; returns the same shape.
    Idempotent — running on already-simplified text is a no-op.
    """
    if value is None:
        return value
    if isinstance(value, list):
        return [_convert_one(line) if isinstance(line, str) else line for line in value]
    if isinstance(value, str):
        return _convert_one(value)
    return value
