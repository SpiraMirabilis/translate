"""
Scan translated chapters for chat-style [tag] markers used in IM dialogue
(e.g. "Zhang Yu: [thumbs up] Don't worry...") and audit which ones are real
emojis vs. WeChat-style system notices (photo/recalled/transfer) vs. link-card
titles. Optionally rewrite real-emoji tags to their Unicode equivalents.

Usage:
    python emoji_audit.py [--book-id 15]              # report only
    python emoji_audit.py --book-id 15 --show-context # report + sample lines per tag
    python emoji_audit.py --book-id 15 --apply        # rewrite chapters in place
    python emoji_audit.py --book-id 15 --apply --dry-run
"""

import argparse
import json
import re
import sys
from collections import Counter, defaultdict

from config import TranslationConfig
from logger import Logger
from database import DatabaseManager


# ---------------------------------------------------------------------------
# Manual alias table: WeChat / QQ-style descriptive tag -> Unicode emoji.
# Keys are case-insensitive; punctuation/spacing normalized before lookup.
# ---------------------------------------------------------------------------
EMOJI_MAP = {
    "confetti":            "🎉",
    "fireworks":           "🎆",
    "thumbs up":           "👍",
    "strong":              "💪",
    "smile":               "😊",
    "flowers":             "🌹",
    "heart":               "❤️",
    "laugh-cry":           "😂",
    "shocked":             "😮",
    "smug":                "😏",
    "eye-roll":            "🙄",
    "hmph to the side":    "😤",
    "puzzled":             "🤔",
    "applause":            "👏",
    "palms together":      "🙏",
    "cupped fists":        "🙏",
    "surprised":           "😲",
    "zipped lips face":    "🤐",
    "snickers":            "🤭",
    "snickering":          "🤭",
    "sneaky chuckle":      "🤭",
    "cracking apart":      "😵",
    "bitter":              "😣",
    "crying":              "😭",
    "weeping":             "😭",
    "terrified":           "😱",
    "wiping sweat":        "😅",
    "face-palm":           "🤦",
    "face-palm laugh":     "🤦",
    "anxious":             "😰",
    "excited":             "🤩",
    "omg":                 "😱",
    "kowtow":              "🙇",
    "vomiting":            "🤮",
    "vomit":               "🤮",
    "furious":             "😡",
    "fury":                "😡",
    "angry":               "😡",
    "rose":                "🌹",
    "dog-head":            "🐶",
    "fighting":            "💪",
    "bawling bawling bawling": "😭😭😭",
    # Surfaced from 【】-bracket sweep:
    "fist":                "👊",
    "fire":                "🔥",
    "shh":                 "🤫",
    "pitiful":             "🥺",
    "tears":               "😢",
    "smiley face":         "😀",
    "stomp":               "😤",
    "fear":                "😨",
    "evil grin":           "😈",
    "pleased":             "😊",
    "silly grin":          "😄",
    "question":            "❓",
}


# Tags that are WeChat/QQ system notices or attachment cards — NOT emojis.
# We deliberately leave these alone (they're part of the storytelling).
SYSTEM_TAGS = {
    "recalled", "photo", "image", "video", "file", "link", "group link",
    "video link", "transfer", "transfer record", "red packet",
    "payment received", "transfer received", "recipient has accepted the payment",
    "pinned comment", "storm warning",
}


# Match both ASCII [tag] and full-width 【tag】 (used in CN-style chat clients).
TAG_RE = re.compile(r'[\[【]([^\]】\n]+)[\]】]')
# Pulls the line a tag lives on so we can show it as context.
LINE_OF_TAG = re.compile(r'[^\n]*[\[【][^\]】\n]+[\]】][^\n]*')


def normalize(tag: str) -> str:
    return tag.strip().lower()


def classify(tag_norm: str) -> str:
    if tag_norm in EMOJI_MAP:
        return "emoji"
    if tag_norm in SYSTEM_TAGS:
        return "system"
    # Money transfer cards: "[3 spirit coins]", "[0.1 spirit coin transfer]",
    # "[1 spirit coin]", "[10,000 immortal coins]", "[100 immortal coins]".
    if re.match(r'^[\d,]+(\.\d+)?\s+(spirit|immortal)\s+coin', tag_norm):
        return "system"
    if ("spirit coin" in tag_norm or "immortal coin" in tag_norm) and (
        "transfer" in tag_norm or "red packet" in tag_norm
    ):
        return "system"
    # Click-prompt link cards.
    if tag_norm.startswith("click "):
        return "system"
    # Image/screenshot file names ("[ranking screenshot.image file]").
    if "image file" in tag_norm or "screenshot" in tag_norm:
        return "system"
    # Bare "[1]" — likely a money-transfer placeholder.
    if tag_norm.isdigit():
        return "system"
    return "card_title"


def scan_book(dm, book_id, want_context):
    """
    Returns:
        tag_counts: Counter[normalized_tag -> int]
        examples:  dict[normalized_tag -> list[(chapter_num, raw_line)]]
        per_chapter: list[(chapter_id, chapter_num, text_with_brackets_lines)]
    """
    tag_counts = Counter()
    examples = defaultdict(list)

    chapters = dm.list_chapters(book_id)
    if not chapters:
        print(f"No chapters found for book {book_id}.", file=sys.stderr)
        sys.exit(1)

    for ch in chapters:
        c = dm.get_chapter(chapter_id=ch['id'])
        if not c:
            continue
        content = c.get('content', [])
        text = '\n'.join(content) if isinstance(content, list) else str(content)
        for line in text.split('\n'):
            for tag in TAG_RE.findall(line):
                norm = normalize(tag)
                tag_counts[norm] += 1
                if want_context and len(examples[norm]) < 2:
                    examples[norm].append((ch['chapter'], line.strip()))
    return tag_counts, examples


def apply_replacements(dm, book_id, dry_run):
    """
    Rewrite real-emoji tags to Unicode emojis. Only touches tags found in
    EMOJI_MAP; system tags and card titles are left alone.

    Replacement is case-insensitive on the tag body but preserves the rest of
    the line untouched.
    """
    # Build one big regex that matches any known emoji tag in [brackets] or 【brackets】.
    alts = sorted(EMOJI_MAP.keys(), key=len, reverse=True)
    pat = re.compile(
        r'[\[【](' + '|'.join(re.escape(a) for a in alts) + r')[\]】]',
        re.IGNORECASE,
    )

    def sub(m):
        return EMOJI_MAP[m.group(1).lower()]

    chapters = dm.list_chapters(book_id)
    total_subs = 0
    chapters_touched = 0
    for ch in chapters:
        c = dm.get_chapter(chapter_id=ch['id'])
        if not c:
            continue
        content = c.get('content', [])
        is_list = isinstance(content, list)
        text = '\n'.join(content) if is_list else str(content)
        new_text, n = pat.subn(sub, text)
        if n == 0:
            continue
        total_subs += n
        chapters_touched += 1
        print(f"  ch{c['chapter']:>4}: {n} replacement(s)")
        if dry_run:
            continue
        # Persist: re-serialize in the same shape the DB stored
        new_content = new_text.split('\n') if is_list else new_text
        conn = dm.backend.get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "UPDATE chapters SET translated_content = ? WHERE id = ?",
                (json.dumps(new_content, ensure_ascii=False), c['id']),
            )
            conn.commit()
        finally:
            conn.close()

    print(f"\n{'(dry-run) ' if dry_run else ''}Replaced {total_subs} tag(s) "
          f"across {chapters_touched} chapter(s).")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--book-id", type=int, default=15)
    ap.add_argument("--show-context", action="store_true",
                    help="Print one or two sample sentences for each tag")
    ap.add_argument("--apply", action="store_true",
                    help="Rewrite chapters: replace real-emoji tags with Unicode")
    ap.add_argument("--dry-run", action="store_true",
                    help="With --apply, show what would change without writing")
    args = ap.parse_args()

    config = TranslationConfig()
    logger = Logger(config)
    dm = DatabaseManager(config, logger)

    tag_counts, examples = scan_book(dm, args.book_id, args.show_context)

    buckets = defaultdict(list)
    for tag, n in tag_counts.items():
        buckets[classify(tag)].append((tag, n))
    for v in buckets.values():
        v.sort(key=lambda x: -x[1])

    total_uses = sum(tag_counts.values())
    print(f"Book {args.book_id}: {len(tag_counts)} unique tags, "
          f"{total_uses} total uses across IM lines.\n")

    def print_section(title, rows, *, with_mapping=False):
        if not rows:
            return
        print(f"=== {title} ({len(rows)} unique, {sum(n for _, n in rows)} uses) ===")
        for tag, n in rows:
            mapping = f"  →  {EMOJI_MAP[tag]}" if with_mapping else ""
            print(f"  {n:>4}  [{tag}]{mapping}")
            if args.show_context:
                for ch_num, line in examples[tag]:
                    if len(line) > 110:
                        line = line[:107] + "..."
                    print(f"            ch{ch_num}: {line}")
        print()

    print_section("Real emojis (will be rewritten by --apply)",
                  buckets["emoji"], with_mapping=True)
    print_section("System / attachment tags (left alone)", buckets["system"])
    print_section("Link-card titles / proper nouns (left alone)",
                  buckets["card_title"])

    if args.apply:
        print("\nApplying replacements..." + (" [DRY RUN]" if args.dry_run else ""))
        apply_replacements(dm, args.book_id, args.dry_run)


if __name__ == "__main__":
    main()
