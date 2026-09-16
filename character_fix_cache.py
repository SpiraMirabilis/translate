"""Persistent decision cache for the character-count fixer.

Stores the model's decision keyed by a hash of (model + prompt + match context),
so repeated runs over the same book don't re-query the model for unchanged text.
Backed by the shared db_backend abstraction, so it works on both SQLite (default)
and MySQL.

Decision semantics: an empty string means "keep unchanged"; a non-empty string
is the replacement text for the matched span. A missing row (get -> None) means
"not yet decided" and triggers a model query.
"""

import logging

from db_backend import create_backend

logger = logging.getLogger(__name__)

# VARCHAR(64) (not TEXT) so the PRIMARY KEY is valid on MySQL too; a sha256 hex
# digest is exactly 64 chars. Single DDL valid on both backends.
_DDL = (
    "CREATE TABLE IF NOT EXISTS character_fix_cache ("
    "cache_key VARCHAR(64) PRIMARY KEY, "
    "decision TEXT)"
)


class CharacterFixCache:
    """A get/set decision cache over the shared db backend.

    Uses its own connection so its commits don't entangle a caller's
    transaction (e.g. the runner's chapter-read transaction during a dry run).
    """

    def __init__(self, backend=None):
        self._backend = backend or create_backend()
        self._conn = self._backend.get_connection()
        cur = self._conn.cursor()
        cur.execute(_DDL)
        self._conn.commit()

    def get(self, key):
        """Return the stored decision for *key*, or None if not cached."""
        cur = self._conn.cursor()
        cur.execute("SELECT decision FROM character_fix_cache WHERE cache_key = ?", (key,))
        row = cur.fetchone()
        return None if row is None else row[0]

    def set(self, key, value):
        """Store a decision. Ignores duplicate keys (the value is deterministic
        for a given key, so an existing row already holds the right answer)."""
        cur = self._conn.cursor()
        try:
            cur.execute(
                "INSERT INTO character_fix_cache (cache_key, decision) VALUES (?, ?)",
                (key, value),
            )
        except Exception:
            pass  # key already cached — deterministic value, nothing to do

    def commit(self):
        self._conn.commit()

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass
