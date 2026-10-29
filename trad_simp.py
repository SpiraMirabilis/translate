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
#
# Masking runs BEFORE OpenCC, so each entry must match the text as it arrives.
# A partially-converted mirror (twkan serves 乾清宫 with the simplified 宫) and a
# fully traditional raw (乾清宮) both occur, so terms whose other characters
# differ between scripts are listed in both spellings.
# Each key matches the text as it ARRIVES; each value is what should survive.
# Masking runs before OpenCC, so a key may be traditional — but restoring it
# verbatim would also freeze the characters that ought to simplify (旋乾轉坤
# must keep its 乾 and still yield 转). Hence a mapping, not a plain list.
PROTECTED_TERMS = {
    "大乾": "大乾",
    # The Palace of Heavenly Purity — 76 occurrences in book 93 alone, against
    # zero for 干清宫. t2s folds it to 干清宫 without this.
    "乾清宮": "乾清宫",
    "乾清宫": "乾清宫",
    # …and every other 乾清 name: book 106's Taiwanese-sourced chapters lost
    # 乾清园 (a garden) and 乾清郡 (a commandery) to 干清. 乾清 is never gān-qīng
    # in these raws. Only the pair is masked, so a trailing 園/門/郡 still
    # simplifies through OpenCC.
    "乾清": "乾清",
    # 乾天古道 (book 106, ch294) came through as 干天古道: 乾天 is the Qian
    # trigram's heaven, never gān "dry sky".
    "乾天": "乾天",
    # 旋乾轉坤 "turn heaven and earth about": 乾 is the trigram, as in 乾坤,
    # but OpenCC's phrase dictionary only protects the bare pair.
    "旋乾轉坤": "旋乾转坤",
    "旋乾转坤": "旋乾转坤",
    # 乾綱獨斷 "to decide by the ruler's sole authority": same case as 旋乾轉坤 —
    # 乾 is the trigram (the sovereign/heaven), not 乾 gān "dry".
    "乾綱": "乾纲",
    "乾纲": "乾纲",
    # Given names built on the Qian hexagram's 元亨利貞.
    "乾亨": "乾亨",
    "徐乾學": "徐乾学",
    "徐乾学": "徐乾学",
    # Book 106's own qián terms (its simplified chapters 1-685 write them all
    # with 乾): the 乾朝 dynasty, 乾宫, the 乾转 "Heaven-Turning" family
    # (乾转界/乾转法/五渊乾转阵/八门乾转仪), the emperor 昊乾, the trigram
    # positions 乾位/乾门, and 丁乾 in its divination verses.
    "乾朝": "乾朝",
    "乾宮": "乾宫",
    "乾宫": "乾宫",
    "乾轉": "乾转",
    "乾转": "乾转",
    "昊乾": "昊乾",
    "乾位": "乾位",
    "乾門": "乾门",
    "乾门": "乾门",
    "丁乾": "丁乾",
    # 徵 is zhǐ, the fourth note of the pentatonic scale, not 征 "levy". OpenCC
    # knows 宫商角徵羽 and 變徵 but not the scale written with 、, nor book 106's
    # 徵音/徵神音 or the names 八徵日轮 and 血徵.
    "角、徵": "角、徵",
    "變徵": "变徵",
    "变徵": "变徵",
    "徵神音": "徵神音",
    "徵音": "徵音",
    "八徵": "八徵",
    "血徵": "血徵",
}

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
    # 著书 "to write books" only in fixed phrases: bare 著书 straddles far
    # too often (拿著書, 包裹著書本, 學著書里 — book 17) for a verb lookbehind.
    "著书立说", "道树著书", "道书著书",
)
# 著雍 is the Taisui year name; OpenCC leaves it alone and only our 著→着
# pass would break it.
ZHU_INITIAL = ("著名", "著作", "著称", "著述", "著者", "著录", "著雍")

# A 著 closing a 《book title》 is the verb zhù, "authored by" (《结丹心得——慎独道人著》),
# never the aspect particle — a particle cannot precede 》.
_ZHU_BEFORE_BRACKET = re.compile("著(?=》)")

# Verb-final characters that take the aspect particle 着. Not exhaustive — a
# heuristic, and the residue is a cosmetic 著/着 choice, never a meaning change.
_PARTICLE_VERBS = (
    "划写论编笑说讲念想拿带看跟随靠沿顺朝举指喊叫哭忙等望盯坐站躺趴走跑"
    "抱握提挂穿戴摸摆摇睁闭张闻听数捧扛背拖拉推顶托捏抓踩踏骑抬瞧搂拽"
    "理挥奔"
)
# The verb list can never be complete, so the other side of the boundary is
# checked too: 着 + 名单/名额/名字 and 着 + 作用/作业/作战 are common word
# pairs that read as 著名/著作 only by straddling. (梳理着名单, 发挥着作用,
# 奔着名额 — book 106's Taiwanese-sourced ch265/281.) 著名单位 / 著名单曲
# ("a famous unit/single") are real zhù uses, hence the inner lookahead.
_ZHU_NOT_FOLLOWED = {
    "著名": "(?!单(?![位曲])|额|字)",
    "著作": "(?!用|业|战)",
}
_ZHU_INITIAL_RE = re.compile(
    "(?<![" + _PARTICLE_VERBS + "])("
    + "|".join(w + _ZHU_NOT_FOLLOWED.get(w, "") for w in ZHU_INITIAL) + ")"
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


def _mask_map(text, mapping, table):
    """Like ``_mask``, but the sentinel restores the term's *corrected* form.

    Longest keys first, so an entry cannot be shadowed by a shorter one that
    overlaps it.
    """
    for term in sorted(mapping, key=len, reverse=True):
        if term in text:
            table.append(mapping[term])
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


def _keep_bmp(before, after):
    """Undo any conversion that lands outside the Basic Multilingual Plane.

    t2s maps some rare characters to the 2013 standard's extension-plane
    simplifications (聻→𫆏, 騊駼→𫘦𬳿, 纁→𫄸), which common fonts lack and which
    simplified web novels don't use — book 106's simplified raws write 聻 and
    騊駼. t2s is character-for-character, so position i of the output is the
    conversion of position i of the input; a length change means a phrase
    rewrite we can't align, and the output is kept as is.
    """
    if len(before) != len(after):
        return after
    return "".join(
        b if ord(a) > 0xFFFF and ord(b) <= 0xFFFF else a
        for b, a in zip(before, after)
    )


def _convert_one(text):
    table = []
    # The masking sentinels use PUA codepoints U+E000/U+E001. If the input
    # already contains them (corrupt scrape, font-hack glyphs), _unmask would
    # misinterpret them and could raise or splice wrong text — strip them
    # first; they are never legitimate novel content.
    if "\ue000" in text or "\ue001" in text:
        text = text.replace("\ue000", "").replace("\ue001", "")
    # Shield the qián proper nouns before OpenCC sees them — t2s folds 乾 to 干.
    text = _mask_map(text, PROTECTED_TERMS, table)
    text = _keep_bmp(text, _get_converter().convert(text))
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


_s2t = None


def to_traditional(text):
    """Simplified → Traditional (plain OpenCC s2t), for searching sites that
    store whichever form the submitter typed — NovelUpdates' search doesn't
    convert. Never used on stored text: chapters stay as t2s left them."""
    global _s2t
    if not text:
        return text
    if _s2t is None:
        from opencc import OpenCC
        _s2t = OpenCC('s2t')
    return _s2t.convert(text)
