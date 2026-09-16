"""Shared entity-name lookups for modules that need a "is this a speaker?" gate.

Several modules key a line transform on whether some short prefix is really a
*person* — a chat username, a dialogue speaker label — rather than the tail of a
narrative clause that happens to end in a colon. Regex shape alone is too loose
for that (``茉莉安慰道：`` matches every name-like pattern), so the reliable gate
is an exact match against the book's own entity records.

``load_person_names`` returns the union of ``untranslated`` and ``translation``
names, so one set gates both the source side (Chinese name) and the translated
side (English name) of the same transform.
"""


def load_person_names(db, book_id):
    """Set of person-like entity names for ``book_id``, or None if unavailable.

    Person-like means *carrying a gender* — the attribute the entity schema puts
    on characters and chatgroup usernames — which is what distinguishes a speaker
    from an ability or a place. Book-scoped entities and globals
    (``book_id IS NULL``) both count.

    Returns ``None`` — meaning "gate undeterminable", NOT "no names" — when there
    is no db/book id or the query fails. Callers decide what that means for them:
    skip the gate (looser) or skip the transform (safer).
    """
    if db is None or not book_id:
        return None
    try:
        conn = db.backend.get_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT untranslated, translation FROM entities "
            "WHERE (book_id = ? OR book_id IS NULL) "
            "AND gender IS NOT NULL AND gender != ''",
            (book_id,))
        names = {v for row in cur.fetchall() for v in row[:2] if v}
        conn.close()
        return names
    except Exception:  # noqa: BLE001 - a gate lookup must never break ingest
        return None
