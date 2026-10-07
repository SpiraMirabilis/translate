"""Per-chapter entity index — what the reader's "Terms this chapter" panel reads.

Which entities occur in a chapter used to be knowable only by scanning: the
glossary is keyed by source text, so answering it meant loading every entity of
the book (6-8k rows for the big ones) and testing each against the chapter. That
is fine once per translation; it is not fine once per reader click, least of all
in the public process, which is deliberately kept free of heavy work.

So presence is computed where the chapter is written (save_chapter) and stored
in chapter_entities. Reading it back is a join.

Matching is an exact substring match of the entity's *untranslated* form against
the chapter's source text, NFC-normalised on both sides — the same rule as
`entities_inside_text`'s "exact" bucket, which is what the translation prompt
itself is built from. The "similar" (prefix/suffix) bucket is deliberately not
indexed: it exists to give the model naming-style hints, and a term that merely
shares two characters with something in the chapter is not a term *in* the
chapter.

The index also owns ``entities.last_chapter``: the highest chapter whose source
contains the entity, refreshed whenever a chapter's rows are rewritten. It used
to move only when the translation model happened to re-list an entity it
already knew, which it does for a fraction of them — so a protagonist present
in every chapter could read ten chapters behind.

Staleness is bounded and one-directional: the index is only as current as the
glossary was when the chapter was last saved. An entity created at ch300 that
also occurs at ch5 does not appear in ch5's panel until the book is reindexed
(backfill_chapter_entities.py). It never shows a term that is not there.
"""
import unicodedata


class ChapterEntitiesRepo:
    """Reads and writes the chapter_entities index."""

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

    def entity_index_rows(self, book_id):
        """(entity_id, untranslated) for every entity in scope for a book.

        Book-scoped rows plus global ones, matching get_entities_snapshot. A
        global entity whose source form collides with a book-scoped one loses:
        the book's own record is the rendering that chapter was translated
        against, and listing both would show the reader the same term twice.
        """
        try:
            with self._conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT id, untranslated, book_id FROM entities "
                    "WHERE book_id = ? OR book_id IS NULL", (book_id,))
                rows = cursor.fetchall()
        except Exception as e:
            self.logger.error(f"Error loading entity index rows for book {book_id}: {e}")
            return []

        by_key = {}
        for entity_id, untranslated, ent_book_id in rows:
            if not untranslated:
                continue
            key = unicodedata.normalize('NFC', untranslated)
            existing = by_key.get(key)
            if existing is None or (existing[2] is None and ent_book_id is not None):
                by_key[key] = (entity_id, key, ent_book_id)
        return [(eid, key) for eid, key, _ in by_key.values()]

    @staticmethod
    def _match_entity_rows(source_lines, index_rows):
        """[(entity_id, occurrences)] for entities whose source form is in the text.

        str.count rather than a compiled regex per key: the regex form costs
        ~450ms against an 8k-entity glossary, this costs ~17ms, and with no
        pattern syntax involved there is nothing to escape.
        """
        if isinstance(source_lines, list):
            text = ' '.join(str(line) for line in source_lines)
        elif isinstance(source_lines, str):
            text = source_lines
        else:
            return []
        if not text:
            return []
        text = unicodedata.normalize('NFC', text)

        hits = []
        for entity_id, key in index_rows:
            n = text.count(key)
            if n:
                hits.append((entity_id, n))
        return hits

    def index_chapter_entities(self, chapter_id, book_id, source_lines, index_rows=None,
                               refresh_last_chapter=True):
        """Rebuild one chapter's row set in chapter_entities. Returns the row count.

        Idempotent: the chapter's existing rows are replaced wholesale, so a
        retranslation or an editor save re-states presence rather than
        accumulating it.

        `refresh_last_chapter` re-derives last_chapter for every entity the
        chapter gained or lost. A whole-book reindex turns it off and refreshes
        once at the end instead.
        """
        try:
            if index_rows is None:
                index_rows = self.entity_index_rows(book_id)
            hits = self._match_entity_rows(source_lines, index_rows)

            with self._conn() as conn:
                cursor = conn.cursor()
                touched = set()
                if refresh_last_chapter:
                    cursor.execute("SELECT entity_id FROM chapter_entities WHERE chapter_id = ?",
                                   (chapter_id,))
                    touched = {row[0] for row in cursor.fetchall()}
                cursor.execute("DELETE FROM chapter_entities WHERE chapter_id = ?",
                               (chapter_id,))
                if hits:
                    cursor.executemany(
                        "INSERT INTO chapter_entities (chapter_id, entity_id, occurrences) "
                        "VALUES (?, ?, ?)",
                        [(chapter_id, eid, n) for eid, n in hits])
                if refresh_last_chapter:
                    touched.update(eid for eid, _ in hits)
                    if touched:
                        self._refresh_last_chapters(cursor, book_id, sorted(touched))
            return len(hits)
        except Exception as e:
            self.logger.error(f"Error indexing entities for chapter {chapter_id}: {e}")
            if self.strict_writes:
                raise
            return 0

    def reindex_book_chapter_entities(self, book_id, only_missing=False, progress=None):
        """Reindex every chapter of a book. Returns (chapters_done, rows_written).

        `only_missing` skips chapters that already carry rows — the resumable
        form for the initial backfill. `progress(chapter_number, n_rows)` is
        called after each chapter.
        """
        index_rows = self.entity_index_rows(book_id)

        with self._conn() as conn:
            cursor = conn.cursor()
            if only_missing:
                cursor.execute(
                    "SELECT c.id, c.chapter_number FROM chapters c "
                    "WHERE c.book_id = ? AND NOT EXISTS ("
                    "  SELECT 1 FROM chapter_entities ce WHERE ce.chapter_id = c.id) "
                    "ORDER BY c.chapter_number", (book_id,))
            else:
                cursor.execute(
                    "SELECT id, chapter_number FROM chapters WHERE book_id = ? "
                    "ORDER BY chapter_number", (book_id,))
            chapters = cursor.fetchall()

        done = 0
        rows = 0
        for chapter_id, chapter_number in chapters:
            ch = self.get_chapter(chapter_id=chapter_id)
            if not ch:
                continue
            n = self.index_chapter_entities(chapter_id, book_id,
                                            ch.get('untranslated') or [],
                                            index_rows=index_rows,
                                            refresh_last_chapter=False)
            done += 1
            rows += n
            if progress:
                progress(chapter_number, n)
        if done:
            self.refresh_entity_last_chapters(book_id)
        return done, rows

    # ------------------------------------------------------------------
    # last_chapter
    # ------------------------------------------------------------------

    _LAST_CHAPTER_ID_BATCH = 500

    def _refresh_last_chapters(self, cursor, book_id, entity_ids=None):
        """Set last_chapter from the index for a book's entities.

        Only the book's own rows: a global entity has no single book to have a
        last chapter in. An entity with no index rows at all (its source form
        occurs in no saved chapter — a mis-keyed record, or one that only
        appears in queued chapters) keeps whatever it had rather than going
        NULL. Returns the number of rows updated.
        """
        sql = ("UPDATE entities SET last_chapter = ("
               "  SELECT MAX(c.chapter_number) FROM chapter_entities ce"
               "  JOIN chapters c ON c.id = ce.chapter_id"
               "  WHERE ce.entity_id = entities.id AND c.book_id = ?) "
               "WHERE book_id = ? AND EXISTS ("
               "  SELECT 1 FROM chapter_entities ce"
               "  JOIN chapters c ON c.id = ce.chapter_id"
               "  WHERE ce.entity_id = entities.id AND c.book_id = ?)")
        if entity_ids is None:
            cursor.execute(sql, (book_id, book_id, book_id))
            return cursor.rowcount
        updated = 0
        for i in range(0, len(entity_ids), self._LAST_CHAPTER_ID_BATCH):
            batch = entity_ids[i:i + self._LAST_CHAPTER_ID_BATCH]
            marks = ",".join("?" * len(batch))
            cursor.execute(f"{sql} AND id IN ({marks})",
                           (book_id, book_id, book_id, *batch))
            updated += cursor.rowcount
        return updated

    def refresh_entity_last_chapters(self, book_id, entity_ids=None):
        """Re-derive last_chapter from chapter_entities. Returns rows updated."""
        try:
            with self._conn() as conn:
                return self._refresh_last_chapters(
                    conn.cursor(), book_id,
                    sorted(set(entity_ids)) if entity_ids is not None else None)
        except Exception as e:
            self.logger.error(f"Error refreshing last_chapter for book {book_id}: {e}")
            if self.strict_writes:
                raise
            return 0

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def entities_with_note_change_at(self, book_id, chapter_number, entity_ids=None):
        """Entity ids whose note was *rewritten* during this chapter.

        Feeds the "note updated" chip: the note shown for this chapter is not
        the one an earlier chapter's reader saw, which usually means the
        character's situation moved (a breakthrough, a promotion, a revealed
        identity).

        Creations are excluded (`previous_note IS NOT NULL`) — an entity's
        first note is new because the entity is, which the "new" chip already
        says, and this way the distinction does not depend on
        `entities.origin_chapter`, which records when extraction ran rather
        than when the entity appeared.

        Revisions carrying no chapter (hand edits, script sweeps) belong to the
        present, not to a chapter, and are never flagged here — the same rule
        notes_as_of applies when it rewinds.
        """
        if entity_ids is not None and not entity_ids:
            return set()
        try:
            sql = ("SELECT DISTINCT entity_id FROM entity_note_revisions "
                   "WHERE book_id = ? AND chapter_number = ? "
                   "AND previous_note IS NOT NULL AND previous_note <> ''")
            params = [book_id, chapter_number]
            if entity_ids is not None and len(entity_ids) <= 500:
                sql += " AND entity_id IN (" + ",".join("?" * len(entity_ids)) + ")"
                params.extend(entity_ids)
            with self._conn() as conn:
                cursor = conn.cursor()
                cursor.execute(sql, params)
                return {row[0] for row in cursor.fetchall()}
        except Exception as e:
            self.logger.error(
                f"Error checking note changes for book {book_id} ch{chapter_number}: {e}")
            return set()

    def get_chapter_terms(self, book_id, chapter_number, published_only=False,
                          include_notes=True):
        """The glossary for one chapter, as a reader-facing list of dicts.

        Each row: id, category, untranslated, translation, occurrences,
        first_seen (the chapter introduced this entity), note_changed (the note
        was rewritten during it), gender (only for categories this book
        gender-tracks) and note.

        The note is the note **as it read at this chapter** (notes_as_of), not
        the current one. A note tracks the present state of a character — age,
        realm, rank, allegiance — so handing a reader on chapter 12 the note
        written at chapter 900 would spoil the book with its own glossary.

        Returns [] for an unindexed chapter, which is also what an unindexed
        chapter looks like to the panel: no terms, no error.
        """
        try:
            pub_clause, pub_param = self._published_filter("c.")
            gate = f" AND {pub_clause}" if published_only else ""
            params = [book_id, chapter_number]
            if published_only:
                params.append(pub_param)

            with self._conn() as conn:
                cursor = conn.cursor()
                cursor.execute(f'''
                    SELECT e.id, e.category, e.untranslated, e.translation,
                           e.gender, e.origin_chapter, ce.occurrences
                    FROM chapter_entities ce
                    JOIN chapters c ON c.id = ce.chapter_id
                    JOIN entities e ON e.id = ce.entity_id
                    WHERE c.book_id = ? AND c.chapter_number = ?{gate}
                ''', params)
                rows = cursor.fetchall()
        except Exception as e:
            self.logger.error(
                f"Error loading chapter terms for book {book_id} ch{chapter_number}: {e}")
            return []

        if not rows:
            return []

        gendered = set(self.get_book_gendered_categories(book_id))
        notes = {}
        note_changed = set()
        if include_notes:
            try:
                entity_ids = [r[0] for r in rows]
                notes = self.notes_as_of(book_id, chapter_number,
                                         entity_ids=entity_ids) or {}
                note_changed = self.entities_with_note_change_at(book_id, chapter_number,
                                                                 entity_ids)
            except Exception as e:
                self.logger.error(f"Error resolving point-in-time notes: {e}")

        terms = []
        for entity_id, category, untranslated, translation, gender, origin_chapter, occurrences in rows:
            term = {
                "id": entity_id,
                "category": category,
                "untranslated": untranslated,
                "translation": translation,
                "occurrences": occurrences or 1,
                "first_seen": origin_chapter == chapter_number,
            }
            if category in gendered and gender:
                term["gender"] = gender
            note = notes.get(entity_id)
            if note:
                term["note"] = note
                # Only meaningful alongside a note the reader can see: a
                # revision that cleared a note leaves nothing to flag.
                term["note_changed"] = entity_id in note_changed
            terms.append(term)

        # Book's own category order first (that is the order the Entities page
        # and the prompt use), unknown categories after it, then the terms the
        # chapter leans on most.
        order = {name: i for i, name in enumerate(self.get_book_categories(book_id))}
        terms.sort(key=lambda t: (order.get(t["category"], len(order)),
                                  t["category"],
                                  -t["occurrences"],
                                  (t["translation"] or "").lower()))
        return terms
