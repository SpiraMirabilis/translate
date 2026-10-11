#!/usr/bin/env python3
"""Write one JSON dossier per public book for tag classification.

Each dossier holds the book's metadata, its current tags, and ~1,500-word excerpts
of the translated text from the first two published chapters and one mid-book
chapter — enough for a reader (or a model) to judge genre, premise, setting,
protagonist and romance axis. Read-only.

    python3 build_tag_dossiers.py --out /tmp/dossiers [--book 90 ...] [--words 1500]
"""
import argparse
import json
import os
import sys

from config import TranslationConfig
from logger import Logger
from database import DatabaseManager

DISCUSSION_CH = 0  # the book-discussion sentinel is not prose


def excerpt(lines, words):
    out, n = [], 0
    for line in lines or []:
        if not isinstance(line, str) or not line.strip():
            continue
        w = line.split()
        out.append(line.strip())
        n += len(w)
        if n >= words:
            break
    return "\n".join(out)


def sample_chapters(db, book_id):
    chapters = [c["chapter"] for c in db.list_chapters(book_id, published_only=True)
                if c["chapter"] > DISCUSSION_CH]
    if not chapters:
        return []
    chapters.sort()
    picks = chapters[:2]
    mid = chapters[len(chapters) // 2]
    if mid not in picks:
        picks.append(mid)
    return picks


def build(db, book, words):
    excerpts = []
    for n in sample_chapters(db, book["id"]):
        ch = db.get_chapter(book_id=book["id"], chapter_number=n, published_only=True)
        if not ch:
            continue
        excerpts.append({"chapter": n, "title": ch.get("title"),
                         "text": excerpt(ch.get("content"), words)})
    return {
        "book_id": book["id"],
        "title": book["title"],
        "author": book.get("author"),
        "source_language": book.get("source_language"),
        "status": book.get("status"),
        "published_chapters": book.get("published_chapter_count"),
        "description": book.get("description"),
        "current_tags": book.get("tags") or [],
        "excerpts": excerpts,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="directory for <book_id>.json files")
    ap.add_argument("--book", type=int, nargs="*", help="only these book ids (default: every public book)")
    ap.add_argument("--words", type=int, default=1500)
    args = ap.parse_args()

    config = TranslationConfig()
    db = DatabaseManager(config, Logger(config))
    os.makedirs(args.out, exist_ok=True)
    books = [b for b in db.list_books() if b.get("is_public")]
    if args.book:
        want = set(args.book)
        books = [b for b in books if b["id"] in want]
    for b in books:
        d = build(db, b, args.words)
        path = os.path.join(args.out, f"{b['id']}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        print(f"{b['id']:>4} {len(d['excerpts'])} excerpts  {b['title'][:60]}", file=sys.stderr)
    print(f"{len(books)} dossiers → {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
