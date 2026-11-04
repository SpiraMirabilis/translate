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
  detected as stale instead of being uploaded under a fresh version key.

Stale is not missing. A content change only moves the version basis (see
``DatabaseManager.ebook_version_basis``); the old artifact stays in Spaces (or
on disk, with Spaces off) and the public endpoints keep serving it until
prewarm_ebooks.py rebuilds. ``served_version`` recovers which version that old
artifact is, so its Spaces key can still be found.

With Spaces on, the local file is only a build product: ``offload`` uploads it,
confirms the object's size, and deletes the local copy, keeping the ``.ver``
stamp. The stamp then records the version the CDN is serving — the artifact
itself lives only there (it can always be rebuilt from the database + covers).
With Spaces off nothing is offloaded and epub_cache/ is the serving copy.

Lock order: a book's EPUB lock (``book_id``) is always taken before its AZW3
lock (``f"{book_id}-azw3"``), because an AZW3 build needs the source EPUB on
disk for the whole conversion and only the EPUB lock keeps it there.
"""
import fcntl
import os
import time
from contextlib import contextmanager

# Per public format: (Spaces key builder, Spaces prune function, content type).
_SPACES_FORMATS = {
    "epub": ("epub_key", "prune_epub_versions", "application/epub+zip"),
    "azw3": ("azw3_key", "prune_azw3_versions", "application/x-mobi8-ebook"),
}


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


def served_version(artifact_path):
    """Version basis the last build of this artifact was made from, or None when
    nothing was ever stamped. The artifact may live on disk or (offloaded) only
    in Spaces — callers look the version up there and fall back to the local
    file only if it exists. Differs from the current basis exactly when the
    artifact is stale."""
    return read_stamp(artifact_path)


def age_minutes(artifact_path, require_file=True):
    """Minutes since the artifact was built — the stamp's mtime (written right
    after the build), else the file's own. Infinite when there is no artifact,
    so "missing" always counts as older than any staleness ceiling.

    ``require_file=False`` is for Spaces mode, where an offloaded artifact has
    only its stamp left on disk; the caller has already confirmed the CDN
    copy exists (or treats it as missing anyway)."""
    if require_file and not os.path.exists(artifact_path):
        return float("inf")
    paths = (stamp_path(artifact_path), artifact_path) if require_file \
        else (stamp_path(artifact_path),)
    for path in paths:
        try:
            return (time.time() - os.path.getmtime(path)) / 60.0
        except OSError:
            continue
    return float("inf")


def drop_local(artifact_path):
    """Delete the local artifact, keeping its stamp (which now describes the
    copy in Spaces). Missing file is fine."""
    try:
        os.remove(artifact_path)
    except FileNotFoundError:
        pass


def spaces_key(config, book_id, fmt, version_basis):
    """Spaces key a public artifact of `fmt` has at `version_basis`."""
    import spaces
    ver = spaces.epub_version(book_id, version_basis)
    return getattr(spaces, _SPACES_FORMATS[fmt][0])(config, book_id, ver)


def offload(config, book_id, fmt, artifact_path, version_basis, drop=True, logger=None):
    """Upload a freshly built public artifact to its versioned Spaces key,
    confirm it landed (HEAD size == local size), prune the book's other
    versions of that format, and (``drop``) delete the local file.

    Returns the key on success, None when Spaces is off or anything failed —
    in which case the local file is kept and remains the serving copy (the
    next prewarm tick retries). Call under the artifact's book lock.
    """
    import spaces
    if not spaces.is_enabled(config):
        return None
    key = spaces_key(config, book_id, fmt, version_basis)
    _key_fn, prune_fn, content_type = _SPACES_FORMATS[fmt]
    try:
        local_size = os.path.getsize(artifact_path)
        if not spaces.upload(config, artifact_path, key, content_type):
            return None
        remote_size = spaces.object_size(config, key)
    except Exception as e:  # SpacesUnavailable, OSError
        if logger:
            logger.warning(f"Offload of {artifact_path} failed, keeping local copy: {e}")
        return None
    if remote_size != local_size:
        if logger:
            logger.warning(f"Offload of {artifact_path} unconfirmed (local {local_size} B, "
                           f"Spaces {remote_size} B), keeping local copy")
        return None
    getattr(spaces, prune_fn)(config, book_id, keep_key=key)
    if drop:
        drop_local(artifact_path)
    return key


def drop_if_offloaded(config, book_id, fmt, artifact_path):
    """Delete a local public artifact when Spaces already holds the exact
    version its stamp records, at the same size. For copies left behind by
    builds that predate offloading, or by an upload that failed. Returns True
    when the file was deleted. Call under the artifact's book lock."""
    import spaces
    if not os.path.exists(artifact_path):
        return False
    basis = read_stamp(artifact_path)
    if basis is None:
        return False  # unstamped legacy file — can't tell which key it is
    key = spaces_key(config, book_id, fmt, basis)
    if spaces.object_size(config, key) != os.path.getsize(artifact_path):
        return False
    drop_local(artifact_path)
    return True
