#!/usr/bin/env python3
"""Restart t9.service without killing a chapter mid-translation.

Idle server → restart at once. Busy server:

  1. record every running book: its chapter in flight, the chapters left
     (auto-process cap, else the book's queue), and the run's options;
  2. stop auto-process for those books (stop_auto_process.stop_auto) —
     the chapter in flight still finishes;
  3. poll /api/translate/status until no job is left;
  4. systemctl --user restart t9.service, then wait for /api/health;
  5. restart each book's auto-process run with its recorded options
     (start_auto_process.start_auto), with `priority` so it takes back its
     slot ahead of starts that were already waiting.

Step 1 is saved to logs/restart_t9_state.json before anything is stopped, so a
run interrupted while draining (Ctrl-C, a lost SSH session) can be finished
with `--restore`.

A job parked on a GUI prompt (entity review, JSON fix, chapter conflict) cannot
finish by itself: the script says so and keeps waiting until someone answers it
in the admin UI, or until --timeout. On a timeout nothing is restarted; the
stopped books are listed with the command that resumes them.

Single-chapter runs (not auto-processing) are waited for but not restarted —
there is nothing left to resume. Neither is a capped run on its last chapter.

Usage:
    python3 restart_t9.py                 # drain if needed, restart, resume
    python3 restart_t9.py --dry-run       # report what it would do
    python3 restart_t9.py --public        # also restart t9-public.service
    python3 restart_t9.py --timeout 3600  # give up draining after an hour
    python3 restart_t9.py --restore       # resume the runs a previous call saved

Exit codes: 0 done, 1 gave up (timeout / a resume failed), 2 admin unreachable.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv

import t9_client
from start_auto_process import RUN_OPTION_KEYS, describe_item, start_auto
from stop_auto_process import NotAuthenticated, stop_auto
from translation_status import IDLE_STATUSES, book_progress, fetch_status

STATE_FILE = Path(__file__).resolve().parent / "logs" / "restart_t9_state.json"


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def live_jobs(status: dict) -> dict:
    """{book_id: job} for every job still in flight (same rule as mcp_server.guard)."""
    jobs = {}
    for bid, job in (status.get("jobs") or {}).items():
        if job.get("is_running") or job.get("status") not in IDLE_STATUSES:
            jobs[int(bid)] = job
    return jobs


def get_status(url: str, password: str | None) -> dict:
    status = fetch_status(url, password)
    if status is None:  # 401, already reported by fetch_status
        raise NotAuthenticated("Not authenticated — set T9_PASSWORD (in .env).")
    return status


# ---------------------------------------------------------------- step 1

def snapshot(jobs: dict) -> list[dict]:
    """What each running book needs to be resumed the same way."""
    runs = []
    for book_id, job in sorted(jobs.items()):
        opts = job.get("run_options") or {}
        auto = bool(job.get("auto_process"))
        _, _, queued = book_progress(book_id)
        # The in-flight chapter is still a queue row; it will be done by resume.
        queued_after = max(queued - 1, 0)
        cap_left = job.get("auto_remaining")  # None = unlimited (or not auto)
        if not auto:
            resume = False
        elif opts.get("max_chapters"):
            resume = bool(cap_left)
        else:
            resume = queued_after > 0
        runs.append({
            "book_id": book_id,
            "chapter_in_flight": job.get("chapter_number"),
            "status": job.get("status"),
            "auto_process": auto,
            "remaining_cap": cap_left,
            "queued_after": queued_after,
            "run_options": opts,
            "resume": resume,
        })
    return runs


def describe(run: dict) -> str:
    if not run["auto_process"]:
        left = "single-chapter run, nothing to resume"
    elif run["remaining_cap"] is not None:
        left = (f"{run['remaining_cap']} more in this run (capped), "
                f"{run['queued_after']} queued")
    else:
        left = f"{run['queued_after']} more queued"
    opts = ", ".join(f"{k}={v}" for k, v in run["run_options"].items()
                     if k in RUN_OPTION_KEYS and v not in (None, False))
    return (f"book {run['book_id']}: ch{run['chapter_in_flight']} in flight "
            f"({run['status']}), {left}; options: {opts or 'defaults'}")


def resume_payload(run: dict) -> dict:
    opts = run["run_options"]
    payload = {k: opts.get(k) for k in RUN_OPTION_KEYS}
    # A capped run resumes with what was left of its cap, not the whole cap.
    payload["max_chapters"] = run["remaining_cap"] if opts.get("max_chapters") else None
    payload.update(book_id=run["book_id"], auto_process=True, priority=True)
    return payload


def resume_command(run: dict) -> str:
    p = resume_payload(run)
    parts = [f"python3 start_auto_process.py -b {run['book_id']}"]
    for key, flag in (("translation_model", "--model"), ("advice_model", "--advice-model"),
                      ("cleaning_model", "--cleaning-model"),
                      ("max_chapters", "--max-chapters")):
        if p.get(key):
            parts.append(f"{flag} {p[key]}")
    for key in ("no_review", "two_pass", "no_clean", "no_stream", "save_as_draft"):
        if p.get(key):
            parts.append("--" + key.replace("_", "-"))
    return " ".join(parts)


# ---------------------------------------------------------------- steps 3-5

def save_state(runs: list[dict]) -> None:
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(
        {"saved_at": datetime.now().isoformat(timespec="seconds"), "runs": runs},
        indent=2))


def drain(url, password, runs, poll, timeout) -> bool:
    """Wait until no job is running. False on timeout.

    A book that starts auto-processing meanwhile (the scrape worker's
    auto-starts, someone in the UI) is snapshotted, stopped, and added to
    `runs`, so it is resumed after the restart like the others.
    """
    books = {r["book_id"] for r in runs}
    start = time.monotonic()
    last_line = None
    parked_warned = set()
    while True:
        jobs = live_jobs(get_status(url, password))
        if not jobs:
            return True
        line = ", ".join(f"book {b} ch{j.get('chapter_number')} {j.get('status')}"
                         for b, j in sorted(jobs.items()))
        if line != last_line:
            log(f"waiting: {line}")
            last_line = line
        for b, j in jobs.items():
            st = j.get("status") or ""
            if st.startswith("awaiting_") and b not in parked_warned:
                log(f"⚠ book {b} is parked on '{st}' — answer it in the admin UI; "
                    "the chapter cannot finish until someone does.")
                parked_warned.add(b)
            if b not in books and j.get("auto_process"):
                # Started after the snapshot (e.g. the scrape worker's auto-start).
                run = snapshot({b: j})[0]
                runs.append(run)
                save_state(runs)
                books.add(b)
                log(f"started meanwhile — {describe(run)}; stopping it too")
                with t9_client.client(url, password, timeout=15) as c:
                    stop_auto(c, b)
        if timeout and time.monotonic() - start > timeout:
            return False
        time.sleep(poll)


def restart_services(public: bool) -> None:
    units = ["t9.service"] + (["t9-public.service"] if public else [])
    log(f"restarting {' '.join(units)}")
    subprocess.run(["systemctl", "--user", "restart", *units], check=True)


def wait_healthy(url: str, timeout: float = 180) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{url}/api/health", timeout=5).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(2)
    return False


def restore(url, password, runs) -> bool:
    ok = True
    to_resume = [r for r in runs if r["resume"]]
    if not to_resume:
        log("nothing to resume")
        return True
    with t9_client.client(url, password, timeout=30) as c:
        for run in to_resume:
            r = start_auto(c, resume_payload(run))
            bid = run["book_id"]
            if r.status_code == 200:
                log(f"book {bid}: auto-process resumed at {describe_item(r.json().get('item'))}")
            elif r.status_code == 202:
                log(f"book {bid}: waiting for a slot (position {r.json().get('position', '?')})")
            elif r.status_code == 404:
                log(f"book {bid}: queue is empty now — nothing to resume")
            else:
                ok = False
                detail = r.text
                try:
                    detail = r.json().get("detail", detail)
                except ValueError:
                    pass
                log(f"book {bid}: resume FAILED ({r.status_code}: {detail}) — "
                    f"run: {resume_command(run)}")
    return ok


# ---------------------------------------------------------------- main

def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=t9_client.DEFAULT_URL)
    parser.add_argument("--password", default=os.getenv("T9_PASSWORD"))
    parser.add_argument("--public", action="store_true",
                        help="Also restart t9-public.service (runs no translations).")
    parser.add_argument("--poll", type=float, default=10,
                        help="Seconds between status checks while draining (default 10).")
    parser.add_argument("--timeout", type=float, default=0,
                        help="Give up draining after N seconds without restarting "
                             "(default 0 = wait as long as it takes).")
    parser.add_argument("-n", "--dry-run", action="store_true",
                        help="Report what would happen; stop and restart nothing.")
    parser.add_argument("--restore", action="store_true",
                        help=f"Only resume the runs saved in {STATE_FILE.name}.")
    args = parser.parse_args()

    try:
        if args.restore:
            if not STATE_FILE.exists():
                print(f"No saved state at {STATE_FILE}", file=sys.stderr)
                return 1
            runs = json.loads(STATE_FILE.read_text())["runs"]
            for run in runs:
                log(describe(run))
            if args.dry_run:
                return 0
            ok = restore(args.url, args.password, runs)
            if ok:
                STATE_FILE.unlink(missing_ok=True)
            return 0 if ok else 1

        jobs = live_jobs(get_status(args.url, args.password))
        if not jobs:
            log("idle — nothing translating")
            if args.dry_run:
                log("dry run: would restart now")
                return 0
            restart_services(args.public)
            if not wait_healthy(args.url):
                log("⚠ t9.service did not report healthy within 180s")
                return 1
            log("t9.service is up")
            return 0

        # 1. who is translating, and what's left
        runs = snapshot(jobs)
        log(f"{len(runs)} book(s) translating:")
        for run in runs:
            log("  " + describe(run))
        if args.dry_run:
            for run in runs:
                if run["resume"]:
                    log(f"  would resume with: {resume_command(run)}")
            return 0
        save_state(runs)

        # 2. stop auto-process (in-flight chapters still finish)
        with t9_client.client(args.url, args.password, timeout=15) as c:
            for run in runs:
                if run["auto_process"]:
                    stop_auto(c, run["book_id"])
                    log(f"book {run['book_id']}: auto-process will stop after "
                        f"ch{run['chapter_in_flight']}")

        # 3. wait for the in-flight chapters
        if not drain(args.url, args.password, runs, args.poll, args.timeout):
            log(f"gave up after {args.timeout:.0f}s — t9.service NOT restarted. "
                "Auto-process stays stopped; resume with:")
            for run in runs:
                if run["resume"]:
                    log(f"  {resume_command(run)}")
            log("(or `python3 restart_t9.py --restore` once they are idle)")
            return 1
        log("all in-flight chapters finished")

        # 4. restart
        restart_services(args.public)
        if not wait_healthy(args.url):
            log(f"⚠ t9.service did not come back within 180s — state kept in "
                f"{STATE_FILE}; run `python3 restart_t9.py --restore` once it is up")
            return 1
        log("t9.service is up")

        # 5. restore
        ok = restore(args.url, args.password, runs)
        if ok:
            STATE_FILE.unlink(missing_ok=True)
        return 0 if ok else 1

    except NotAuthenticated as exc:
        print(exc, file=sys.stderr)
        return 2
    except httpx.HTTPError as exc:
        print(f"Admin server error at {args.url}: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        if STATE_FILE.exists():
            print(f"\nInterrupted. Stopped runs are saved in {STATE_FILE}; "
                  "`python3 restart_t9.py --restore` resumes them.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
