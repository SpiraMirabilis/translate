import json


class FootnotesRepo:
    """Persistent footnotes and in-chapter illustrations."""

    # Illustration management section
    def add_illustration(self, book_id, marker_id, filename, alt=None,
                         original_href=None, ordinal=None, queue_id=None, chapter_id=None):
        """Insert an illustration row, idempotent on (book_id, marker_id).

        Returns True if a new row was inserted, False if it already existed or
        on error. The marker_id is the opaque id embedded in chapter content as
        ⟦IMG:<marker_id>⟧ (see illustrations.py).
        """
        try:
            with self._conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    'SELECT id FROM illustrations WHERE book_id = ? AND marker_id = ?',
                    (book_id, marker_id),
                )
                if cursor.fetchone():
                    return False

                from datetime import datetime
                cursor.execute('''
                INSERT INTO illustrations
                    (book_id, marker_id, filename, alt, original_href, ordinal,
                     queue_id, chapter_id, created_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (book_id, marker_id, filename, alt, original_href, ordinal,
                      queue_id, chapter_id, datetime.now().isoformat()))
            return True
        except Exception as e:
            self.logger.error(f"Error adding illustration {marker_id} for book {book_id}: {e}")
            return False

    def get_book_illustration(self, book_id, marker_id):
        """Return a single illustration row as a dict, or None."""
        try:
            with self._conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    'SELECT id, book_id, marker_id, filename, alt, original_href, '
                    'ordinal, queue_id, chapter_id FROM illustrations '
                    'WHERE book_id = ? AND marker_id = ?',
                    (book_id, marker_id),
                )
                row = cursor.fetchone()
            if not row:
                return None
            cols = ['id', 'book_id', 'marker_id', 'filename', 'alt',
                    'original_href', 'ordinal', 'queue_id', 'chapter_id']
            return dict(zip(cols, row))
        except Exception as e:
            self.logger.error(f"Error fetching illustration {marker_id} for book {book_id}: {e}")
            return None

    def get_chapter_illustrations(self, book_id, chapter_id):
        """Return a chapter's illustration rows (list of dicts), ordered by ordinal."""
        try:
            with self._conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    'SELECT id, book_id, marker_id, filename, alt, original_href, '
                    'ordinal, queue_id, chapter_id FROM illustrations '
                    'WHERE book_id = ? AND chapter_id = ? ORDER BY ordinal',
                    (book_id, chapter_id),
                )
                rows = cursor.fetchall()
            cols = ['id', 'book_id', 'marker_id', 'filename', 'alt',
                    'original_href', 'ordinal', 'queue_id', 'chapter_id']
            return [dict(zip(cols, r)) for r in rows]
        except Exception as e:
            self.logger.error(f"Error fetching illustrations for chapter {chapter_id}: {e}")
            return []

    def link_illustrations_to_chapter(self, book_id, chapter_id, marker_ids):
        """Set chapter_id on the illustration rows for the given marker ids.

        Called after save_chapter so a queue-time illustration row becomes
        associated with its chapter. The markers in the saved content array are
        the linkage, so this is robust to renumber/requeue/retranslate.
        """
        if not marker_ids:
            return 0
        try:
            with self._conn() as conn:
                cursor = conn.cursor()
                placeholders = ','.join('?' for _ in marker_ids)
                cursor.execute(
                    f'UPDATE illustrations SET chapter_id = ? '
                    f'WHERE book_id = ? AND marker_id IN ({placeholders})',
                    (chapter_id, book_id, *marker_ids),
                )
                n = cursor.rowcount
            return n
        except Exception as e:
            self.logger.error(f"Error linking illustrations to chapter {chapter_id}: {e}")
            return 0

    _FOOTNOTE_COLS = ['id', 'book_id', 'chapter_id', 'anchor', 'source_term',
                      'body', 'occurrence', 'status', 'is_source', 'sort_order']

    def add_footnote(self, book_id, chapter_id, anchor, body, *, source_term=None,
                     occurrence=1, is_source=0, sort_order=None, status='active'):
        """Upsert a footnote, idempotent on (chapter_id, is_source, anchor, occurrence).

        Returns the row id, or None on error. The body is updated in place when the
        row already exists, so re-running a script with an edited body never
        duplicates.
        """
        try:
            from datetime import datetime
            now = datetime.now().isoformat()
            with self._conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    'SELECT id FROM footnotes WHERE chapter_id = ? AND is_source = ? '
                    'AND anchor = ? AND occurrence = ?',
                    (chapter_id, is_source, anchor, occurrence),
                )
                existing = cursor.fetchone()
                if existing:
                    fid = existing[0]
                    cursor.execute(
                        'UPDATE footnotes SET body = ?, source_term = ?, sort_order = ?, '
                        'status = ?, modified_date = ? WHERE id = ?',
                        (body, source_term, sort_order, status, now, fid),
                    )
                    return fid
                cursor.execute(
                    'INSERT INTO footnotes (book_id, chapter_id, anchor, source_term, body, '
                    'occurrence, status, is_source, sort_order, created_date, modified_date) '
                    'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                    (book_id, chapter_id, anchor, source_term, body, occurrence, status,
                     is_source, sort_order, now, now),
                )
                fid = cursor.lastrowid
            return fid
        except Exception as e:
            self.logger.error(f"Error adding footnote for chapter {chapter_id}: {e}")
            return None

    def get_chapter_footnotes(self, chapter_id, *, is_source=None, status=None):
        """Return a chapter's footnote rows (list of dicts), ordered by id."""
        try:
            with self._conn() as conn:
                cursor = conn.cursor()
                q = ('SELECT id, book_id, chapter_id, anchor, source_term, body, '
                     'occurrence, status, is_source, sort_order FROM footnotes '
                     'WHERE chapter_id = ?')
                params = [chapter_id]
                if is_source is not None:
                    q += ' AND is_source = ?'
                    params.append(is_source)
                if status is not None:
                    q += ' AND status = ?'
                    params.append(status)
                q += ' ORDER BY id'
                cursor.execute(q, tuple(params))
                rows = cursor.fetchall()
            return [dict(zip(self._FOOTNOTE_COLS, r)) for r in rows]
        except Exception as e:
            self.logger.error(f"Error fetching footnotes for chapter {chapter_id}: {e}")
            return []

    def get_book_footnotes(self, book_id, *, status=None):
        """Return a book's footnote rows joined with chapter_number, for reports."""
        try:
            with self._conn() as conn:
                cursor = conn.cursor()
                q = ('SELECT f.id, f.book_id, f.chapter_id, c.chapter_number, f.anchor, '
                     'f.source_term, f.body, f.occurrence, f.status, f.is_source, f.sort_order '
                     'FROM footnotes f JOIN chapters c ON c.id = f.chapter_id '
                     'WHERE f.book_id = ?')
                params = [book_id]
                if status is not None:
                    q += ' AND f.status = ?'
                    params.append(status)
                q += ' ORDER BY c.chapter_number, f.id'
                cursor.execute(q, tuple(params))
                rows = cursor.fetchall()
            cols = ['id', 'book_id', 'chapter_id', 'chapter_number', 'anchor',
                    'source_term', 'body', 'occurrence', 'status', 'is_source', 'sort_order']
            return [dict(zip(cols, r)) for r in rows]
        except Exception as e:
            self.logger.error(f"Error fetching footnotes for book {book_id}: {e}")
            return []

    def update_footnote(self, footnote_id, *, anchor=None, body=None, source_term=None,
                        occurrence=None, sort_order=None):
        """Update mutable fields of a footnote. Returns True if a row changed."""
        sets, params = [], []
        for col, val in (('anchor', anchor), ('body', body), ('source_term', source_term),
                         ('occurrence', occurrence), ('sort_order', sort_order)):
            if val is not None:
                sets.append(f'{col} = ?')
                params.append(val)
        if not sets:
            return False
        try:
            from datetime import datetime
            sets.append('modified_date = ?')
            params.append(datetime.now().isoformat())
            params.append(footnote_id)
            with self._conn() as conn:
                cursor = conn.cursor()
                cursor.execute(f'UPDATE footnotes SET {", ".join(sets)} WHERE id = ?', tuple(params))
                n = cursor.rowcount
            return n > 0
        except Exception as e:
            self.logger.error(f"Error updating footnote {footnote_id}: {e}")
            return False

    def set_footnote_status(self, footnote_id, status):
        """Set a footnote's status ('active' | 'orphaned'). Returns True on success."""
        try:
            from datetime import datetime
            with self._conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    'UPDATE footnotes SET status = ?, modified_date = ? WHERE id = ?',
                    (status, datetime.now().isoformat(), footnote_id),
                )
            return True
        except Exception as e:
            self.logger.error(f"Error setting footnote {footnote_id} status: {e}")
            return False

    def delete_footnote(self, footnote_id):
        """Delete a footnote row. Returns True if a row was removed."""
        try:
            with self._conn() as conn:
                cursor = conn.cursor()
                cursor.execute('DELETE FROM footnotes WHERE id = ?', (footnote_id,))
                n = cursor.rowcount
            return n > 0
        except Exception as e:
            self.logger.error(f"Error deleting footnote {footnote_id}: {e}")
            return False

    def rerender_chapter_footnotes(self, chapter_id):
        """Re-apply persisted footnotes onto a chapter's content (translated and,
        if any, source), flipping each row's status based on whether its anchor was
        found. Writes back with a plain UPDATE — never calls save_chapter, so the
        save-time reapply hook cannot recurse. Deterministic and idempotent.

        Returns True on success (including the no-op case), False on error.
        """
        try:
            from footnotes import render_footnotes, content_to_list
            chapter = self.get_chapter(chapter_id=chapter_id)
            if not chapter:
                return False
            with self._conn() as conn:
                cursor = conn.cursor()
                wrote = False
                for is_source in (0, 1):
                    rows = self.get_chapter_footnotes(chapter_id, is_source=is_source)
                    if not rows:
                        continue
                    key = 'untranslated' if is_source else 'content'
                    column = 'untranslated_content' if is_source else 'translated_content'
                    base = content_to_list(chapter.get(key))
                    new_lines, orphans = render_footnotes(base, rows)
                    orphan_ids = {o['id'] for o in orphans}
                    from datetime import datetime
                    now = datetime.now().isoformat()
                    for r in rows:
                        want = 'orphaned' if r['id'] in orphan_ids else 'active'
                        if r.get('status') != want:
                            cursor.execute(
                                'UPDATE footnotes SET status = ?, modified_date = ? WHERE id = ?',
                                (want, now, r['id']),
                            )
                    cursor.execute(
                        f'UPDATE chapters SET {column} = ? WHERE id = ?',
                        (json.dumps(new_lines, ensure_ascii=False), chapter_id),
                    )
                    wrote = True
            if wrote:
                self.invalidate_epub_cache(chapter['book_id'])
            return True
        except Exception as e:
            self.logger.error(f"Error rerendering footnotes for chapter {chapter_id}: {e}")
            return False
