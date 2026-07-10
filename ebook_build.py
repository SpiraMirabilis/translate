"""Cross-process/thread safety helpers for ebook artifact generation.

EPUB/AZW3 builds run from several places at once — the public download
endpoints (request threadpool), the prewarm cron, and admin exports — and
may run from more than one process. Three primitives keep them safe:

- ``book_lock()``: a blocking per-book flock serializing builds. flock
  contends across processes AND across threads of one process (each call
  opens its own file descriptor).
- temp-file + ``os.replace`` writes (in the writers themselves), so a
  half-written artifact is never visible at its final path.
- a ``<artifact>.ver`` sidecar stamp recording the content-version basis an
  artifact was built from, so a file that exists but predates an edit is
  detected as stale instead of being served/uploaded under a fresh version
  key forever.
"""
import fcntl
import os
from contextlib import contextmanager


@contextmanager
def book_lock(cache_dir, lock_id):
    """Blocking exclusive lock scoping one book's artifact builds."""
    os.makedirs(cache_dir, exist_ok=True)
    fh = open(os.path.join(cache_dir, f".build-{lock_id}.lock"), "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fh, fcntl.LOCK_UN)
        finally:
            fh.close()


def stamp_path(artifact_path):
    return artifact_path + ".ver"


def read_stamp(artifact_path):
    try:
        with open(stamp_path(artifact_path), "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return None


def write_stamp(artifact_path, token):
    tmp = stamp_path(artifact_path) + f".tmp{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(str(token or ""))
    os.replace(tmp, stamp_path(artifact_path))


def is_current(artifact_path, token):
    """True when the artifact exists and was stamped with this version token.

    Legacy artifacts with no sidecar count as stale (one rebuild migrates
    them onto the stamped scheme).
    """
    return os.path.exists(artifact_path) and read_stamp(artifact_path) == str(token or "")
