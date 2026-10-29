#!/usr/bin/env python3
"""The scraper lane worker — runs as t9-scraper.service.

Keeps at most one job on the scraper (see scrape_sync.py for the rules), runs
the TOC sweeps at scrape_sweep_hours, and starts translations for books in
auto mode. It also ticks the NovelUpdates lane (nu_sync.py), which keeps its
own single job on the scraper and has its own kill switch (nu_enabled). A standalone process rather than a thread in the admin app, so the
frequent admin restarts never interrupt a backfill; the scraper's job id is
persisted on submit, so its own restarts resume polling instead of resubmitting.

Usage:
    python3 scrape_worker.py            # run forever (the service)
    python3 scrape_worker.py --once     # one tick, then exit
    python3 scrape_worker.py --status   # print the lane and exit
"""
import argparse
import json
import logging
import os
import signal
import sys
import time

HEARTBEAT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "scrape_worker.json")
# Poll the scraper this often while a job is in flight, and the task table
# this often while idle (the admin UI's "Check now" lands within IDLE_SLEEP).
BUSY_SLEEP = 10
IDLE_SLEEP = 10

_stop = False


def _on_signal(signum, frame):
    global _stop
    _stop = True


def write_heartbeat(state):
    try:
        os.makedirs(os.path.dirname(HEARTBEAT), exist_ok=True)
        tmp = HEARTBEAT + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, HEARTBEAT)
    except OSError:
        pass


def build_db():
    from config import TranslationConfig
    from database import DatabaseManager
    from logger import Logger
    config = TranslationConfig()
    return DatabaseManager(config, Logger(config))


def print_status(db):
    lane = db.lane_task()
    if lane:
        print(f"lane: task {lane['id']} {lane['kind']} book {lane['book_id']} ({lane['status']})")
    else:
        print("lane: idle")
    for t in db.list_scrape_tasks(statuses=("queued",), order="t.priority, t.id", limit=30):
        print(f"  queued  #{t['id']:<6} p{t['priority']:<3} {t['kind']:<9} book {t['book_id']:<4} "
              f"{t.get('reason') or ''} {('after ' + t['not_before']) if t.get('not_before') else ''}")
    for b in db.list_book_scrapes():
        if b["mode"] == "off":
            continue
        print(f"  book {b['book_id']:<4} {b['mode']:<5} toc={b.get('toc_count')} "
              f"have={b.get('have_max')} translated={b.get('translated_max')} "
              f"checked={b.get('last_checked_at')} "
              + (f"HOLD: {b['hold_reason']}" if b.get("hold_reason") else ""))
    nu = db.nu_lane_task()
    print(f"nu lane: task {nu['id']} {nu['kind']} book {nu['book_id']} ({nu['status']})" if nu
          else "nu lane: idle")
    for t in db.list_nu_tasks(statuses=("queued",), order="t.priority, t.id", limit=30):
        print(f"  queued  #{t['id']:<6} p{t['priority']:<3} {t['kind']:<8} book {t['book_id']} "
              f"{('after ' + t['not_before']) if t.get('not_before') else ''}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true", help="run one tick and exit")
    ap.add_argument("--status", action="store_true", help="print the lane and exit")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        stream=sys.stdout)
    log = logging.getLogger("scrape_worker")

    db = build_db()
    if args.status:
        print_status(db)
        return 0

    import nu_sync
    import scrape_sync
    engine = scrape_sync.ScrapeEngine(db, logger=log)
    nu_engine = nu_sync.NuEngine(db, logger=log)

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)
    log.info("scrape worker started")
    while not _stop:
        try:
            state = engine.tick()
        except Exception:
            log.exception("tick failed")
            state = "error"
        try:
            nu_state = nu_engine.tick()
        except Exception:
            log.exception("NovelUpdates tick failed")
            nu_state = "error"
        lane = nu_lane = None
        try:
            lane = db.lane_task()
            nu_lane = db.nu_lane_task()
        except Exception:
            pass
        write_heartbeat({"at": time.time(), "pid": os.getpid(), "state": state,
                         "lane_task_id": lane["id"] if lane else None,
                         "nu_state": nu_state, "nu_task_id": nu_lane["id"] if nu_lane else None})
        if args.once:
            break
        # A finished job means the next can be submitted right away.
        delay = BUSY_SLEEP if (state == "busy" and lane) or (nu_state == "busy" and nu_lane) else IDLE_SLEEP
        for _ in range(delay):
            if _stop:
                break
            time.sleep(1)
    log.info("scrape worker stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
