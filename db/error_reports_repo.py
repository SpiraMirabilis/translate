import datetime


class ErrorReportsRepo:
    """Reader-submitted translation error reports (error_reports table).

    A report is anchored to a book and, optionally, to one chapter
    (chapter_number NULL means "book-wide issue"). The optional `quote` is the
    passage the reader highlighted; the admin queue turns it into a Chapter
    Editor deep link, which is the whole point of collecting it.
    """

    def create_error_report(self, data: dict) -> int:
        """Insert a new error report and return its id."""
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'INSERT INTO error_reports (book_id, chapter_number, report_type, quote, '
                'problem, suggested_fix, reporter_email, status, created_at, ip, user_agent) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (
                    data['book_id'],
                    data.get('chapter_number'),
                    data['report_type'],
                    data.get('quote'),
                    data['problem'],
                    data.get('suggested_fix'),
                    data.get('reporter_email'),
                    'new',
                    datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
                    data.get('ip'),
                    data.get('user_agent'),
                ),
            )
            report_id = cursor.lastrowid
        return report_id

    def list_error_reports(self, status: str = None, book_id: int = None) -> list:
        """List error reports (newest first), optionally filtered.

        Joins books so the admin queue can show a title without a second
        round-trip per row. The join is LEFT so a report whose book was deleted
        outside the FK cascade still lists rather than vanishing.
        """
        select = ('SELECT e.*, b.title AS book_title FROM error_reports e '
                  'LEFT JOIN books b ON b.id = e.book_id')
        where, vals = [], []
        if status:
            where.append('e.status = ?')
            vals.append(status)
        if book_id is not None:
            where.append('e.book_id = ?')
            vals.append(book_id)
        sql = select
        if where:
            sql += ' WHERE ' + ' AND '.join(where)
        sql += ' ORDER BY e.id DESC'
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, vals)
            cols = [d[0] for d in cursor.description]
            rows = [dict(zip(cols, r)) for r in cursor.fetchall()]
        return rows

    def get_error_report(self, report_id: int):
        """Fetch a single error report by id."""
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT e.*, b.title AS book_title FROM error_reports e '
                'LEFT JOIN books b ON b.id = e.book_id WHERE e.id = ?',
                (report_id,))
            row = cursor.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cursor.description]
        return dict(zip(cols, row))

    def update_error_report(self, report_id: int, updates: dict):
        """Update fields on an error report."""
        allowed = {'status', 'admin_notes', 'reviewed_at'}
        parts, vals = [], []
        for k, v in updates.items():
            if k in allowed:
                parts.append(f'{k} = ?')
                vals.append(v)
        if not parts:
            return
        vals.append(report_id)
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(f'UPDATE error_reports SET {", ".join(parts)} WHERE id = ?', vals)

    def delete_error_report(self, report_id: int):
        """Delete an error report."""
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM error_reports WHERE id = ?', (report_id,))

    def count_error_reports(self, status: str = None) -> int:
        """Count error reports, optionally filtered by status."""
        with self._conn() as conn:
            cursor = conn.cursor()
            if status:
                cursor.execute('SELECT COUNT(*) FROM error_reports WHERE status = ?', (status,))
            else:
                cursor.execute('SELECT COUNT(*) FROM error_reports')
            count = cursor.fetchone()[0]
        return count
