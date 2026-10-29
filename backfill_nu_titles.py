#!/usr/bin/env python3
"""Fill books.nu_title (the title NovelUpdates lists a novel under) for the
existing library, from NU's own series pages.

Each book is looked up with the scraper's NU search (`POST /nu/search`) by its
original title in both scripts (`nu_sync.search_names`) — the same search the
NU lane runs. One job at a time, and never alongside a job of the NU lane
(the script waits for the lane to go idle first).

- `found`: NU's series title is proposed, with the series slug/URL.
- near misses only: no title is proposed; the candidates are listed for a
  human to pick (copy one's title into "title").
- nothing at all: the novel isn't on NU (yet); nothing to fill.
- no original title: can't be searched — set source_title first
  (backfill_source_titles.py).

Dry run by default: writes a proposals JSON and prints a review table.
Applying is a separate step, from the file, after review:

    python3 backfill_nu_titles.py                     # all books missing one
    python3 backfill_nu_titles.py -b 15 117           # just these
    python3 backfill_nu_titles.py --apply /tmp/t9_nu_titles/proposals.json

--apply also records the series (slug, URL, in our group or not) for books
the NU lane hasn't looked up yet, exactly as its own search would — mode
stays off, so nothing is posted. --no-link writes the titles only.

Each run starts with one group sync, so "in our group" is current (--no-sync
to skip). Search results are cached per book in --cache-dir, so a rerun
doesn't search again (--refetch to ignore the cache).
"""
import argparse
import json
import os
import sys
import time

DEFAULT_DIR = "/tmp/t9_nu_titles"


def build_db():
    from config import TranslationConfig
    from database import DatabaseManager
    from logger import Logger
    config = TranslationConfig()
    return DatabaseManager(config, Logger(config))


def wait_for_idle_lane(db, poll=10):
    """Never put a second NU job on the scraper: wait out the lane's own."""
    while db.nu_lane_task():
        time.sleep(poll)


def search(db, book, cache_dir, refetch=False):
    import nu_sync
    path = os.path.join(cache_dir, "search", f"{book['id']}.json")
    if not refetch and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    names = nu_sync.search_names(book)
    wait_for_idle_lane(db)
    res = nu_sync.make_client().nu_search(names, poll=3, timeout=900)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False)
    return res


def resolve(db, book, args):
    import nu_sync
    row = {"book_id": book["id"], "english_title": book["title"],
           "current": book.get("nu_title"), "names": nu_sync.search_names(book),
           "title": None, "slug": None, "url": None, "series_id": None,
           "in_our_group": None, "outcome": None, "candidates": []}
    if not row["names"]:
        row["outcome"] = "no_original_title"
        return row
    try:
        res = search(db, book, args.cache_dir, refetch=args.refetch)
    except Exception as e:
        row["outcome"] = f"error: {e}"
        return row
    series = res.get("series") or {}
    if res.get("found") and series.get("title"):
        row.update(title=series["title"], slug=series.get("slug"), url=series.get("url"),
                   series_id=series.get("id"), in_our_group=bool(res.get("inOurGroup")),
                   outcome="found")
    elif res.get("candidates"):
        row["outcome"] = "candidates"
        row["candidates"] = [{"title": c.get("title"), "slug": c.get("slug"), "url": c.get("url"),
                              "associatedNames": (c.get("associatedNames") or [])[:6]}
                             for c in res["candidates"]]
    else:
        row["outcome"] = "not_on_nu"
    return row


def print_table(rows):
    for r in rows:
        mark = " " if r["outcome"] == "found" else "!"
        t = r["title"] or "—"
        ours = " (ours)" if r.get("in_our_group") else ""
        print(f"{mark} {r['book_id']:>4}  {t:<44} [{r['outcome']}{ours}]  {r['english_title']}")
        for c in r.get("candidates") or []:
            print(f"        ? {c['title']}  {c['url']}")


def cmd_apply(db, path, link=True):
    import nu_sync
    with open(path, encoding="utf-8") as f:
        rows = json.load(f)["proposals"]
    titles = linked = 0
    for r in rows:
        if not r.get("title"):
            continue
        db.set_book_nu_title(r["book_id"], r["title"])
        titles += 1
        if not link or not r.get("slug"):
            continue
        state = db.get_book_nu(r["book_id"])
        if state["state"] not in ("untracked", "searching"):
            continue  # the lane already knows this book; don't second-guess it
        db.update_book_nu(r["book_id"], nu_slug=r["slug"], nu_url=r.get("url"),
                          nu_series_id=r.get("series_id"), candidates=None,
                          checked_at=nu_sync._iso(nu_sync.datetime.datetime.now()),
                          state="active" if r.get("in_our_group") else "linked",
                          search_names=r.get("names"))
        linked += 1
    print(f"Wrote nu_title on {titles} book(s)" + (f"; recorded the series for {linked}." if link else "."))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-b", "--books", type=int, nargs="+", help="only these book ids")
    ap.add_argument("--all", action="store_true", help="include books that already have an nu_title")
    ap.add_argument("--refetch", action="store_true", help="ignore cached search results")
    ap.add_argument("--no-sync", action="store_true",
                    help="skip the group sync that refreshes 'in our group' before searching")
    ap.add_argument("--cache-dir", default=DEFAULT_DIR)
    ap.add_argument("--out", help=f"proposals file (default {DEFAULT_DIR}/proposals.json)")
    ap.add_argument("--apply", metavar="FILE", help="write the titles from a reviewed proposals file")
    ap.add_argument("--no-link", action="store_true", help="with --apply: titles only, no series records")
    args = ap.parse_args(argv)

    db = build_db()
    if args.apply:
        cmd_apply(db, args.apply, link=not args.no_link)
        return 0

    import nu_sync
    if nu_sync.load_hold():
        print(f"The NU lane is held ({nu_sync.load_hold().get('reason')}); clear that first.", file=sys.stderr)
        return 1

    books = [db.get_book(book_id=b["id"]) for b in db.list_books()
             if not b.get("is_original")
             and (args.all or not b.get("nu_title"))
             and (not args.books or b["id"] in args.books)]
    books.sort(key=lambda b: b["id"])
    print(f"{len(books)} book(s) to look up", file=sys.stderr)
    if books and not args.no_sync:
        # "In our group" comes from the scraper's stored group list; a stale
        # one would record our own series as merely linked.
        print("syncing our NU group page first…", file=sys.stderr, flush=True)
        wait_for_idle_lane(db)
        nu_sync.make_client().nu_sync_group(poll=3, timeout=900)

    rows = []
    for i, book in enumerate(books, 1):
        print(f"[{i}/{len(books)}] book {book['id']}…", file=sys.stderr, flush=True)
        rows.append(resolve(db, book, args))

    out = args.out or os.path.join(args.cache_dir, "proposals.json")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"generated": time.strftime("%Y-%m-%d %H:%M:%S"), "proposals": rows},
                  f, ensure_ascii=False, indent=1)
    print_table(rows)
    counts = {}
    for r in rows:
        key = r["outcome"] if not r["outcome"].startswith("error") else "error"
        counts[key] = counts.get(key, 0) + 1
    print("\n" + ", ".join(f"{v} {k}" for k, v in sorted(counts.items()))
          + f". Review {out}, then --apply it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
