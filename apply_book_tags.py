#!/usr/bin/env python3
"""Apply reviewed tag proposals to books, folding aliases through tag_vocab.json.

    python3 apply_book_tags.py --file tag_proposals.json [--book 90 ...] [--apply]

Proposals: {"<book_id>": {"tags": [...], ...}} (extra keys ignored). Dry run by
default: prints current → final per book. Canonical tags come from the vocab's
axes; aliases fold; `drop` tags vanish; anything else is kept only when it
looks like a fandom name on a fan-fiction book, otherwise reported as unknown.
Writes go through DatabaseManager.set_book_tags — the only tag write path.
"""
import argparse
import json
import sys

from config import TranslationConfig
from logger import Logger
from database import DatabaseManager

VOCAB_PATH = "tag_vocab.json"


def load_vocab(path=VOCAB_PATH):
    with open(path, encoding="utf-8") as f:
        v = json.load(f)
    canonical = [t for axis in v["axes"].values() for t in axis]
    return {
        "canonical": canonical,
        "order": {t: i for i, t in enumerate(canonical)},
        "aliases": {k.lower(): val.lower() for k, val in v.get("aliases", {}).items()},
        "drop": {t.lower() for t in v.get("drop", [])},
    }


def fold(tags, vocab, allow_free=False):
    """Normalise a tag list: lowercase, alias-fold, drop, dedupe, canonical order first.

    Returns (final_tags, unknown_tags). Unknown tags are kept when allow_free is
    set (fandom names on fan fiction), else left out.
    """
    seen, final, unknown = set(), [], []
    for t in tags or []:
        if not isinstance(t, str):
            continue
        s = t.strip().lower()
        s = vocab["aliases"].get(s, s)
        if not s or s in vocab["drop"] or s in seen:
            continue
        seen.add(s)
        if s in vocab["order"]:
            final.append(s)
        else:
            unknown.append(s)
            if allow_free:
                final.append(s)
    final.sort(key=lambda t: (vocab["order"].get(t, 10_000), t))
    return final, unknown


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", required=True)
    ap.add_argument("--book", type=int, nargs="*")
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    ap.add_argument("--vocab", default=VOCAB_PATH)
    args = ap.parse_args()

    vocab = load_vocab(args.vocab)
    with open(args.file, encoding="utf-8") as f:
        proposals = json.load(f)
    config = TranslationConfig()
    db = DatabaseManager(config, Logger(config))
    books = {b["id"]: b for b in db.list_books()}

    changed = 0
    for key, prop in sorted(proposals.items(), key=lambda kv: int(kv[0])):
        book_id = int(key)
        if args.book and book_id not in args.book:
            continue
        book = books.get(book_id)
        if not book:
            print(f"{book_id}: no such book", file=sys.stderr)
            continue
        proposed = list(prop["tags"] if isinstance(prop, dict) else prop)
        # Curation is never the model's call: an existing editor's choice survives.
        if any(vocab["aliases"].get(t.lower(), t.lower()) == "editor's choice"
               for t in (book.get("tags") or [])):
            proposed.append("editor's choice")
        fanfic = "fan fiction" in {vocab["aliases"].get(t.lower(), t.lower()) for t in proposed}
        final, unknown = fold(proposed, vocab, allow_free=fanfic)
        current = sorted(t.lower() for t in (book.get("tags") or []))
        if sorted(final) == current:
            continue
        changed += 1
        print(f"\n[{book_id}] {book['title'][:70]}")
        print(f"   now : {', '.join(current) or '—'}")
        print(f"   new : {', '.join(final)}")
        if unknown and not fanfic:
            print(f"   skip: {', '.join(unknown)}  (not in vocab)")
        if args.apply:
            if not db.set_book_tags(book_id, final):
                print("   !! write failed", file=sys.stderr)
    print(f"\n{changed} book(s) {'updated' if args.apply else 'would change'}"
          + ("" if args.apply else "  — re-run with --apply"))


if __name__ == "__main__":
    main()
