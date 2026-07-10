import json
import re
import datetime
import traceback
from modules import apply_source_ingest


class ChaptersRepo:
    """Chapter storage, retrieval, search/replace, and renumbering."""

    @staticmethod
    def _published_filter(prefix=""):
        """SQL predicate (+ its parameter) selecting publicly-visible chapters:
        published_at set and not in the future. NULL = draft, future =
        scheduled — both invisible until their time comes, no cron needed."""
        return (f"{prefix}published_at IS NOT NULL AND {prefix}published_at <= ?",
                datetime.datetime.now().isoformat())

    # Chapter management section
    def save_chapter(self, book_id, chapter_number, title, untranslated_content, translated_content,
                    summary=None, translation_model=None, publish=None):
        """
        Save a chapter to the database.

        Args:
            book_id: Book ID
            chapter_number: Chapter number
            title: Chapter title
            untranslated_content: Original untranslated text (list of lines)
            translated_content: Translated text (list of lines)
            summary: Chapter summary (optional)
            translation_model: Model used for translation (optional)
            publish: Publish state for NEWLY created chapters — True publishes
                immediately, False saves a draft, None (default) publishes
                unless the book is an original work (drafts by default).
                Re-saves of existing chapters never touch publish state.

        Returns:
            int: Chapter ID if successful, None otherwise
        """
        try:
            # Get book info to make sure it exists
            book = self.get_book(book_id=book_id)
            if not book:
                self.logger.error(f"Book with ID {book_id} not found")
                return None

            # Per-book module source transforms (trad→simp, novel543 boilerplate strip, …)
            untranslated_content = apply_source_ingest(book, untranslated_content,
                                                       self.config, self.logger, db=self,
                                                       chapter_number=chapter_number)

            # Serialize content if it's a list
            if isinstance(untranslated_content, list):
                untranslated_text = json.dumps(untranslated_content, ensure_ascii=False)
            else:
                untranslated_text = untranslated_content

            if isinstance(translated_content, list):
                translated_text = json.dumps(translated_content, ensure_ascii=False)
            else:
                translated_text = translated_content
            
            # Current timestamp
            timestamp = datetime.datetime.now().isoformat()
            
            # Get current translation model if not specified
            if translation_model is None:
                translation_model = self.config.translation_model
                
            # Post-commit side effects (cache invalidation, illustration links,
            # footnote re-render) stay OUTSIDE this scope: they must only run
            # once the chapter row is durably committed.
            with self._conn() as conn:
                cursor = conn.cursor()

                # Check if chapter already exists
                cursor.execute('''
                SELECT id FROM chapters
                WHERE book_id = ? AND chapter_number = ?
                ''', (book_id, chapter_number))

                existing = cursor.fetchone()

                if existing:
                    # Update existing chapter
                    chapter_id = existing[0]

                    cursor.execute('''
                    UPDATE chapters
                    SET title = ?, untranslated_content = ?, translated_content = ?,
                        summary = ?, translation_date = ?, translation_model = ?
                    WHERE id = ?
                    ''', (title, untranslated_text, translated_text, summary, timestamp,
                        translation_model, chapter_id))

                    # Update book modified date
                    cursor.execute('''
                    UPDATE books
                    SET modified_date = ?
                    WHERE id = ?
                    ''', (timestamp, book_id))

                    self.logger.info(f"Updated chapter {chapter_number} for book ID {book_id}")
                else:
                    # Insert new chapter. Publish state is decided only here —
                    # updates (retranslation, editor saves) never change it.
                    if publish is None:
                        publish = not book.get("is_original", False)
                    published_at = timestamp if publish else None
                    cursor.execute('''
                    INSERT INTO chapters
                    (book_id, chapter_number, title, untranslated_content, translated_content,
                    summary, translation_date, translation_model, published_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (book_id, chapter_number, title, untranslated_text, translated_text,
                        summary, timestamp, translation_model, published_at))

                    chapter_id = cursor.lastrowid

                    # Update book modified date
                    cursor.execute('''
                    UPDATE books
                    SET modified_date = ?
                    WHERE id = ?
                    ''', (timestamp, book_id))

                    self.logger.info(f"Added chapter {chapter_number} to book ID {book_id}")

            self.invalidate_epub_cache(book_id)

            # Link any illustration rows referenced by this chapter's content
            # (markers in the saved source array) to this chapter_id.
            try:
                from illustrations import markers_in
                if isinstance(untranslated_content, list):
                    src_lines = untranslated_content
                elif isinstance(untranslated_content, str):
                    src_lines = untranslated_content.split('\n')
                else:
                    src_lines = []
                marker_ids = markers_in(src_lines)
                if marker_ids:
                    self.link_illustrations_to_chapter(book_id, chapter_id, marker_ids)
            except Exception as e:
                self.logger.error(f"Illustration linkage skipped for chapter {chapter_number}: {e}")

            # Re-apply persisted footnotes onto the freshly-saved content. A brand-new
            # chapter has no footnote rows, so this is a no-op for initial translation;
            # on retranslation (or any re-save) it re-anchors them and flags orphans.
            try:
                if self.get_chapter_footnotes(chapter_id):
                    self.rerender_chapter_footnotes(chapter_id)
            except Exception as e:
                self.logger.error(f"Footnote reapply skipped for chapter {chapter_number}: {e}")

            return chapter_id

        except Exception as e:
            self.logger.error(f"Error saving chapter: {e}\n{traceback.format_exc()}")
            if self.strict_writes:
                raise
            return None

    def get_chapter(self, chapter_id=None, book_id=None, chapter_number=None, published_only=False):
        """
        Get chapter data from the database.

        Args:
            chapter_id: Chapter ID (optional if book_id and chapter_number are provided)
            book_id: Book ID (required if chapter_id is not provided)
            chapter_number: Chapter number (required if chapter_id is not provided)
            published_only: Only return the chapter if it's publicly visible
                (published_at set and not in the future) — the public API gate

        Returns:
            dict: Chapter data dictionary or None if not found
        """
        if not chapter_id and (not book_id or not chapter_number):
            self.logger.error("Either chapter_id or both book_id and chapter_number must be provided")
            return None

        try:
            pub_clause, pub_param = self._published_filter("c.")
            gate = f" AND {pub_clause}" if published_only else ""
            gate_params = (pub_param,) if published_only else ()
            with self._conn() as conn:
                cursor = conn.cursor()

                if chapter_id:
                    cursor.execute(f'''
                SELECT c.id, c.book_id, c.chapter_number, c.title, c.untranslated_content,
                    c.translated_content, c.summary, c.translation_date, c.translation_model,
                    b.title as book_title, c.is_proofread, c.published_at
                FROM chapters c
                JOIN books b ON c.book_id = b.id
                WHERE c.id = ?{gate}
                ''', (chapter_id,) + gate_params)
                else:
                    cursor.execute(f'''
                SELECT c.id, c.book_id, c.chapter_number, c.title, c.untranslated_content,
                    c.translated_content, c.summary, c.translation_date, c.translation_model,
                    b.title as book_title, c.is_proofread, c.published_at
                FROM chapters c
                JOIN books b ON c.book_id = b.id
                WHERE c.book_id = ? AND c.chapter_number = ?{gate}
                ''', (book_id, chapter_number) + gate_params)

                row = cursor.fetchone()
            
            if not row:
                return None
                
            # Deserialize JSON content
            try:
                untranslated_content = json.loads(row[4])
            except json.JSONDecodeError:
                untranslated_content = row[4].split('\n')
                
            try:
                translated_content = json.loads(row[5])
            except json.JSONDecodeError:
                translated_content = row[5].split('\n')
                
            chapter_data = {
                "id": row[0],
                "book_id": row[1],
                "chapter": row[2],
                "title": row[3],
                "untranslated": untranslated_content,
                "content": translated_content,
                "summary": row[6],
                "translation_date": row[7],
                "model": row[8],
                "book_title": row[9],
                "is_proofread": row[10],
                "published_at": row[11],
            }

            return chapter_data
            
        except Exception as e:
            self.logger.error(f"Error retrieving chapter data: {e}")
            return None

    def get_chapters_bulk(self, book_id, chapter_numbers=None, include_untranslated=False,
                          published_only=False):
        """
        Fetch many chapters in one query (replaces per-chapter get_chapter loops
        in book export, EPUB generation and WordPress publishing).

        Args:
            book_id: Book ID
            chapter_numbers: Iterable of chapter numbers, or None for all chapters
            include_untranslated: Also decode + include the source text
            published_only: Only publicly-visible chapters (public API / EPUB gate)

        Returns:
            list: Chapter dicts shaped like get_chapter() (minus "untranslated"
                  unless requested), ordered by chapter_number. A row whose JSON
                  content is corrupt falls back to newline-splitting like
                  get_chapter(); a row that fails entirely is skipped, not fatal.
        """
        try:
            pub_clause, pub_param = self._published_filter("c.")
            gate = f" AND {pub_clause}" if published_only else ""
            gate_params = [pub_param] if published_only else []
            with self._conn() as conn:
                cursor = conn.cursor()

                src_col = ", c.untranslated_content" if include_untranslated else ""
                base = f'''
            SELECT c.id, c.book_id, c.chapter_number, c.title,
                c.translated_content, c.summary, c.translation_date,
                c.translation_model, b.title as book_title, c.is_proofread,
                c.published_at{src_col}
            FROM chapters c
            JOIN books b ON c.book_id = b.id
            WHERE c.book_id = ?{gate}'''

                rows = []
                if chapter_numbers is None:
                    cursor.execute(base + " ORDER BY c.chapter_number", [book_id] + gate_params)
                    rows = cursor.fetchall()
                else:
                    nums = list(chapter_numbers)
                    # Chunk the IN list to stay well under parameter limits
                    for i in range(0, len(nums), 500):
                        chunk = nums[i:i + 500]
                        placeholders = ",".join("?" * len(chunk))
                        cursor.execute(
                            base + f" AND c.chapter_number IN ({placeholders})"
                            " ORDER BY c.chapter_number",
                            [book_id] + gate_params + chunk)
                        rows.extend(cursor.fetchall())

            def _decode(raw):
                try:
                    return json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    return raw.split('\n') if isinstance(raw, str) else []

            result = []
            for row in rows:
                try:
                    chapter = {
                        "id": row[0],
                        "book_id": row[1],
                        "chapter": row[2],
                        "title": row[3],
                        "content": _decode(row[4]),
                        "summary": row[5],
                        "translation_date": row[6],
                        "model": row[7],
                        "book_title": row[8],
                        "is_proofread": row[9],
                        "published_at": row[10],
                    }
                    if include_untranslated:
                        chapter["untranslated"] = _decode(row[11])
                    result.append(chapter)
                except Exception as e:
                    self.logger.error(f"Skipping corrupt chapter row in bulk fetch: {e}")
            if chapter_numbers is not None:
                result.sort(key=lambda c: c["chapter"])
            return result

        except Exception as e:
            self.logger.error(f"Error bulk-fetching chapters: {e}")
            if self.strict_writes:
                raise
            return []

    def list_chapters(self, book_id, limit=None, offset=0, published_only=False):
        """
        List chapters for a specific book (all of them unless limit is given).

        Args:
            book_id: Book ID
            limit: Optional max rows to return (None = all, legacy behavior)
            offset: Rows to skip when limit is given
            published_only: Only publicly-visible chapters (public API gate)

        Returns:
            list: List of chapter metadata dictionaries
        """
        try:
            # Verify book exists
            book = self.get_book(book_id=book_id)
            if not book:
                self.logger.warning(f"Book with ID {book_id} not found")
                return []
            pub_clause, pub_param = self._published_filter()
            with self._conn() as conn:
                cursor = conn.cursor()

                query = f'''
            SELECT id, chapter_number, title, translation_date, translation_model, is_proofread,
                published_at
            FROM chapters
            WHERE book_id = ?{f" AND {pub_clause}" if published_only else ""}
            ORDER BY chapter_number
            '''
                params = [book_id] + ([pub_param] if published_only else [])
                if limit is not None:
                    query += ' LIMIT ? OFFSET ?'
                    params.extend([int(limit), int(offset)])

                cursor.execute(query, params)

                rows = cursor.fetchall()

            result = []
            for row in rows:
                chapter_id, chapter_number, title, translation_date, model, is_proofread, published_at = row
                result.append({
                    "id": chapter_id,
                    "chapter": chapter_number,
                    "title": title,
                    "translation_date": translation_date,
                    "model": model,
                    "is_proofread": is_proofread,
                    "published_at": published_at,
                })

            return result

        except Exception as e:
            self.logger.error(f"Error listing chapters: {e}")
            return []

    def list_recent_translated_chapters(self, limit=50, book_id=None,
                                        chapter_min=None, chapter_max=None):
        """Return recently translated chapters from public books, joined with book info.

        Ordered by translation_date DESC. If book_id is given, restricted to that book
        (is_public gate still enforced). chapter_min/chapter_max bound chapter_number
        (inclusive) for windowed feeds. translation_date is ISO 8601, so lexicographic
        DESC matches chronological DESC.
        """
        try:
            pub_clause, pub_param = self._published_filter("c.")
            with self._conn() as conn:
                cursor = conn.cursor()
                sql = f'''
                SELECT c.id, c.book_id, c.chapter_number, c.title, c.summary,
                       c.translation_date, b.title AS book_title, b.author AS book_author
                FROM chapters c
                JOIN books b ON c.book_id = b.id
                WHERE b.is_public = 1
                  AND c.translation_date IS NOT NULL
                  AND {pub_clause}
            '''
                params = [pub_param]
                if book_id is not None:
                    sql += ' AND c.book_id = ?'
                    params.append(book_id)
                if chapter_min is not None:
                    sql += ' AND c.chapter_number >= ?'
                    params.append(chapter_min)
                if chapter_max is not None:
                    sql += ' AND c.chapter_number <= ?'
                    params.append(chapter_max)
                sql += ' ORDER BY c.translation_date DESC LIMIT ?'
                params.append(limit)
                cursor.execute(sql, tuple(params))
                rows = cursor.fetchall()
            return [
                {
                    "id": row[0],
                    "book_id": row[1],
                    "chapter": row[2],
                    "title": row[3],
                    "summary": row[4],
                    "translation_date": row[5],
                    "book_title": row[6],
                    "book_author": row[7],
                }
                for row in rows
            ]
        except Exception as e:
            self.logger.error(f"Error listing recent translated chapters: {e}")
            return []

    # Caps to prevent runaway memory use on common-substring queries.
    # match_count stays accurate; only the stored matches list is bounded.
    SEARCH_MAX_MATCHES_PER_CHAPTER = 200

    SEARCH_MAX_TOTAL_MATCHES = 2000

    SEARCH_MAX_TEXT_LEN = 500

    def search_book_chapters(self, book_id, query, scope='both', is_regex=False,
                             published_only=False):
        """Search all chapters of a book for a query string.

        Args:
            book_id: Book ID
            query: Search string or regex pattern
            scope: 'translated', 'untranslated', or 'both'
            is_regex: Whether query is a regex pattern
            published_only: Only search publicly-visible chapters (public API gate)

        Returns:
            list of dicts with chapter_number, title, match_count, matches.
            match_count is the true count; matches may be truncated to bound
            response size (truncated=True is set on the chapter dict in that case).
        """
        try:
            pub_clause, pub_param = self._published_filter()
            gate = f" AND {pub_clause}" if published_only else ""
            gate_params = (pub_param,) if published_only else ()
            with self._conn() as conn:
                cursor = conn.cursor()
                cursor.execute(f'''
                SELECT chapter_number, title, untranslated_content, translated_content
                FROM chapters WHERE book_id = ?{gate} ORDER BY chapter_number
            ''', (book_id,) + gate_params)
                rows = cursor.fetchall()

            if is_regex:
                try:
                    pattern = re.compile(query, re.IGNORECASE)
                except re.error:
                    return []
            else:
                query_lower = query.lower()

            per_ch_cap = self.SEARCH_MAX_MATCHES_PER_CHAPTER
            total_cap = self.SEARCH_MAX_TOTAL_MATCHES
            text_cap = self.SEARCH_MAX_TEXT_LEN

            results = []
            total_collected = 0

            def _clip(line):
                if len(line) <= text_cap:
                    return line
                return line[:text_cap] + '…'

            for chapter_number, title, raw_untrans, raw_trans in rows:
                try:
                    untrans_lines = json.loads(raw_untrans) if raw_untrans else []
                except (json.JSONDecodeError, TypeError):
                    untrans_lines = raw_untrans.split('\n') if raw_untrans else []
                try:
                    trans_lines = json.loads(raw_trans) if raw_trans else []
                except (json.JSONDecodeError, TypeError):
                    trans_lines = raw_trans.split('\n') if raw_trans else []

                matches = []
                ch_match_count = 0

                def add_match(line_idx, col, length, field, line):
                    nonlocal ch_match_count, total_collected
                    ch_match_count += 1
                    if len(matches) >= per_ch_cap or total_collected >= total_cap:
                        return
                    matches.append({
                        'line': line_idx, 'col': col,
                        'length': length, 'field': field,
                        'text': _clip(line),
                    })
                    total_collected += 1

                # Search untranslated
                if scope in ('untranslated', 'both'):
                    for line_idx, line in enumerate(untrans_lines):
                        if is_regex:
                            for m in pattern.finditer(line):
                                add_match(line_idx, m.start(), m.end() - m.start(), 'untranslated', line)
                        else:
                            line_lower = line.lower()
                            start = 0
                            while True:
                                idx = line_lower.find(query_lower, start)
                                if idx == -1:
                                    break
                                add_match(line_idx, idx, len(query), 'untranslated', line)
                                start = idx + 1

                # Search translated
                if scope in ('translated', 'both'):
                    for line_idx, line in enumerate(trans_lines):
                        if is_regex:
                            for m in pattern.finditer(line):
                                add_match(line_idx, m.start(), m.end() - m.start(), 'translated', line)
                        else:
                            line_lower = line.lower()
                            start = 0
                            while True:
                                idx = line_lower.find(query_lower, start)
                                if idx == -1:
                                    break
                                add_match(line_idx, idx, len(query), 'translated', line)
                                start = idx + 1

                if ch_match_count > 0:
                    entry = {
                        'chapter_number': chapter_number,
                        'title': title or f'Chapter {chapter_number}',
                        'match_count': ch_match_count,
                        'matches': matches,
                    }
                    if len(matches) < ch_match_count:
                        entry['truncated'] = True
                    results.append(entry)

            return results
        except Exception as e:
            self.logger.error(f"Error searching chapters: {e}")
            return []

    # In-memory undo snapshot:
    # { book_id: { 'snapshots': [(ch_id, old_content, old_title), ...], 'query': str, 'replacement': str } }
    _replace_undo = {}

    @staticmethod
    def _replace_once(text, query, replacement, pattern):
        """Replace every occurrence in one string. Returns (new_text, count).

        Plain (non-regex) matching is case-INSENSITIVE, matching the search
        endpoint's behaviour. Callers that need case-sensitivity should pass a
        regex, or do their own pass.
        """
        if not text:
            return text, 0
        if pattern is not None:
            return pattern.subn(replacement, text)

        count = 0
        lower_text = text.lower()
        query_lower = query.lower()
        pos = 0
        parts = []
        while True:
            idx = lower_text.find(query_lower, pos)
            if idx == -1:
                parts.append(text[pos:])
                break
            parts.append(text[pos:idx])
            parts.append(replacement)
            count += 1
            pos = idx + len(query)
        return (''.join(parts) if count else text), count

    def replace_in_chapters(self, book_id, query, replacement, chapter_numbers=None,
                            is_regex=False, include_titles=True):
        """Replace text in the translated content of chapters, and in their titles.

        Chapter titles live in their own column, so a replacement that skipped them
        left conventions stale in the title while the prose was clean. `include_titles`
        defaults to True; pass False to touch prose only.

        Saves a snapshot of affected chapters (content AND title) before modifying,
        enabling undo.

        Returns:
            dict with affected_chapters, total_replacements, title_replacements,
            and can_undo flag
        """
        try:
            with self._conn() as conn:
                cursor = conn.cursor()

                sql = 'SELECT id, chapter_number, translated_content, title FROM chapters WHERE book_id = ?'
                params = [book_id]
                if chapter_numbers:
                    placeholders = ','.join('?' * len(chapter_numbers))
                    sql += f' AND chapter_number IN ({placeholders})'
                    params.extend(chapter_numbers)
                sql += ' ORDER BY chapter_number'

                cursor.execute(sql, params)
                rows = cursor.fetchall()

                pattern = None
                if is_regex:
                    try:
                        pattern = re.compile(query, re.IGNORECASE)
                    except re.error:
                        return {'affected_chapters': 0, 'total_replacements': 0,
                                'title_replacements': 0, 'can_undo': False}

                affected = 0
                total = 0
                title_total = 0
                snapshots = []

                for ch_id, ch_num, raw_content, raw_title in rows:
                    try:
                        lines = json.loads(raw_content) if raw_content else []
                    except (json.JSONDecodeError, TypeError):
                        lines = raw_content.split('\n') if raw_content else []

                    ch_replacements = 0
                    new_lines = []
                    for line in lines:
                        new_line, count = self._replace_once(line, query, replacement, pattern)
                        new_lines.append(new_line)
                        ch_replacements += count

                    new_title, title_count = (
                        self._replace_once(raw_title, query, replacement, pattern)
                        if include_titles else (raw_title, 0)
                    )

                    if ch_replacements > 0 or title_count > 0:
                        # Snapshot content AND title before overwriting
                        snapshots.append((ch_id, raw_content, raw_title))
                        cursor.execute(
                            'UPDATE chapters SET translated_content = ?, title = ? WHERE id = ?',
                            (json.dumps(new_lines, ensure_ascii=False), new_title, ch_id)
                        )
                        affected += 1
                        total += ch_replacements
                        title_total += title_count

            if affected > 0:
                self.invalidate_epub_cache(book_id)

            # Store undo snapshot (one level, keyed by book)
            if snapshots:
                ChaptersRepo._replace_undo[book_id] = {
                    'snapshots': snapshots,
                    'query': query,
                    'replacement': replacement,
                    'affected_chapters': affected,
                    'total_replacements': total,
                    'title_replacements': title_total,
                }

            return {'affected_chapters': affected, 'total_replacements': total,
                    'title_replacements': title_total, 'can_undo': len(snapshots) > 0}

        except Exception as e:
            self.logger.error(f"Error replacing in chapters: {e}\n{traceback.format_exc()}")
            if self.strict_writes:
                raise
            return {'affected_chapters': 0, 'total_replacements': 0,
                    'title_replacements': 0, 'can_undo': False}

    def undo_replace(self, book_id):
        """Undo the last replace_in_chapters operation for a book.

        Returns:
            dict with status and number of chapters restored, or None if nothing to undo
        """
        undo = ChaptersRepo._replace_undo.pop(book_id, None)
        if not undo:
            return None

        try:
            with self._conn() as conn:
                cursor = conn.cursor()
                for snap in undo['snapshots']:
                    # Snapshots are (ch_id, old_content, old_title); tolerate the
                    # older 2-tuple shape in case one is still in memory.
                    if len(snap) == 3:
                        ch_id, old_content, old_title = snap
                        cursor.execute(
                            'UPDATE chapters SET translated_content = ?, title = ? WHERE id = ?',
                            (old_content, old_title, ch_id)
                        )
                    else:
                        ch_id, old_content = snap
                        cursor.execute(
                            'UPDATE chapters SET translated_content = ? WHERE id = ?',
                            (old_content, ch_id)
                        )
            self.invalidate_epub_cache(book_id)
            return {'restored_chapters': len(undo['snapshots'])}
        except Exception as e:
            self.logger.error(f"Error undoing replace: {e}")
            return None

    def has_replace_undo(self, book_id):
        """Check if an undo snapshot exists for a book."""
        return book_id in ChaptersRepo._replace_undo

    def delete_chapter(self, chapter_id=None, book_id=None, chapter_number=None):
        """
        Delete a chapter from the database.
        
        Args:
            chapter_id: Chapter ID (optional if book_id and chapter_number are provided)
            book_id: Book ID (required if chapter_id is not provided)
            chapter_number: Chapter number (required if chapter_id is not provided)
            
        Returns:
            bool: True if successful, False otherwise
        """
        if not chapter_id and (not book_id or not chapter_number):
            self.logger.error("Either chapter_id or both book_id and chapter_number must be provided")
            return False
            
        try:
            with self._conn() as conn:
                cursor = conn.cursor()
                
                # Get chapter details first (for logging)
                if chapter_id:
                    cursor.execute('''
                SELECT book_id, chapter_number, title FROM chapters WHERE id = ?
                ''', (chapter_id,))
                else:
                    cursor.execute('''
                SELECT id, title FROM chapters WHERE book_id = ? AND chapter_number = ?
                ''', (book_id, chapter_number))
                    
                chapter = cursor.fetchone()
                
                if not chapter:
                    self.logger.warning("Chapter not found")
                    return False
                    
                # Delete the chapter
                if chapter_id:
                    cursor.execute("DELETE FROM chapters WHERE id = ?", (chapter_id,))
                else:
                    cursor.execute('''
                DELETE FROM chapters WHERE book_id = ? AND chapter_number = ?
                ''', (book_id, chapter_number))
                
                # Update book modified date
                timestamp = datetime.datetime.now().isoformat()
                
                if chapter_id:
                    book_id = chapter[0]
                
                cursor.execute('''
            UPDATE books
            SET modified_date = ?
            WHERE id = ?
            ''', (timestamp, book_id))
            self.invalidate_epub_cache(book_id)

            if chapter_id:
                self.logger.info(f"Deleted chapter {chapter[1]}: '{chapter[2]}' from book ID {chapter[0]}")
            else:
                self.logger.info(f"Deleted chapter {chapter_number} (ID: {chapter[0]}): '{chapter[1]}' from book ID {book_id}")

            return True

        except Exception as e:
            self.logger.error(f"Error deleting chapter: {e}\n{traceback.format_exc()}")
            if self.strict_writes:
                raise
            return False

    def renumber_chapter(self, book_id, chapter_number, new_chapter_number):
        """
        Move a chapter to a new chapter_number, updating all related tables.

        Returns:
            (True, None) on success, (False, reason) on failure. Reasons:
              - "not_found": source chapter doesn't exist
              - "target_exists": target chapter_number already in use
              - "invalid": new_chapter_number invalid (<1)
        """
        if not isinstance(new_chapter_number, int) or new_chapter_number < 1:
            return False, "invalid"
        if new_chapter_number == chapter_number:
            return True, None

        try:
            with self._conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT id FROM chapters WHERE book_id = ? AND chapter_number = ?",
                    (book_id, chapter_number),
                )
                if not cursor.fetchone():
                    return False, "not_found"

                cursor.execute(
                    "SELECT id FROM chapters WHERE book_id = ? AND chapter_number = ?",
                    (book_id, new_chapter_number),
                )
                if cursor.fetchone():
                    return False, "target_exists"

                cursor.execute(
                    "UPDATE chapters SET chapter_number = ? WHERE book_id = ? AND chapter_number = ?",
                    (new_chapter_number, book_id, chapter_number),
                )
                cursor.execute(
                    "UPDATE wp_publish_state SET chapter_number = ? WHERE book_id = ? AND chapter_number = ?",
                    (new_chapter_number, book_id, chapter_number),
                )
                cursor.execute(
                    "UPDATE comments SET chapter_number = ? WHERE book_id = ? AND chapter_number = ?",
                    (new_chapter_number, book_id, chapter_number),
                )
                cursor.execute(
                    "UPDATE api_calls SET chapter_number = ? WHERE book_id = ? AND chapter_number = ?",
                    (new_chapter_number, book_id, chapter_number),
                )
                cursor.execute(
                    "UPDATE reader_log SET chapter_number = ? WHERE book_id = ? AND chapter_number = ?",
                    (new_chapter_number, book_id, chapter_number),
                )
                # activity_log uses column name `chapter`, not `chapter_number`
                cursor.execute(
                    "UPDATE activity_log SET chapter = ? WHERE book_id = ? AND chapter = ?",
                    (new_chapter_number, book_id, chapter_number),
                )
        except Exception as e:
            self.logger.error(f"Error renumbering chapter {chapter_number} -> {new_chapter_number}: {e}\n{traceback.format_exc()}")
            if self.strict_writes:
                raise
            return False, str(e)
        self.invalidate_epub_cache(book_id)
        self.logger.info(f"Renumbered chapter {chapter_number} -> {new_chapter_number} for book ID {book_id}")
        return True, None

    # ------------------------------------------------------------------
    # Proofread flag (added in B4 for the web layer; additive only)
    # ------------------------------------------------------------------

    def _proofread_timestamp(self, is_proofread):
        """Timestamp to store in chapters.is_proofread — owns the MySQL
        (DATETIME literal) vs SQLite (ISO-8601 Z suffix) format branch."""
        if not is_proofread:
            return None
        fmt = '%Y-%m-%d %H:%M:%S' if self.backend.name == 'mysql' else '%Y-%m-%dT%H:%M:%SZ'
        return datetime.datetime.utcnow().strftime(fmt)

    def set_chapter_proofread(self, book_id, chapter_number, is_proofread):
        """Set (True) or clear (False) a chapter's proofread timestamp.

        Returns the stored timestamp string, or None when clearing.
        Raises LookupError when the chapter doesn't exist.
        """
        now = self._proofread_timestamp(is_proofread)
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE chapters SET is_proofread = ? WHERE book_id = ? AND chapter_number = ?",
                (now, book_id, chapter_number),
            )
            if cursor.rowcount == 0:
                raise LookupError(
                    f"Chapter {chapter_number} not found for book {book_id}")
        return now

    def set_chapters_proofread(self, book_id, chapter_numbers, is_proofread):
        """Bulk variant of set_chapter_proofread (single transaction).

        Chapters that don't exist contribute 0 to the count (no error).
        Returns (updated_count, timestamp_or_None).
        """
        now = self._proofread_timestamp(is_proofread)
        updated = 0
        with self._conn() as conn:
            cursor = conn.cursor()
            for num in chapter_numbers:
                cursor.execute(
                    "UPDATE chapters SET is_proofread = ? WHERE book_id = ? AND chapter_number = ?",
                    (now, book_id, num),
                )
                updated += cursor.rowcount
        return updated, now

    # ------------------------------------------------------------------
    # Publishing (published_at: NULL = draft, future = scheduled, past = live)
    # ------------------------------------------------------------------

    def _post_publish_change(self, book_id):
        """Publish-state changes alter the public artifact set: bump the book's
        modified_date (feeds the Spaces EPUB version key) and drop the cached
        EPUB so the next download regenerates with the new chapter set."""
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE books SET modified_date = ? WHERE id = ?",
                           (datetime.datetime.now().isoformat(), book_id))
        self.invalidate_epub_cache(book_id)

    def set_chapter_published(self, book_id, chapter_number, published_at):
        """Set (ISO timestamp — now or scheduled) or clear (None = back to
        draft) a chapter's publish time. Returns the stored value.
        Raises LookupError when the chapter doesn't exist."""
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE chapters SET published_at = ? WHERE book_id = ? AND chapter_number = ?",
                (published_at, book_id, chapter_number),
            )
            if cursor.rowcount == 0:
                raise LookupError(
                    f"Chapter {chapter_number} not found for book {book_id}")
        self._post_publish_change(book_id)
        return published_at

    def set_chapters_published(self, book_id, schedule):
        """Bulk publish/schedule: `schedule` is [(chapter_number, published_at_or_None), ...]
        (staggered drip-release sets a different time per chapter). Chapters
        that don't exist contribute 0 to the count. Returns updated_count."""
        updated = 0
        with self._conn() as conn:
            cursor = conn.cursor()
            for num, published_at in schedule:
                cursor.execute(
                    "UPDATE chapters SET published_at = ? WHERE book_id = ? AND chapter_number = ?",
                    (published_at, book_id, num),
                )
                updated += cursor.rowcount
        if updated:
            self._post_publish_change(book_id)
        return updated

    def latest_published_at(self, book_id):
        """Most recent publish time that has already passed, or None.
        Used to detect when a scheduled chapter has crossed its publish time
        so cached public artifacts (EPUB) regenerate."""
        try:
            pub_clause, pub_param = self._published_filter()
            with self._conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    f"SELECT MAX(published_at) FROM chapters WHERE book_id = ? AND {pub_clause}",
                    (book_id, pub_param))
                row = cursor.fetchone()
            return row[0] if row else None
        except Exception as e:
            self.logger.error(f"Error getting latest published time: {e}")
            return None
