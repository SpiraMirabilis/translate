#!/usr/bin/env python3
"""
Surface entity translations that look like unresolved pinyin transliterations.

Heuristic: tokenize each translation, drop punctuation and a small whitelist of
common English / xianxia-naturalized words, then check whether the remaining
tokens decompose into 2+ pinyin syllables. Anything that decomposes cleanly
and isn't whitelisted gets flagged as a candidate for human review.

Output is grouped by category, sorted by origin chapter.
"""

import os
import re
import sys
import warnings
from collections import defaultdict

warnings.filterwarnings("ignore", category=FutureWarning)
os.environ.pop("DEBUG", None)

from config import TranslationConfig
from database import DatabaseManager
from logger import Logger

PINYIN_SYLLABLES = {
    # a-, o-, e-
    "a", "ai", "an", "ang", "ao",
    "o", "ou",
    "e", "ei", "en", "eng", "er",
    # b-
    "ba", "bai", "ban", "bang", "bao", "bei", "ben", "beng", "bi", "bian",
    "biao", "bie", "bin", "bing", "bo", "bu",
    # p-
    "pa", "pai", "pan", "pang", "pao", "pei", "pen", "peng", "pi", "pian",
    "piao", "pie", "pin", "ping", "po", "pou", "pu",
    # m-
    "ma", "mai", "man", "mang", "mao", "me", "mei", "men", "meng", "mi",
    "mian", "miao", "mie", "min", "ming", "miu", "mo", "mou", "mu",
    # f-
    "fa", "fan", "fang", "fei", "fen", "feng", "fo", "fou", "fu",
    # d-
    "da", "dai", "dan", "dang", "dao", "de", "dei", "den", "deng", "di",
    "dian", "diao", "die", "ding", "diu", "dong", "dou", "du", "duan", "dui",
    "dun", "duo",
    # t-
    "ta", "tai", "tan", "tang", "tao", "te", "teng", "ti", "tian", "tiao",
    "tie", "ting", "tong", "tou", "tu", "tuan", "tui", "tun", "tuo",
    # n-
    "na", "nai", "nan", "nang", "nao", "ne", "nei", "nen", "neng", "ni",
    "nian", "niang", "niao", "nie", "nin", "ning", "niu", "nong", "nou",
    "nu", "nuan", "nuo", "nv", "nve",
    # l-
    "la", "lai", "lan", "lang", "lao", "le", "lei", "leng", "li", "lia",
    "lian", "liang", "liao", "lie", "lin", "ling", "liu", "long", "lou",
    "lu", "luan", "lue", "lun", "luo", "lv", "lve",
    # g-
    "ga", "gai", "gan", "gang", "gao", "ge", "gei", "gen", "geng", "gong",
    "gou", "gu", "gua", "guai", "guan", "guang", "gui", "gun", "guo",
    # k-
    "ka", "kai", "kan", "kang", "kao", "ke", "ken", "keng", "kong", "kou",
    "ku", "kua", "kuai", "kuan", "kuang", "kui", "kun", "kuo",
    # h-
    "ha", "hai", "han", "hang", "hao", "he", "hei", "hen", "heng", "hong",
    "hou", "hu", "hua", "huai", "huan", "huang", "hui", "hun", "huo",
    # j-
    "ji", "jia", "jian", "jiang", "jiao", "jie", "jin", "jing", "jiong",
    "jiu", "ju", "juan", "jue", "jun",
    # q-
    "qi", "qia", "qian", "qiang", "qiao", "qie", "qin", "qing", "qiong",
    "qiu", "qu", "quan", "que", "qun",
    # x-
    "xi", "xia", "xian", "xiang", "xiao", "xie", "xin", "xing", "xiong",
    "xiu", "xu", "xuan", "xue", "xun",
    # zh-
    "zha", "zhai", "zhan", "zhang", "zhao", "zhe", "zhei", "zhen", "zheng",
    "zhi", "zhong", "zhou", "zhu", "zhua", "zhuai", "zhuan", "zhuang", "zhui",
    "zhun", "zhuo",
    # ch-
    "cha", "chai", "chan", "chang", "chao", "che", "chen", "cheng", "chi",
    "chong", "chou", "chu", "chua", "chuai", "chuan", "chuang", "chui",
    "chun", "chuo",
    # sh-
    "sha", "shai", "shan", "shang", "shao", "she", "shei", "shen", "sheng",
    "shi", "shou", "shu", "shua", "shuai", "shuan", "shuang", "shui",
    "shun", "shuo",
    # r-
    "ran", "rang", "rao", "re", "ren", "reng", "ri", "rong", "rou", "ru",
    "rua", "ruan", "rui", "run", "ruo",
    # z-
    "za", "zai", "zan", "zang", "zao", "ze", "zei", "zen", "zeng", "zi",
    "zong", "zou", "zu", "zuan", "zui", "zun", "zuo",
    # c-
    "ca", "cai", "can", "cang", "cao", "ce", "cen", "ceng", "ci", "cong",
    "cou", "cu", "cuan", "cui", "cun", "cuo",
    # s-
    "sa", "sai", "san", "sang", "sao", "se", "sen", "seng", "si", "song",
    "sou", "su", "suan", "sui", "sun", "suo",
    # y-
    "ya", "yan", "yang", "yao", "ye", "yi", "yin", "ying", "yo", "yong",
    "you", "yu", "yuan", "yue", "yun",
    # w-
    "wa", "wai", "wan", "wang", "wei", "wen", "weng", "wo", "wu",
}

# Naturalized xianxia / cultural terms — intentionally kept as pinyin even
# though they don't appear in english dictionaries.
NATURALIZED_PINYIN = {
    "tao", "dao", "qi", "yin", "yang", "kung", "fu", "gong", "qigong",
    "taichi", "taiji", "kungfu", "wushu", "wuxia", "xianxia", "wuxing",
    "bagua", "baguazhang", "neigong", "neidan", "waidan", "fengshui",
    "lingqi", "shen", "hun",
    # sci-fi naturalized
    "mecha", "anime", "manga", "kanji", "shuriken",
    # translator's house-style choices for this corpus
    "mana", "dantian",
}


def _load_english_words() -> set[str]:
    paths = [
        "/usr/share/dict/american-english",
        "/usr/share/dict/words",
    ]
    for p in paths:
        if os.path.exists(p):
            with open(p, encoding="utf-8", errors="ignore") as f:
                words = set()
                for line in f:
                    w = line.strip().lower()
                    # Strip possessive suffix "'s" so "azure's" matches "azure".
                    if w.endswith("'s"):
                        w = w[:-2]
                    if w and w.isalpha():
                        words.add(w)
                return words
    return set()


ENGLISH_WORDS = _load_english_words()

CATEGORY_SKIP = {"characters"}  # Names are intentionally transliterated.

WORD_RE = re.compile(r"[A-Za-z]+(?:-[A-Za-z]+)*")


def is_pinyin_syllable_chain(word: str) -> tuple[bool, int]:
    """Try to decompose `word` into pinyin syllables (longest-match greedy).

    Returns (success, syllable_count). Decomposition is greedy from the left,
    preferring longer syllables.
    """
    w = word.lower()
    if not w:
        return False, 0
    syllables = 0
    i = 0
    n = len(w)
    while i < n:
        matched = None
        # Try syllable lengths from longest (6) down to shortest (1).
        for L in range(min(6, n - i), 0, -1):
            chunk = w[i:i + L]
            if chunk in PINYIN_SYLLABLES:
                matched = chunk
                break
        if matched is None:
            return False, 0
        syllables += 1
        i += len(matched)
    return True, syllables


def split_words(text: str) -> list[str]:
    out = []
    for tok in WORD_RE.findall(text):
        # Split hyphenated compounds.
        out.extend(tok.split("-"))
    return [w for w in out if w]


def is_camelcase_pinyin(word: str) -> tuple[bool, int]:
    """Detect 'TianQin' or 'BiGu' style — internal capitals splitting pinyin."""
    if not word or word.islower() or word.isupper():
        return False, 0
    # Split on internal uppercase boundaries.
    parts = re.findall(r"[A-Z][a-z]*", word)
    if len(parts) < 2:
        return False, 0
    total = 0
    for p in parts:
        ok, n = is_pinyin_syllable_chain(p)
        if not ok:
            return False, 0
        total += n
    return True, total


def looks_like_pinyin(word: str, name_pinyin: set[str]) -> bool:
    """Conservative test: multi-syllable pinyin that isn't English and isn't a known
    character/place name from this book."""
    lw = word.lower()
    if len(lw) < 4:
        return False  # 1-3 letter tokens are too noisy (Tao, Yin, Qi, Wu...).
    if lw in ENGLISH_WORDS:
        return False
    if lw in NATURALIZED_PINYIN:
        return False
    if lw in name_pinyin:
        return False
    # Try camelcase split first (BiGu, TianQin).
    ok, n = is_camelcase_pinyin(word)
    if ok and n >= 2:
        return True
    ok, n = is_pinyin_syllable_chain(lw)
    if not ok:
        return False
    if n < 2:
        return False
    return True


def flag_translation(translation: str, name_pinyin: set[str]) -> list[str]:
    """Return the list of pinyin-looking words in this translation."""
    if not translation:
        return []
    flagged = []
    for w in split_words(translation):
        if looks_like_pinyin(w, name_pinyin):
            flagged.append(w)
    return flagged


def main():
    book_id = int(sys.argv[1]) if len(sys.argv) > 1 else 15

    config = TranslationConfig()
    logger = Logger(config)
    db_manager = DatabaseManager(config, logger)

    conn = db_manager.backend.get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT category, untranslated, translation, origin_chapter
            FROM entities
            WHERE (book_id = ? OR book_id IS NULL)
            """,
            (book_id,),
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    # Build a pool of character-name pinyin tokens — these are OK to leave
    # transliterated, even when they appear in non-character entities.
    name_pinyin: set[str] = set()
    for row in rows:
        if row[0] != "characters":
            continue
        translation = row[2] or ""
        for w in split_words(translation):
            lw = w.lower()
            if lw in ENGLISH_WORDS:
                continue
            ok, n = is_pinyin_syllable_chain(lw)
            if ok and n >= 1:
                name_pinyin.add(lw)

    by_cat: dict[str, list] = defaultdict(list)
    total_seen = 0
    total_flagged = 0
    for row in rows:
        category, untranslated, translation, origin_chapter = row[0], row[1], row[2], row[3]
        if category in CATEGORY_SKIP:
            continue
        total_seen += 1
        hits = flag_translation(translation or "", name_pinyin)
        if not hits:
            continue
        total_flagged += 1
        by_cat[category].append((origin_chapter or 0, untranslated, translation, hits))

    for cat in sorted(by_cat):
        entries = sorted(by_cat[cat], key=lambda r: (r[0], r[1]))
        print(f"\n== {cat} ({len(entries)} flagged) ==")
        for origin, untranslated, translation, hits in entries:
            ch = f"ch{origin}" if origin else "ch?"
            hits_str = ", ".join(sorted(set(hits)))
            print(f"  {ch:>6}  {untranslated} : {translation}    [{hits_str}]")

    print(
        f"\n# scanned {total_seen} non-character entities; flagged {total_flagged} "
        f"({100*total_flagged/max(total_seen,1):.1f}%)",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
