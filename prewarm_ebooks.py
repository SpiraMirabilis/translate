#!/usr/bin/env python3
"""
Prewarm public EPUB + AZW3 artifacts into Spaces/CDN.

Designed to run from cron every 5 minutes. On each tick it:
  1. Takes an exclusive lock so a still-running previous instance blocks this one
     (flock is released automatically if the previous process died).
  2. Bails out early when the machine is under load (translation jobs etc.) so it
     never competes with foreground work.
  3. Walks every public book with published chapters. For each, it computes the
     book's current content version (the same token the public download endpoint
     uses) and:
       - skips the book when both the EPUB and AZW3 for that version already exist
         in Spaces (nothing to do);
       - skips books modified in the last 15 minutes (likely mid-translation/edit,
         so the content version is still churning — don't waste a build);
       - otherwise generates the published-only EPUB, converts it to AZW3, and
         uploads both under their versioned Spaces keys (pruning stale versions).

The artifacts and keys are byte-for-byte what web/api/public.py serves, so a
prewarmed book makes the public /epub and /azw3 endpoints redirect straight to
the CDN with no on-demand generation.

Usage:
    python3 prewarm_ebooks.py [--dry-run] [--force] [--book-id N]
                              [--max-load F] [--max-minutes M] [--quiet-minutes Q]

Cron (every 5 min), logging to a file:
    */5 * * * * cd /home/mdm/t9 && /usr/bin/python3 prewarm_ebooks.py >> /home/mdm/t9/logs/prewarm_ebooks.log 2>&1
"""
import argparse
import datetime
import fcntl
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import TranslationConfig
from logger import Logger
from database import DatabaseManager
import spaces
import azw3
from output_formatter import OutputFormatter

LOCK_PATH = "/tmp/t9_prewarm_ebooks.lock"

# Load ceiling: skip the run when the 1-minute load average exceeds
# cpu_count * MAX_LOAD_PER_CPU. Re-checked before each book so a mid-run spike
# (e.g. a translation job starting) yields the machine promptly.
MAX_LOAD_PER_CPU = 0.7

# Don't build a book that changed within this many minutes — its content version
# is probably still moving as chapters are added/edited.
QUIET_MINUTES = 15

# Soft wall-clock budget for one run. We stop cleanly before the next cron tick
# so work stays incremental and load re-checks stay meaningful; the next tick
# picks up where this one left off.
MAX_RUN_MINUTES = 4.0


def _log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def _acquire_lock():
    """Return an open, flock'd file handle, or None if another instance holds it."""
    fh = open(LOCK_PATH, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    fh.write(str(os.getpid()))
    fh.flush()
    return fh


def _load_ok(max_load):
    try:
        load1 = os.getloadavg()[0]
    except OSError:
        return True, 0.0
    return load1 <= max_load, load1


def _minutes_since(iso_str):
    """Minutes since an ISO timestamp (naive, server-local like the rest of T9).
    Returns a large number when the timestamp is missing/unparseable so a book
    with no modified_date is never treated as 'too fresh'."""
    if not iso_str:
        return float("inf")
    try:
        dt = datetime.datetime.fromisoformat(str(iso_str).strip().replace("Z", ""))
    except (ValueError, TypeError):
        return float("inf")
    return (datetime.datetime.now() - dt).total_seconds() / 60.0


def _build_published_epub(db, config, logger, book, epub_path):
    """Generate the published-only EPUB for a book onto epub_path. Returns True on
    success. Mirrors web/api/public.py's generation exactly (content + book_info)."""
    book_id = book["id"]
    book_info = {
        "id": book_id,
        "title": book.get("title", "Unknown"),
        "author": book.get("author") or "Translator",
        "language": book.get("language") or "en",
    }
    if book.get("cover_image"):
        cover_full = os.path.join(config.script_dir, book["cover_image"])
        if os.path.exists(cover_full):
            book_info["cover_image"] = cover_full

    all_chapters = [
        {
            "chapter": ch["chapter"],
            "title": ch.get("title") or f"Chapter {ch['chapter']}",
            "content": ch.get("content", []),
        }
        for ch in db.get_chapters_bulk(book_id, published_only=True)
    ]
    if not all_chapters:
        return False

    formatter = OutputFormatter(config, logger)
    output_path = formatter.save_book_as_epub(all_chapters, book_info)
    if not output_path or not os.path.exists(output_path):
        return False

    os.makedirs(os.path.dirname(epub_path), exist_ok=True)
    shutil.copy2(output_path, epub_path)
    return True


def _process_book(db, config, logger, book, args, azw3_ok):
    """Prewarm one book. Returns a short status string for logging."""
    book_id = book["id"]

    # Same content-version basis the public endpoint uses: modified_date OR the
    # latest already-passed publish time (so a scheduled chapter going live bumps
    # the version). Keys derived from this match the endpoint's byte-for-byte.
    latest_published = db.latest_published_at(book_id)
    version_basis = max(filter(None, [book.get("modified_date"), latest_published]),
                        default=None)
    ver = spaces.epub_version(book_id, version_basis)
    epub_key = spaces.epub_key(config, book_id, ver)
    azw3_key = spaces.azw3_key(config, book_id, ver)

    have_epub = spaces.exists(config, epub_key)
    have_azw3 = spaces.exists(config, azw3_key) if azw3_ok else True  # can't/needn't build

    need_epub = not have_epub
    need_azw3 = azw3_ok and not spaces.exists(config, azw3_key)
    if not need_epub and not need_azw3:
        return "skip: up-to-date"

    # Freshness guard — don't build a book that's actively changing.
    mins = _minutes_since(book.get("modified_date"))
    if not args.force and mins < args.quiet_minutes:
        return f"skip: modified {mins:.0f}m ago (<{args.quiet_minutes}m)"

    todo = []
    if need_epub:
        todo.append("epub")
    if need_azw3:
        todo.append("azw3")
    if args.dry_run:
        return f"DRY-RUN would build: {', '.join(todo)} (ver {ver})"

    cache_dir = db._epub_cache_dir()
    epub_path = os.path.join(cache_dir, f"{book_id}.epub")

    # Rebuild the EPUB fresh whenever we're doing any work — AZW3 needs a local
    # source EPUB, and a stale on-disk file might belong to an older version.
    if not _build_published_epub(db, config, logger, book, epub_path):
        return "error: EPUB generation failed / no chapters"

    built = []
    if need_epub:
        if spaces.upload(config, epub_path, epub_key, "application/epub+zip"):
            spaces.prune_epub_versions(config, book_id, keep_key=epub_key)
            built.append("epub")
        else:
            return "error: EPUB upload failed"

    if need_azw3:
        azw3_path = os.path.join(cache_dir, f"{book_id}.azw3")
        if not azw3.convert_epub_to_azw3(epub_path, azw3_path, logger):
            return f"partial: built {built or ['(none)']}, AZW3 conversion failed"
        if spaces.upload(config, azw3_path, azw3_key, "application/x-mobi8-ebook"):
            spaces.prune_azw3_versions(config, book_id, keep_key=azw3_key)
            built.append("azw3")
        else:
            return f"partial: built {built}, AZW3 upload failed"

    return f"built: {', '.join(built)} (ver {ver})"


def main():
    ap = argparse.ArgumentParser(description="Prewarm public EPUB + AZW3 into Spaces.")
    ap.add_argument("--dry-run", action="store_true", help="Report what would build; change nothing.")
    ap.add_argument("--force", action="store_true", help="Ignore the recent-modification freshness guard.")
    ap.add_argument("--book-id", type=int, default=None, help="Only process this book.")
    ap.add_argument("--max-load", type=float, default=None,
                    help="Skip run if 1-min load average exceeds this (default: cpu_count * %.2f)." % MAX_LOAD_PER_CPU)
    ap.add_argument("--max-minutes", type=float, default=MAX_RUN_MINUTES,
                    help="Soft wall-clock budget for the run (default %.1f)." % MAX_RUN_MINUTES)
    ap.add_argument("--quiet-minutes", type=float, default=QUIET_MINUTES,
                    help="Skip books modified within this many minutes (default %d)." % QUIET_MINUTES)
    args = ap.parse_args()

    # 1) Single-instance guard.
    lock = _acquire_lock()
    if lock is None:
        _log("Another instance is running — exiting.")
        return 0

    try:
        config = TranslationConfig()
        logger = Logger(config)

        if not spaces.is_enabled(config):
            _log("Spaces is disabled/unconfigured — nothing to prewarm into blob storage. Exiting.")
            return 0

        # 2) Load guard.
        ncpu = os.cpu_count() or 1
        max_load = args.max_load if args.max_load is not None else ncpu * MAX_LOAD_PER_CPU
        ok, load1 = _load_ok(max_load)
        if not ok:
            _log(f"Load average {load1:.2f} > {max_load:.2f} — deferring. Exiting.")
            return 0

        db = DatabaseManager(config, logger)
        azw3_ok = azw3.is_available()
        if not azw3_ok:
            _log("ebook-convert not available — will prewarm EPUB only (no AZW3).")

        books = db.list_books(order_by="title")
        if args.book_id is not None:
            books = [b for b in books if b["id"] == args.book_id]

        deadline = time.monotonic() + args.max_minutes * 60.0
        n_built = n_skip = n_err = 0

        for book in books:
            if not book.get("is_public", True):
                continue
            if not book.get("published_chapter_count", 0):
                continue

            if time.monotonic() > deadline:
                _log(f"Hit {args.max_minutes:.1f}m run budget — stopping; next tick resumes.")
                break
            ok, load1 = _load_ok(max_load)
            if not ok:
                _log(f"Load rose to {load1:.2f} > {max_load:.2f} mid-run — stopping.")
                break

            try:
                status = _process_book(db, config, logger, book, args, azw3_ok)
            except Exception as e:
                status = f"error: {e}"
            label = f'#{book["id"]} "{book.get("title", "")[:48]}"'
            if status.startswith("built") or status.startswith("DRY-RUN") or status.startswith("partial"):
                n_built += 1
                _log(f"{label}: {status}")
            elif status.startswith("error"):
                n_err += 1
                _log(f"{label}: {status}")
            else:
                n_skip += 1  # up-to-date / too-fresh: quiet unless you want the detail
                if args.book_id is not None or args.dry_run:
                    _log(f"{label}: {status}")

        _log(f"Done. built/attempted={n_built} skipped={n_skip} errors={n_err}")
        return 0
    finally:
        lock.close()  # releases the flock


if __name__ == "__main__":
    sys.exit(main())
