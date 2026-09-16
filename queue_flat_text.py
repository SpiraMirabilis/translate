#!/usr/bin/env python3
"""Parse a flat-text novel into per-chapter translation-queue items.

Chapters are delimited by a header line matching ``^第<number>章 <title>``.
The number may be Arabic (``第4章  漏财引祸``) or Chinese (``第八十一章  无限延伸的洞穴``);
some raws mix both styles inside a single book.

Volumes
-------
Many raws are split into volumes (``第二卷 云游四海``) and **restart chapter
numbering at 第一章 in every volume**. A volume can also restart implicitly —
the number simply drops back to 1 with no ``卷`` line in between. Because a
book has one flat integer chapter_number, the source number cannot be used
directly: it collides across volumes and, in sloppy raws, is plain wrong
(duplicated or skipped by the author).

So numbering is controlled by --numbering:

  sequential (default)  Global 1..N in file order. File order is the reading
                        order; the source's own numbers are advisory only.
  source                Use the source number verbatim. Only safe for
                        single-volume raws with clean numbering; the script
                        refuses if that would produce duplicates.

With ``sequential``, the source label (volume + original chapter number) is
kept in the report and, with --number-titles, prefixed onto the queue title.

Usage:
    # Dry run (default): parse and report, write nothing
    python3 queue_flat_text.py --file scientist.txt --book-id 87 --dry-run

    # Show the volume/chapter map in full
    python3 queue_flat_text.py --file scientist.txt --book-id 87 --dry-run --show-map

    # Actually enqueue
    python3 queue_flat_text.py --file scientist.txt --book-id 87

Re-running is safe: chapter numbers already present in the queue for the
target book are skipped.
"""

import argparse
import hashlib
import json
import os
import re
import sys

from config import TranslationConfig
from database import DatabaseManager
from logger import Logger
from db_backend import create_backend
from illustrations import (IllustrationCollector, make_marker, ext_for_mime,
                           store_chapter_illustrations, _looks_decorative)

# Numerals that may appear in a 第…章 / 第…卷 header.
_NUM_CHARS = "0-9０-９〇零一二三四五六七八九十百千两"

# Header like: 第1章 妖魔乱世 / 第123章 标题 / 第一百零六章  暴力破阵
# Tolerate leading whitespace (incl. the ideographic space) — some raws indent.
CHAPTER_RE = re.compile(rf"^[\s　]*第\s*([{_NUM_CHARS}]{{1,12}})\s*章\s*(.*?)\s*$")
VOLUME_RE = re.compile(rf"^[\s　]*第\s*([{_NUM_CHARS}]{{1,12}})\s*卷\s*(.*?)\s*$")

_CN_DIGITS = {"〇": 0, "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000}


def parse_number(s):
    """Chinese or Arabic numeral string -> int (None if unparseable).

    Handles the forms these raws actually use: 五, 十, 十五, 九十五, 一百零六,
    二百一十, 三百零五, and the odd 第一十三章. Full-width digits too.
    """
    s = s.translate({ord(c): ord("0") + i for i, c in enumerate("０１２３４５６７８９")})
    if s.isdigit():
        return int(s)

    total = 0        # accumulated 百/千 sections
    section = 0      # current section being built
    number = 0       # pending digit
    for ch in s:
        if ch in _CN_DIGITS:
            number = _CN_DIGITS[ch]
        elif ch in _CN_UNITS:
            unit = _CN_UNITS[ch]
            if unit == 10:
                section += (number or 1) * 10
            else:
                section = (section + (number or 1)) * unit
                total += section
                section = 0
            number = 0
        else:
            return None
    return total + section + number


def parse_chapters(path):
    """Return (chapters, volumes).

    chapters: list of dicts with keys
        src_number   number as written in the source (per-volume)
        title        header text with the 第…章 prefix stripped
        raw_header   the full header line
        volume       1-based volume index this chapter belongs to
        line_no      1-based line number of the header
        content_lines
    volumes: list of dicts {index, label, title, line_no, explicit}
    """
    with open(path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()

    chapters = []
    volumes = []
    current = None
    prev_num = None

    def open_volume(label, title, line_no, explicit):
        volumes.append({"index": len(volumes) + 1, "label": label,
                        "title": title, "line_no": line_no, "explicit": explicit})

    for i, line in enumerate(lines, 1):
        vm = VOLUME_RE.match(line)
        if vm:
            open_volume(line.strip(), vm.group(2), i, True)
            prev_num = None
            if current is not None:
                chapters.append(current)
                current = None
            continue

        m = CHAPTER_RE.match(line)
        if m:
            num = parse_number(m.group(1))
            # Implicit volume break: numbering falls back to 1 (or otherwise
            # drops) with no 卷 line. Guard on num==1 only, so the author's
            # one-off misnumberings don't shatter the book into fake volumes.
            if volumes and num == 1 and prev_num is not None and prev_num != 1:
                open_volume(f"(unlabeled volume {len(volumes) + 1})", "", i, False)
            if not volumes:
                open_volume("(implicit volume 1)", "", i, False)

            if current is not None:
                chapters.append(current)
            current = {
                "src_number": num,
                "title": m.group(2).strip(),
                "raw_header": line.strip(),
                "volume": volumes[-1]["index"],
                "line_no": i,
                "content_lines": [],
            }
            prev_num = num
        elif current is not None:
            current["content_lines"].append(line)
        # lines before the first header (blurb//TOC) are ignored

    if current is not None:
        chapters.append(current)

    for ch in chapters:
        body = ch["content_lines"]
        while body and not body[0].strip():
            body.pop(0)
        while body and not body[-1].strip():
            body.pop()

    return chapters, volumes


def assign_numbers(chapters, mode):
    """Set ch['number'] according to the numbering mode."""
    if mode == "sequential":
        for n, ch in enumerate(chapters, 1):
            ch["number"] = n
    else:
        for ch in chapters:
            ch["number"] = ch["src_number"]
    return chapters


# --------------------------------------------------------------------------- #
# Illustrations
# --------------------------------------------------------------------------- #

IMG_TAG_RE = re.compile(r'<img\b[^>]*?\bsrc\s*=\s*["\']([^"\']+)["\'][^>]*>', re.I)


class StableCollector(IllustrationCollector):
    """IllustrationCollector with content-derived marker ids.

    The base class mints a random id per run, which is right for a one-shot
    EPUB/FB2 import. Here the queueing step may be re-run after a partial
    failure, so ids are derived from the image bytes instead: the same picture
    always gets the same ⟦IMG:id⟧, and store_chapter_illustrations (idempotent on
    (book_id, marker_id)) then stays idempotent across runs.
    """

    def add(self, image_bytes, mime=None, ext=None, alt=None, original_href=None):
        if not image_bytes:
            return None
        ext = ext or ext_for_mime(mime)
        if _looks_decorative(image_bytes, ext):
            return None
        digest = hashlib.sha1(image_bytes).hexdigest()
        marker_id = digest[:6]
        if marker_id not in self._items:
            self._by_hash[digest] = marker_id
            self._items[marker_id] = {
                "data": image_bytes, "ext": ext,
                "alt": alt, "original_href": original_href,
            }
        return marker_id


def load_illustrations(manifest_path):
    """Return (collector, {url: marker_id}) from a fetch_remote_illustrations manifest."""
    manifest_path = os.path.expanduser(manifest_path)
    with open(manifest_path, encoding='utf-8') as f:
        manifest = json.load(f)
    base = os.path.dirname(os.path.abspath(manifest_path))

    collector = StableCollector()
    url_to_marker = {}
    for url, meta in manifest.items():
        path = os.path.join(base, meta['filename'])
        with open(path, 'rb') as f:
            data = f.read()
        marker_id = collector.add(data, mime=meta.get('mime'), alt=meta.get('alt'),
                                  original_href=url)
        if marker_id is None:
            print(f"  skipping decorative image: {url}")
            continue
        url_to_marker[url] = marker_id
    return collector, url_to_marker


def apply_illustration_markers(chapters, url_to_marker):
    """Replace every <img src=…> line with its ⟦IMG:id⟧ marker line, in place.

    A marker must occupy a whole line (illustrations.MARKER_RE is anchored), so
    the original line — leading ideographic spaces and all — is replaced rather
    than edited, and a line carrying several <img> tags becomes several lines.
    """
    replaced = missing = 0
    for ch in chapters:
        out = []
        for line in ch['content_lines']:
            urls = IMG_TAG_RE.findall(line)
            if not urls:
                out.append(line)
                continue
            for url in urls:
                marker_id = url_to_marker.get(url)
                if marker_id is None:
                    print(f"  WARNING no downloaded image for {url} (ch{ch['number']}); "
                          f"dropping the tag", file=sys.stderr)
                    missing += 1
                    continue
                out.append(make_marker(marker_id))
                replaced += 1
        ch['content_lines'] = out
    return replaced, missing


def existing_queue_numbers(book_id):
    """Chapter numbers already queued for this book (for idempotent re-runs)."""
    backend = create_backend()
    conn = backend.get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT chapter_number FROM queue WHERE book_id = ? AND chapter_number IS NOT NULL",
        (book_id,),
    )
    nums = {row[0] for row in cur.fetchall()}
    conn.close()
    return nums


def existing_chapter_numbers(db, book_id):
    """Chapter numbers already saved as real chapters for this book."""
    rows = db.list_chapters(book_id) or []
    return {r["chapter"] for r in rows if r.get("chapter") is not None}


def report(chapters, volumes, path, book, args):
    numbers = [c["number"] for c in chapters]
    src_numbers = [c["src_number"] for c in chapters]

    print(f"Book {book['id']}: {book['title']}")
    print(f"Parsed {len(chapters)} chapters from {path}")
    print(f"  {len(volumes)} volume(s); numbering mode: {args.numbering}")
    print()

    for v in volumes:
        vchs = [c for c in chapters if c["volume"] == v["index"]]
        if not vchs:
            continue
        src = [c["src_number"] for c in vchs]
        tag = "" if v["explicit"] else "   <- no 卷 line, detected by restart"
        print(f"  vol {v['index']}: {v['label']}{tag}")
        print(f"    line {v['line_no']}, {len(vchs)} chapters, "
              f"source numbers {min(src)}..{max(src)} -> "
              f"global {vchs[0]['number']}..{vchs[-1]['number']}")
        dup = sorted({n for n in src if src.count(n) > 1})
        if dup:
            print(f"    source numbers used twice: {dup[:20]}")
        missing = [n for n in range(1, max(src) + 1) if n not in set(src)]
        if missing:
            print(f"    source numbers never used ({len(missing)}): {missing[:20]}")
        jumps = [(vchs[k - 1], vchs[k]) for k in range(1, len(vchs))
                 if vchs[k]["src_number"] != vchs[k - 1]["src_number"] + 1]
        if jumps:
            print(f"    {len(jumps)} out-of-sequence step(s) in the source:")
            for a, b in jumps[:10]:
                print(f"      line {b['line_no']}: {a['raw_header']!r} -> {b['raw_header']!r}")
        print()

    unparsed = [c for c in chapters if c["src_number"] is None]
    if unparsed:
        print(f"  WARNING {len(unparsed)} header(s) with an unparseable number:")
        for c in unparsed[:10]:
            print(f"    line {c['line_no']}: {c['raw_header']!r}")

    empty = [c for c in chapters if not c["content_lines"]]
    if empty:
        print(f"  WARNING {len(empty)} chapter(s) with no body:")
        for c in empty[:10]:
            print(f"    line {c['line_no']}: {c['raw_header']!r}")

    dupes = sorted({n for n in numbers if numbers.count(n) > 1})
    if dupes:
        print(f"  ERROR duplicate target chapter numbers ({len(dupes)}): {dupes[:20]}")

    lens = sorted(len(c["content_lines"]) for c in chapters)
    print(f"  body lines: min {lens[0]}, median {lens[len(lens) // 2]}, max {lens[-1]}")
    print(f"  first: [{chapters[0]['number']}] {chapters[0]['raw_header']}  "
          f"({len(chapters[0]['content_lines'])} lines)")
    print(f"  last:  [{chapters[-1]['number']}] {chapters[-1]['raw_header']}  "
          f"({len(chapters[-1]['content_lines'])} lines)")

    if args.show_map:
        print("\n  full map (global -> volume/source/title):")
        for c in chapters:
            print(f"    {c['number']:>4}  v{c['volume']} 第{c['src_number']}章  {c['title']}")

    return dupes


def main():
    parser = argparse.ArgumentParser(description="Queue a flat-text novel chapter-by-chapter.")
    parser.add_argument("--file", required=True, help="Path to the flat text file")
    parser.add_argument("--book-id", type=int, required=True, help="Target book ID")
    parser.add_argument("--dry-run", action="store_true",
                        help="Parse and report only; do not write to the queue.")
    parser.add_argument("--limit", type=int, default=None,
                        help="Only enqueue the first N chapters (useful for testing).")
    parser.add_argument("--numbering", choices=("sequential", "source"), default="sequential",
                        help="sequential: renumber 1..N in file order (default, required for "
                             "multi-volume raws). source: keep the source's own numbers.")
    parser.add_argument("--number-titles", action="store_true",
                        help="Keep the source 第N章 prefix in the queued title. Off by default "
                             "when renumbering, so titles don't contradict chapter_number.")
    parser.add_argument("--show-map", action="store_true",
                        help="Print the full global->source chapter map.")
    parser.add_argument("--illustrations", default=None, metavar="MANIFEST",
                        help="manifest.json from fetch_remote_illustrations.py; swaps "
                             "<img src=…> lines for ⟦IMG:id⟧ markers and persists the images.")
    args = parser.parse_args()

    config = TranslationConfig()
    logger = Logger(config)
    db = DatabaseManager(config, logger)

    book = db.get_book(book_id=args.book_id)
    if not book:
        print(f"ERROR: Book {args.book_id} not found.", file=sys.stderr)
        sys.exit(1)

    chapters, volumes = parse_chapters(args.file)
    if not chapters:
        print(f"ERROR: No chapters matched in {args.file}.", file=sys.stderr)
        sys.exit(1)

    assign_numbers(chapters, args.numbering)

    collector = None
    if args.illustrations:
        collector, url_to_marker = load_illustrations(args.illustrations)
        n, missing = apply_illustration_markers(chapters, url_to_marker)
        print(f"Illustrations: {len(url_to_marker)} image(s) loaded, "
              f"{n} marker(s) placed"
              + (f", {missing} tag(s) with no image dropped" if missing else ""))

    dupes = report(chapters, volumes, args.file, book, args)
    if dupes:
        print("\nRefusing to enqueue: target chapter numbers are not unique.\n"
              "Use --numbering sequential.", file=sys.stderr)
        sys.exit(1)

    already = existing_queue_numbers(args.book_id) | existing_chapter_numbers(db, args.book_id)
    if already:
        print(f"\n  {len(already)} chapter number(s) already queued/saved for this book; skipping those.")

    to_add = [c for c in chapters if c["number"] not in already]
    if args.limit is not None:
        to_add = to_add[:args.limit]

    print(f"\n{'[DRY RUN] would enqueue' if args.dry_run else 'Enqueuing'} {len(to_add)} chapter(s)...")

    if args.dry_run:
        return

    added = 0
    for ch in to_add:
        # A few headers in watermarked raws are nothing but the watermark
        # (第一百四十三章  粗发! -> 第一百四十三章), leaving no title at all.
        # Fall back to the header so the queue item is never nameless.
        title = ch["raw_header"] if (args.number_titles or not ch["title"]) else ch["title"]
        qid = db.add_to_queue(
            book_id=args.book_id,
            content=ch["content_lines"],
            title=title,
            chapter_number=ch["number"],
            source=args.file,
        )
        if qid:
            added += 1
            if collector is not None:
                try:
                    store_chapter_illustrations(
                        db, config, args.book_id, ch["content_lines"],
                        collector, queue_id=qid,
                    )
                except Exception as e:
                    print(f"  Failed to store illustrations for chapter "
                          f"{ch['number']}: {e}", file=sys.stderr)
        else:
            print(f"  FAILED to queue chapter {ch['number']}", file=sys.stderr)
        if added % 100 == 0 and added:
            print(f"  ...{added} queued")

    print(f"\nDone. Added {added} chapter(s) to the queue for book {args.book_id}.")


if __name__ == "__main__":
    main()
