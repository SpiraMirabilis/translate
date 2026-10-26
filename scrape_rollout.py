#!/usr/bin/env python3
"""Bring books onto automated scraping: check, report, then apply a plan.

    python3 scrape_rollout.py check [--wait]        # queue a TOC read per book
    python3 scrape_rollout.py report [--json plan.json]
    python3 scrape_rollout.py apply plan.json [--yes]

`check` queues a read-only TOC check for every candidate (books on a site
the scraper's /supported_sites lists, not completed/dropped, not --skip'ed); t9-scraper.service runs
them one at a time. A book whose recent source titles match its TOC links
itself on that read — nothing else changes, every mode stays as it was.

`report` prints each book's TOC, our heads, the link (or the suggested one)
and a suggested mode:
    auto   linked/suggested by title, translated in the last --recent-days, and
           newest-at-source minus newest-translated within the gap
    queue  linkable, but not being translated right now (or the gap is big)
    review no confident link — open the book's Source modal
With --json it writes those suggestions as a plan you edit, then `apply`
(a dry run without --yes) links what the plan says and sets the modes.
"""
import argparse
import datetime
import json
import sys
import time



def build_db():
    from config import TranslationConfig
    from database import DatabaseManager
    from logger import Logger
    config = TranslationConfig()
    return DatabaseManager(config, Logger(config))


def candidates(db, sites, skip, include_finished):
    import scrape_sync
    out = []
    for b in db.list_book_scrapes():
        if b["book_id"] in skip or not b.get("source_url"):
            continue
        if not scrape_sync.site_supported(b["source_url"], sites):
            continue
        if not include_finished and (b.get("book_status") or "") in ("completed", "dropped"):
            continue
        out.append(b)
    return out


def last_translated_at(db, book_id):
    with db._conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(translation_date) FROM chapters WHERE book_id = ?", (book_id,))
        row = cursor.fetchone()
    return row[0] if row else None


def cmd_check(db, books, args):
    import scrape_sync
    queued = []
    for b in books:
        if db.has_open_scrape_task(b["book_id"], kinds=("toc",)):
            print(f"  book {b['book_id']:<4} TOC check already queued")
            continue
        if b.get("toc_count") and not args.refresh:
            print(f"  book {b['book_id']:<4} has a TOC from {b.get('last_checked_at')} (--refresh to re-read)")
            continue
        queued.append(scrape_sync.enqueue_check(db, b["book_id"]))
    print(f"queued {len(queued)} TOC check(s)")
    if not args.wait or not queued:
        return 0
    print("waiting for the worker (Ctrl-C stops waiting, not the checks)…")
    try:
        while True:
            tasks = [db.get_scrape_task(t) for t in queued]
            open_ = [t for t in tasks if t and t["status"] in ("queued", "running", "blocked")]
            blocked = [t for t in open_ if t["status"] == "blocked"]
            done = len(tasks) - len(open_)
            msg = f"  {done}/{len(tasks)} done"
            if blocked:
                msg += f" — BLOCKED on a challenge: {blocked[0].get('vnc_url') or 'see /scraping'}"
            print(msg, flush=True)
            if not open_:
                break
            time.sleep(15)
    except KeyboardInterrupt:
        print()
    failed = [t for t in (db.get_scrape_task(i) for i in queued) if t and t["status"] == "failed"]
    for t in failed:
        print(f"  book {t['book_id']} failed: {t.get('last_error')}")
    return 0


def assess(db, b, args):
    import scrape_sync
    state = db.get_book_scrape(b["book_id"], include_toc=True)
    toc = (state.get("last_toc") or {}).get("chapters") or []
    have, translated = b.get("have_max"), b.get("translated_max") or 0
    row = {"book_id": b["book_id"], "title": b["title"], "mode": b["mode"],
           "toc": len(toc) if toc else None, "have": have, "translated": translated,
           "linked": bool(state.get("linked")), "link": None, "new": None,
           "suggest": "review", "why": ""}
    if not toc:
        row["why"] = state.get("last_error") or "no TOC read yet"
        return row
    prop = scrape_sync.propose_link(toc, have, state.get("number_offset") or 0,
                                    our_titles=db.recent_source_titles(b["book_id"]))
    cur = scrape_sync.cursor_index(state, toc)
    if row["linked"] and cur is not None:
        idx, number = cur, state.get("cursor_number")
        row["link"] = {"index": idx, "chapter_number": number, "method": "linked"}
        if prop and prop["index"] >= 0 and (prop["index"], prop["chapter_number"]) != (idx, number) \
                and prop["method"] == "title":
            row["why"] = (f"linked at #{idx + 1}=ch{number} but titles say "
                          f"#{prop['index'] + 1}=ch{prop['chapter_number']}")
            row["link"] = dict(prop, relink=True)
            idx, number = prop["index"], prop["chapter_number"]
    elif prop and (prop["confident"] or prop["method"] == "number"):
        idx, number = prop["index"], prop["chapter_number"]
        row["link"] = prop
    else:
        row["why"] = "no confident link" + (f" (best guess: {prop['method']} #{prop['index'] + 1})" if prop else "")
        return row

    new = len(toc) - idx - 1 if idx is not None else len(toc)
    row["new"] = new
    head = (number or 0) + new
    gap = head - translated
    gap_limit = int(state.get("auto_gap") if state.get("auto_gap") is not None else args.gap)
    last = last_translated_at(db, b["book_id"])
    recent = False
    if last:
        try:
            recent = (datetime.datetime.now() - datetime.datetime.fromisoformat(str(last)[:19])
                      ).days <= args.recent_days
        except ValueError:
            pass
    strong = row["link"].get("method") in ("linked", "title", "empty")
    if strong and recent and gap <= gap_limit:
        row["suggest"] = "auto"
        row["why"] = row["why"] or f"translated {str(last)[:10]}, gap {gap}"
    else:
        row["suggest"] = "queue" if strong or row["link"].get("method") == "number" else "review"
        reasons = []
        if not recent:
            reasons.append(f"last translated {str(last)[:10] if last else 'never'}")
        if gap > gap_limit:
            reasons.append(f"gap {gap} > {gap_limit}")
        if not strong:
            reasons.append(f"link by {row['link'].get('method')} — verify")
        row["why"] = row["why"] or ", ".join(reasons)
    return row


def cmd_report(db, books, args):
    rows = [assess(db, b, args) for b in books]
    print(f"{'book':>4}  {'mode':<5} {'suggest':<7} {'toc':>5} {'have':>5} {'tl':>5} {'new':>4}  link / why")
    for r in rows:
        link = r["link"]
        where = (f"#{link['index'] + 1}=ch{link['chapter_number']} ({link.get('method')})"
                 if link and link.get("index", -1) >= 0 else ("before #1" if link else "-"))
        print(f"{r['book_id']:>4}  {r['mode']:<5} {r['suggest']:<7} {r['toc'] or '-':>5} "
              f"{r['have'] if r['have'] is not None else '-':>5} {r['translated']:>5} "
              f"{r['new'] if r['new'] is not None else '-':>4}  {where}  {r['why']}"
              f"  [{r['title'][:30]}]")
    counts = {k: sum(1 for r in rows if r["suggest"] == k) for k in ("auto", "queue", "review")}
    print(f"\n{len(rows)} books: {counts['auto']} auto, {counts['queue']} queue, {counts['review']} review")
    if args.json:
        plan = {str(r["book_id"]): {
            "title": r["title"],
            "mode": r["suggest"] if r["suggest"] != "review" else "off",
            "link": ({"index": r["link"]["index"], "chapter_number": r["link"]["chapter_number"]}
                     if r["link"] and (not r["linked"] or r["link"].get("relink")) else None),
            "why": r["why"],
        } for r in rows}
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False, indent=2)
        print(f"wrote plan to {args.json} — edit modes/links, then: scrape_rollout.py apply {args.json} --yes")
    return 0


def cmd_apply(db, args):
    import scrape_sync
    with open(args.plan, encoding="utf-8") as f:
        plan = json.load(f)
    for key, entry in plan.items():
        book_id = int(key)
        mode = entry.get("mode", "off")
        if mode not in scrape_sync.MODES:
            print(f"  book {book_id}: bad mode {mode!r}, skipped")
            continue
        link = entry.get("link")
        acts = []
        if link:
            acts.append(f"link #{link['index'] + 1}=ch{link.get('chapter_number')}" if link["index"] >= 0
                        else "link before #1")
        state = db.get_book_scrape(book_id)
        if mode != state.get("mode"):
            acts.append(f"mode {state.get('mode')} -> {mode}")
        if not acts:
            continue
        print(f"  book {book_id:<4} {'; '.join(acts)}  [{entry.get('title', '')[:30]}]")
        if not args.yes:
            continue
        if link:
            scrape_sync.link_book(db, book_id, link["index"], link.get("chapter_number"))
        if mode != state.get("mode"):
            db.update_book_scrape(book_id, mode=mode, consecutive_failures=0)
    if not args.yes:
        print("dry run — add --yes to apply")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sites", default=None,
                    help="comma-separated source sites (default: the scraper's /supported_sites)")
    ap.add_argument("--skip", default="", help="comma-separated book ids to leave out")
    ap.add_argument("--book", type=int, action="append", help="only these books (repeatable)")
    ap.add_argument("--include-finished", action="store_true", help="include completed/dropped books")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="queue a read-only TOC check per book")
    c.add_argument("--wait", action="store_true", help="wait for the worker to finish them")
    c.add_argument("--refresh", action="store_true", help="re-read books that already have a TOC")
    r = sub.add_parser("report", help="suggest links and modes")
    r.add_argument("--json", help="write the suggestions as an editable plan")
    r.add_argument("--recent-days", type=int, default=14, help="'being translated' window (default 14)")
    r.add_argument("--gap", type=int, default=None, help="auto-translate gap (default: the setting)")
    a = sub.add_parser("apply", help="apply a plan (dry run without --yes)")
    a.add_argument("plan")
    a.add_argument("--yes", action="store_true")
    args = ap.parse_args(argv)

    import scrape_sync
    db = build_db()
    if args.cmd == "apply":
        return cmd_apply(db, args)
    if getattr(args, "gap", 0) is None:
        args.gap = int(scrape_sync.setting("scrape_default_gap"))
    skip = {int(x) for x in args.skip.split(",") if x.strip()}
    if args.sites:
        sites = [s.strip().lower() for s in args.sites.split(",") if s.strip()]
    else:
        try:
            sites = scrape_sync.fetch_supported_sites(scrape_sync.make_client(timeout=10))
        except Exception as e:
            known = scrape_sync.load_supported_sites()
            if not known:
                print(f"could not read the scraper's supported sites ({e}); pass --sites", file=sys.stderr)
                return 1
            sites = known["sites"]
            print(f"scraper unreachable ({e}); using the list cached {known.get('fetched_at')}")
    books = candidates(db, sites, skip, args.include_finished)
    if args.book:
        books = [b for b in books if b["book_id"] in set(args.book)]
    print(f"{len(books)} candidate book(s) on {', '.join(sites)}")
    return cmd_check(db, books, args) if args.cmd == "check" else cmd_report(db, books, args)


if __name__ == "__main__":
    sys.exit(main())
