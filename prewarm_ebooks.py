#!/usr/bin/env python3
"""
Prewarm public EPUB + AZW3 artifacts so downloads never wait on generation.

Honors SPACES_ENABLED: with Spaces on, artifacts are prewarmed into the
versioned CDN keys (the public endpoint then redirects to the CDN); with it
off, they're prewarmed into the local epub_cache/ directory (the endpoint
serves those files directly).

Designed to run from cron every 5 minutes. On each tick it:
  1. Takes an exclusive lock so a still-running previous instance blocks this one
     (flock is released automatically if the previous process died).
  2. Bails out early when the machine is under load (translation jobs etc.) so it
     never competes with foreground work.
  3. Walks every public book with published chapters and, for each:
       - skips the book when both the EPUB and AZW3 already exist — checked
         against the versioned Spaces keys, or (Spaces off) against the files in
         epub_cache/, which invalidate_epub_cache() clears on any content change;
       - skips books modified in the last 15 minutes (likely mid-translation/edit,
         so the content is still churning — don't waste a build);
       - otherwise generates the published-only EPUB and converts it to AZW3,
         uploading to Spaces (pruning stale versions) or leaving them on local disk.

The artifacts are byte-for-byte what web/api/public.py serves, so a prewarmed
book makes the public /epub and /azw3 endpoints return immediately.

A tick with nothing to prewarm prints nothing at all, so the cron log only ever
shows real activity (builds, failures). Pass --verbose to log every tick.

Usage:
    python3 prewarm_ebooks.py [--dry-run] [--force] [--book-id N] [--verbose]
                              [--max-load F] [--max-minutes M] [--quiet-minutes Q]

Cron (every 5 min), logging to a file:
    */5 * * * * cd /home/mdm/t9 && /usr/bin/python3 prewarm_ebooks.py >> /home/mdm/t9/logs/prewarm_ebooks.log 2>&1
"""
import argparse
import datetime
import fcntl
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import TranslationConfig
from logger import Logger
from database import DatabaseManager
import spaces
import azw3
import ebook_build
from output_formatter import OutputFormatter

# Lock lives under the project dir, not /tmp — a world-writable predictable
# path lets any local user grab the flock (silently disabling prewarm) or
# plant a symlink.
LOCK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         ".prewarm_ebooks.lock")

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
MAX_RUN_MINUTES = 60.0


# Logging. Cron fires every 5 minutes and most ticks have nothing to build, so a
# quiet run must stay completely silent — the log file should only ever show real
# activity. "Context" lines (mode banner, deferral notices, the closing summary)
# are therefore held back, timestamped at the moment they occur, and printed only
# once the run does actual work. Manual runs (--verbose / --dry-run / --book-id)
# print everything immediately.
_verbose = False
_had_activity = False
_deferred = []


def _fmt(msg):
    return f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"


def _log(msg):
    print(_fmt(msg), flush=True)


def _context(msg):
    """A line worth printing only if this run turns out to have done something."""
    if _verbose or _had_activity:
        _log(msg)
    else:
        _deferred.append(_fmt(msg))  # keep the timestamp from when it happened


def _activity(msg):
    """Real work (a build, a failure): flush any held context, then log."""
    global _had_activity
    _had_activity = True
    while _deferred:
        print(_deferred.pop(0), flush=True)
    _log(msg)


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


def _build_published_epub(db, config, logger, book, epub_path, version_basis):
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
    # Build directly onto the cache path (atomic tmp+replace inside) and
    # version-stamp it so the public endpoint agrees the file is current.
    output_path = formatter.save_book_as_epub(all_chapters, book_info, output_path=epub_path)
    if not output_path or not os.path.exists(epub_path):
        return False
    ebook_build.write_stamp(epub_path, version_basis)
    return True


def _process_book(db, config, logger, book, args, azw3_ok, spaces_on):
    """Prewarm one book. Returns a short status string for logging.

    Existence is judged differently by deployment:
      - Spaces on:  against the versioned CDN keys (exact per content version).
      - Spaces off: against the local epub_cache/ files. invalidate_epub_cache()
        deletes those on any content change, so their presence means they're
        current; generated files just stay on disk with no upload.
    """
    book_id = book["id"]
    cache_dir = db._epub_cache_dir()
    epub_path = os.path.join(cache_dir, f"{book_id}.epub")
    azw3_path = os.path.join(cache_dir, f"{book_id}.azw3")

    # Same content-version basis the public endpoint uses: modified_date OR the
    # latest already-passed publish time (so a scheduled chapter going live bumps
    # the version). Keys derived from this match the endpoint's byte-for-byte,
    # and the local cache file is stamped with it in both modes.
    latest_published = db.latest_published_at(book_id)
    version_basis = max(filter(None, [book.get("modified_date"), latest_published]),
                        default=None)

    if spaces_on:
        ver = spaces.epub_version(book_id, version_basis)
        epub_key = spaces.epub_key(config, book_id, ver)
        azw3_key = spaces.azw3_key(config, book_id, ver)
        try:
            have_epub = spaces.exists(config, epub_key)
            have_azw3 = spaces.exists(config, azw3_key) if azw3_ok else True  # can't/needn't build
        except spaces.SpacesUnavailable as e:
            # Transient outage — DON'T treat as missing, or a network blip
            # would trigger a full rebuild + re-upload of every book.
            return f"skip: Spaces unavailable ({e})"
        dest = f"ver {ver}"
    else:
        # Local-only: the on-disk cache is the whole story. The version stamp
        # (not bare existence) decides currency; legacy unstamped files
        # rebuild once and converge onto the stamped scheme.
        have_epub = ebook_build.is_current(epub_path, version_basis)
        have_azw3 = os.path.exists(azw3_path) if azw3_ok else True  # can't/needn't build
        dest = "local cache"

    need_epub = not have_epub
    need_azw3 = azw3_ok and not have_azw3
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
        return f"DRY-RUN would build: {', '.join(todo)} ({dest})"

    # Rebuild the EPUB fresh whenever we're doing any work — AZW3 needs a local
    # source EPUB, and a stale on-disk file might belong to an older version.
    # The per-book flock is shared with the public endpoints, so a reader
    # request racing this cron tick waits and then reuses the fresh build.
    with ebook_build.book_lock(cache_dir, book_id):
        if not _build_published_epub(db, config, logger, book, epub_path, version_basis):
            return "error: EPUB generation failed / no chapters"

        built = []
        if need_epub:
            # Local-only: the build above already wrote epub_path — nothing to upload.
            if spaces_on:
                if not spaces.upload(config, epub_path, epub_key, "application/epub+zip"):
                    return "error: EPUB upload failed"
                spaces.prune_epub_versions(config, book_id, keep_key=epub_key)
            built.append("epub")

    if need_azw3:
        with ebook_build.book_lock(cache_dir, f"{book_id}-azw3"):
            if not azw3.convert_epub_to_azw3(epub_path, azw3_path, logger):
                return f"partial: built {built or ['(none)']}, AZW3 conversion failed"
            if spaces_on:
                if not spaces.upload(config, azw3_path, azw3_key, "application/x-mobi8-ebook"):
                    return f"partial: built {built}, AZW3 upload failed"
                spaces.prune_azw3_versions(config, book_id, keep_key=azw3_key)
        built.append("azw3")

    return f"built: {', '.join(built)} ({dest})"


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
    ap.add_argument("--verbose", action="store_true",
                    help="Log every tick, even when there is nothing to prewarm.")
    args = ap.parse_args()

    # A cron tick with nothing to do prints nothing at all. Manual invocations
    # are chatty so you can see what the run decided.
    global _verbose
    _verbose = args.verbose or args.dry_run or args.book_id is not None

    # 1) Single-instance guard.
    lock = _acquire_lock()
    if lock is None:
        _context("Another instance is running — exiting.")
        return 0

    try:
        config = TranslationConfig()
        logger = Logger(config)

        # Honor SPACES_ENABLED: with Spaces on we prewarm the versioned CDN keys;
        # with it off the public endpoint serves from the local epub_cache/, so we
        # prewarm those files instead.
        spaces_on = spaces.is_enabled(config)
        _context("Spaces enabled — prewarming versioned CDN keys." if spaces_on
                 else "Spaces disabled — prewarming the local epub_cache/ only.")

        # 2) Load guard.
        ncpu = os.cpu_count() or 1
        max_load = args.max_load if args.max_load is not None else ncpu * MAX_LOAD_PER_CPU
        ok, load1 = _load_ok(max_load)
        if not ok:
            _context(f"Load average {load1:.2f} > {max_load:.2f} — deferring. Exiting.")
            return 0

        db = DatabaseManager(config, logger)
        azw3_ok = azw3.is_available()
        if not azw3_ok:
            _context("ebook-convert not available — will prewarm EPUB only (no AZW3).")

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
                _context(f"Hit {args.max_minutes:.1f}m run budget — stopping; next tick resumes.")
                break
            ok, load1 = _load_ok(max_load)
            if not ok:
                _context(f"Load rose to {load1:.2f} > {max_load:.2f} mid-run — stopping.")
                break

            try:
                status = _process_book(db, config, logger, book, args, azw3_ok, spaces_on)
            except Exception as e:
                status = f"error: {e}"
            label = f'#{book["id"]} "{book.get("title", "")[:48]}"'
            if status.startswith("built") or status.startswith("DRY-RUN") or status.startswith("partial"):
                n_built += 1
                _activity(f"{label}: {status}")
            elif status.startswith("error"):
                n_err += 1
                _activity(f"{label}: {status}")
            else:
                n_skip += 1  # up-to-date / too-fresh: routine, never worth a log line
                if _verbose:
                    _log(f"{label}: {status}")

        # Only summarises a run that actually did something (or a manual run).
        _context(f"Done. built/attempted={n_built} skipped={n_skip} errors={n_err}")
        return 0
    finally:
        lock.close()  # releases the flock


if __name__ == "__main__":
    sys.exit(main())
