"""
OutputFormatter module for Translator Application.
Handles conversion of translated content to various output formats.
"""
import os
import re
import glob
import json
import logging
import datetime
import mimetypes
from ebooklib import epub
from typing import Dict, List, Optional, Union, Tuple

from illustrations import parse_marker

# Markdown tag/attr allowlist — kept in parity with the frontend renderer
# (web/frontend/src/lib/chapterMarkdown.js) so the Reader and exported files
# render the same constructs.
_MD_ALLOWED_TAGS = [
    'p', 'br', 'em', 'strong', 'del', 's', 'code', 'pre', 'blockquote', 'hr',
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'ul', 'ol', 'li', 'a',
    'table', 'thead', 'tbody', 'tr', 'th', 'td',
]
_MD_ALLOWED_ATTRS = {'a': ['href', 'title'], 'th': ['align'], 'td': ['align']}

# Block-level Markdown styling for exported EPUB/HTML, mirroring the reader's
# .chapter-markdown rules.
_MD_BLOCK_CSS = '''
    blockquote { border-left: 3px solid #888; margin: 1em 0; padding: 0.25em 0 0.25em 1em; font-style: italic; }
    hr { border: 0; border-top: 1px solid #ccc; width: 40%; margin: 2em auto; }
    ul, ol { margin: 1em 0; padding-left: 1.5em; }
    li { text-indent: 0; }
    code { font-family: monospace; }
    pre { background: #f4f4f4; padding: 0.75em; overflow-x: auto; }
    table { border-collapse: collapse; width: 100%; margin: 1em 0; }
    th, td { border: 1px solid #999; padding: 0.4em 0.6em; text-align: left; }
    .illustration { text-align: center; margin: 1.5em 0; }
    .illustration img { max-width: 100%; }
'''


# Inline formatting sentinels (underline / foreground color), mirroring
# chapterMarkdown.js replaceInlineSentinels: ⟦U⟧…⟦/U⟧ and
# ⟦COLOR:#rrggbb⟧…⟦/COLOR⟧ ride through markdown + bleach as plain text and
# balanced pairs are swapped for real tags AFTER sanitization. Only validated
# hex is interpolated. Markers inside <code>/<pre> stay literal; pairs
# crossing a block boundary stay literal (mismatched tags would break XHTML).
_INLINE_SENTINEL_RE = re.compile(r'⟦(/?)(U|COLOR)(?::(#[0-9a-f]{6}))?⟧')
_BLOCK_TAG_RE = re.compile(
    r'</?(?:p|li|ul|ol|blockquote|h[1-6]|t[dhr]|table|thead|tbody|pre|div)\b', re.I)
_CODE_REGION_RE = re.compile(r'<pre\b.*?</pre>|<code\b.*?</code>', re.S)


def _apply_inline_sentinels(html):
    if not html or '⟦' not in html:
        return html
    code_regions = [(m.start(), m.end()) for m in _CODE_REGION_RE.finditer(html)]

    def in_code(i):
        return any(a <= i < b for a, b in code_regions)

    matches = []
    for m in _INLINE_SENTINEL_RE.finditer(html):
        close = m.group(1) == '/'
        hex_ = m.group(3)
        valid = (not hex_ if close else (not hex_ if m.group(2) == 'U' else bool(hex_)))
        matches.append({
            'start': m.start(), 'end': m.end(), 'close': close,
            'kind': m.group(2), 'hex': hex_,
            'valid': valid and not in_code(m.start()), 'use': False,
        })
    stack = []
    for t in matches:
        if not t['valid']:
            continue
        if not t['close']:
            stack.append(t)
            continue
        if stack and stack[-1]['kind'] == t['kind']:
            top = stack.pop()
            if _BLOCK_TAG_RE.search(html[top['end']:t['start']]):
                continue
            top['use'] = t['use'] = True
        # close without matching open on top: stays literal
    if not any(t['use'] for t in matches):
        return html
    out = []
    last = 0
    for t in matches:
        if not t['use']:
            continue
        out.append(html[last:t['start']])
        if t['close']:
            out.append('</u>' if t['kind'] == 'U' else '</span>')
        else:
            out.append('<u>' if t['kind'] == 'U' else f'<span style="color:{t["hex"]}">')
        last = t['end']
    out.append(html[last:])
    return ''.join(out)


# Rich-table sentinel markers, mirroring chapterMarkdown.js (TABLE_MARKER_RE
# etc.). Whole-line bbcode-style tags with explicit terminators so a cell can
# hold multiple lines / lists / quotes, which pipe tables can't express.
_TABLE_MARKER_RE = re.compile(r'^\s*⟦/?(TABLE|TR|TH|TD)(?::(left|center|right))?⟧\s*$')
_TBL_OPEN_RE = re.compile(r'^\s*⟦TABLE⟧\s*$')
_TR_OPEN_RE = re.compile(r'^\s*⟦TR⟧\s*$')
_CELL_OPEN_RE = re.compile(r'^\s*⟦(TH|TD)(?::(left|center|right))?⟧\s*$')
_CLOSE_RE = re.compile(r'^\s*⟦/(TABLE|TR|TH|TD)⟧\s*$')
_IMG_LINE_RE = re.compile(r'^\s*⟦IMG:[0-9a-f]{4,}⟧\s*$')


def _parse_table_run(lines, start):
    """Parse a sentinel table starting at lines[start] (must be ⟦TABLE⟧).

    Returns ({rows: [{cells: [{header, align, lines}]}]}, next_index) or None
    when malformed — same grammar and failure modes as the JS parseTableRun.
    """
    if start >= len(lines) or not _TBL_OPEN_RE.match(lines[start] or ''):
        return None
    rows = []
    row = None
    cell = None
    i = start + 1
    while i < len(lines):
        line = lines[i] if isinstance(lines[i], str) else None
        if line is None:
            return None
        if cell is not None:
            close = _CLOSE_RE.match(line)
            if close and close.group(1) in ('TH', 'TD'):
                if ('TH' if cell['header'] else 'TD') != close.group(1):
                    return None
                row.append(cell)
                cell = None
            elif _TABLE_MARKER_RE.match(line) or _IMG_LINE_RE.match(line):
                return None
            else:
                cell['lines'].append(line)
        elif row is not None:
            open_m = _CELL_OPEN_RE.match(line)
            close = _CLOSE_RE.match(line)
            if open_m:
                cell = {'header': open_m.group(1) == 'TH',
                        'align': open_m.group(2), 'lines': []}
            elif close and close.group(1) == 'TR':
                if not row:
                    return None
                rows.append({'cells': row})
                row = None
            else:
                return None
        elif _TR_OPEN_RE.match(line):
            row = []
        elif _CLOSE_RE.match(line) and _CLOSE_RE.match(line).group(1) == 'TABLE':
            if row is not None or not rows:
                return None
            return {'rows': rows}, i + 1
        else:
            return None
        i += 1
    return None  # EOF before ⟦/TABLE⟧


def _render_prose_markdown(text, _markdown, _re):
    """The pre-existing prose pipeline: python-markdown + pipe-table fixups."""
    if not text.strip():
        return ""
    html = _markdown.markdown(text, extensions=['extra', 'sane_lists', 'nl2br'])
    # python-markdown emits a spurious all-empty body row for header-only tables
    # (e.g. a single 【…】 notification → `| X |` / `| --- |`). markdown-it (the
    # Reader) omits it, so strip it here to keep EPUB/HTML in parity with the
    # Reader — leaving just the header row.
    html = _re.sub(r"<tr>\s*(?:<td[^>]*>\s*</td>\s*)+</tr>\s*", "", html)
    html = _re.sub(r"<tbody>\s*</tbody>\s*", "", html)
    return html


def _render_table_html(rows, _markdown):
    """Sentinel table → HTML, mirroring chapterMarkdown.js renderTable:
    leading all-header rows in <thead>, <tbody> omitted when empty, align as
    an attribute, cell interiors rendered as full block-level Markdown."""
    def cell_html(cell):
        tag = 'th' if cell['header'] else 'td'
        align = f' align="{cell["align"]}"' if cell['align'] else ''
        inner = _markdown.markdown('\n'.join(cell['lines']),
                                   extensions=['extra', 'sane_lists', 'nl2br'])
        return f'<{tag}{align}>{inner}</{tag}>'

    def row_html(row):
        return '<tr>' + ''.join(cell_html(c) for c in row['cells']) + '</tr>'

    head, body = [], []
    for row in rows:
        if row['cells'] and all(c['header'] for c in row['cells']) and not body:
            head.append(row)
        else:
            body.append(row)
    html = '<table>'
    if head:
        html += '<thead>' + ''.join(row_html(r) for r in head) + '</thead>'
    if body:
        html += '<tbody>' + ''.join(row_html(r) for r in body) + '</tbody>'
    return html + '</table>'


def _render_markdown(text):
    """Render a Markdown document to sanitized HTML.

    Handles sentinel ⟦TABLE⟧ runs (rich tables with block content in cells)
    by rendering them structurally; everything else goes through the prose
    pipeline. Malformed table runs fall through as prose (markers render
    literally — parity with the frontend). Lazily imports markdown + bleach so
    the module never breaks if they're absent; falls back to escaped
    paragraphs in that case.
    """
    if not text or not text.strip():
        return ""
    try:
        import markdown as _markdown
        import bleach as _bleach
    except Exception:
        # Fallback: escape and wrap each blank-line-separated block in <p>.
        import html as _html
        blocks = [b.strip() for b in text.split("\n\n") if b.strip()]
        return "".join(f"<p>{_html.escape(b)}</p>\n" for b in blocks)
    import re as _re
    lines = text.split('\n')
    parts = []
    run = []

    def flush():
        if run:
            parts.append(_render_prose_markdown('\n'.join(run), _markdown, _re))
            run.clear()

    i = 0
    while i < len(lines):
        line = lines[i]
        if _TBL_OPEN_RE.match(line):
            parsed = _parse_table_run(lines, i)
            if parsed:
                table, nxt = parsed
                flush()
                parts.append(_render_table_html(table['rows'], _markdown))
                i = nxt
                continue
        run.append(line)
        i += 1
    flush()
    html = ''.join(parts)
    html = _bleach.clean(html, tags=_MD_ALLOWED_TAGS, attributes=_MD_ALLOWED_ATTRS, strip=True)
    return _apply_inline_sentinels(html)


def render_lines_html(content_lines):
    """Render a stored content line array to sanitized HTML.

    Splits at ⟦IMG:id⟧ marker lines (each kept as a literal <p> placeholder —
    callers with real image URLs handle those themselves) and renders each run
    through _render_markdown, so pipe tables, sentinel ⟦TABLE⟧ tables, lists,
    and inline markdown all come out as real HTML. Shared by WordPress
    publishing and the book HTML export.
    """
    parts = []
    run = []

    def flush():
        if run:
            html = _render_markdown("\n".join(run))
            if html:
                parts.append(html)
            run.clear()

    for line in content_lines or []:
        mid = parse_marker(line)
        if mid:
            flush()
            parts.append(f"<p>⟦IMG:{mid}⟧</p>")
        else:
            run.append(line if isinstance(line, str) else "")
    flush()
    return "\n".join(parts)


class OutputFormatter:
    """
    A class to format translated content into various output formats.
    Supports text, HTML, Markdown, and EPUB output.
    """
    
    def __init__(self, config, logger):
        """
        Initialize the OutputFormatter.
        
        Args:
            config: TranslationConfig object with script_dir and other settings
            logger: Logger object for logging messages
        """
        self.config = config
        self.logger = logger
        self.output_dir = os.path.join(self.config.script_dir, "output")
        
        # Create output directory if it doesn't exist
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)
    
    def save_output(self, translation_result: Dict, format: str = "text", book_info: Dict = None, output_path: str = None) -> str:
        """
        Save the translation result in the specified format.
        
        Args:
            translation_result: Dictionary containing translation data
            format: Output format ('text', 'html', 'markdown', 'epub')
            book_info: Optional dictionary with book metadata for EPUB
            output_path: Optional specific output path
            
        Returns:
            str: Path to the saved output file
        """
        # Get basic information
        title = translation_result.get('title', 'Untitled Chapter')
        chapter = translation_result.get('chapter', 0)
        
        # Handle both 'content' (existing format) and 'translated_content' (archive format)
        if 'content' in translation_result:
            content = translation_result['content']
        else:
            content = []
        
        # Use specified output path or generate one
        if output_path:
            final_output_path = output_path
        else:
            # Generate clean filename from title
            filename_base = self._clean_filename(title)
            
            # Create book-specific directory if book info is provided
            if book_info and 'title' in book_info:
                book_dir = os.path.join(self.output_dir, self._clean_filename(book_info['title']))
                if not os.path.exists(book_dir):
                    os.makedirs(book_dir)
                
                # Format chapter number if available
                if chapter:
                    chapter_prefix = f"chapter_{chapter:03d}_"
                else:
                    chapter_prefix = ""
                
                # Create path with book directory
                final_output_path = os.path.join(book_dir, f"{chapter_prefix}{filename_base}.{format}")
            else:
                # Use regular output directory
                final_output_path = os.path.join(self.output_dir, f"{filename_base}.{format}")
        
        # Process based on format
        if format.lower() == "text":
            return self._save_text(content, title, final_output_path, chapter)
        elif format.lower() == "html":
            return self._save_html(content, title, chapter, final_output_path)
        elif format.lower() == "markdown":
            return self._save_markdown(content, title, chapter, final_output_path)
        elif format.lower() == "epub":
            if not book_info:
                book_info = self.get_book_info()
            return self._save_epub(content, title, chapter, book_info, final_output_path)
        else:
            self.logger.warning(f"Unknown format '{format}', defaulting to text")
            return self._save_text(content, title, final_output_path)
        
    def _register_epub_image(self, book_id, marker_id, used_images):
        """Resolve an illustration file on disk and register it for embedding.

        Files live at illustrations/<book_id>/<marker_id>.<ext>. Returns the
        in-EPUB file name to use as the <img src>, or None if not found.
        """
        if marker_id in used_images:
            return used_images[marker_id]['file_name']
        if book_id is None:
            return None
        pattern = os.path.join(self.config.script_dir, "illustrations", str(book_id), f"{marker_id}.*")
        matches = glob.glob(pattern)
        if not matches:
            self.logger.warning(f"Illustration file not found for marker {marker_id} (book {book_id})")
            return None
        path = matches[0]
        ext = os.path.splitext(path)[1] or '.jpg'
        mime = mimetypes.guess_type(path)[0] or 'image/jpeg'
        file_name = f"images/{marker_id}{ext}"
        used_images[marker_id] = {'file_name': file_name, 'path': path, 'mime': mime}
        return file_name

    def save_book_as_epub(self, all_chapters, book_info, output_path=None):
        """
        Save multiple chapters as a single EPUB file.

        Args:
            all_chapters: List of chapter data dictionaries
            book_info: Dictionary with book metadata
            output_path: Optional explicit target path. When omitted, the
                book title decides the filename under output_dir — note two
                books whose titles clean to the same string share that path,
                so cache-building callers should pass their own target.

        Returns:
            str: Path to the saved EPUB file
        """
        # Extract book metadata
        book_title = book_info.get('title', 'Translated Book')
        book_author = book_info.get('author', 'Translator')
        book_language = book_info.get('language', 'en')
        book_description = book_info.get('description', '')

        if output_path is None:
            book_filename = self._clean_filename(book_title)
            output_path = os.path.join(self.output_dir, f"{book_filename}.epub")

        try:
            # Create a new EPUB book
            book = epub.EpubBook()
            book.set_title(book_title)
            book.set_language(book_language)
            book.add_author(book_author)
            if book_description:
                book.add_metadata('DC', 'description', book_description)

            # Add cover image if available
            cover_file_name = None
            cover_path = book_info.get('cover_image')
            if cover_path and os.path.exists(cover_path):
                import mimetypes
                mime = mimetypes.guess_type(cover_path)[0] or 'image/jpeg'
                with open(cover_path, 'rb') as cf:
                    cover_data = cf.read()
                ext = os.path.splitext(cover_path)[1] or '.jpg'
                cover_file_name = f"cover{ext}"
                book.set_cover(cover_file_name, cover_data, create_page=True)

            # Add default CSS
            default_css = epub.EpubItem(
                uid="style_default",
                file_name="style/default.css",
                media_type="text/css",
                content='''
                    body { font-family: serif; }
                    h1 { text-align: center; margin-bottom: 1em; }
                    p { text-indent: 1.5em; margin-top: 0.5em; margin-bottom: 0.5em; }
                ''' + _MD_BLOCK_CSS
            )
            book.add_item(default_css)
            
            # Create intro/title page
            intro = epub.EpubHtml(title='Introduction', file_name='intro.xhtml', lang=book_language)
            intro.content = f'''
                <html>
                <head>
                    <title>Introduction</title>
                    <link rel="stylesheet" href="style/default.css" type="text/css" />
                </head>
                <body>
                    <h1>{book_title}</h1>
                    {f'<div class="cover-image" style="text-align: center;"><img src="{cover_file_name}" alt="Cover" style="max-width: 100%; height: auto;" /></div>' if cover_file_name else ''}
                    <p>Author: {book_author}</p>
                    <p>Generation date: {datetime.datetime.now().strftime('%Y-%m-%d')}</p>
                    <p>{book_description}</p>
                </body>
                </html>
            '''
            # Attach the stylesheet via ebooklib's API — it regenerates the
            # <head> on serialization and drops any hand-written <link>.
            intro.add_item(default_css)
            book.add_item(intro)
            
            # Add to spine and TOC
            book.spine = ['nav', intro]
            book.toc = [epub.Link('intro.xhtml', 'Introduction', 'intro')]

            # Track in-chapter illustrations to embed (marker_id -> epub file name).
            book_id = book_info.get('id')
            used_images = {}

            # Add each chapter
            for chapter_data in sorted(all_chapters, key=lambda x: x.get('chapter', 0)):
                chapter_number = chapter_data.get('chapter', 0)
                chapter_title = self._display_title(chapter_data.get('title', ''), chapter_number)
                
                # Handle both 'content' and legacy 'translated_content' keys
                if 'content' in chapter_data:
                    content = chapter_data['content']
                elif 'translated_content' in chapter_data:
                    content = chapter_data['translated_content']
                else:
                    content = []
                
                # Convert list of content lines to HTML. Lines are split into
                # runs at illustration markers; each run is rendered as one
                # Markdown document (block-level), and markers become <img>.
                html_content = ""
                run = []

                def _flush():
                    nonlocal html_content, run
                    if run:
                        html_content += _render_markdown("\n".join(run))
                        run = []

                for line in content:
                    marker_id = parse_marker(line)
                    if marker_id:
                        _flush()
                        epub_name = self._register_epub_image(book_id, marker_id, used_images)
                        if epub_name:
                            html_content += f'<div class="illustration"><img src="{epub_name}" alt="" /></div>\n'
                    else:
                        run.append(line)

                _flush()

                # Create chapter
                chapter_id = f"chapter_{chapter_number}"
                chapter_filename = f"chapter_{chapter_number:03d}.xhtml"
                
                # Create EPUB chapter
                epub_chapter = epub.EpubHtml(title=chapter_title, file_name=chapter_filename, lang=book_language)
                epub_chapter.content = f'''
                    <html>
                    <head>
                        <title>{chapter_title}</title>
                        <link rel="stylesheet" href="style/default.css" type="text/css" />
                    </head>
                    <body>
                        <h1>{chapter_title}</h1>
                        {html_content}
                    </body>
                    </html>
                '''
                epub_chapter.add_item(default_css)
                book.add_item(epub_chapter)

                # Add chapter to table of contents and spine
                book.spine.append(epub_chapter)
                book.toc.append(epub.Link(chapter_filename, chapter_title, chapter_id))
            
            # Embed each referenced illustration as an EPUB image resource.
            for marker_id, info in used_images.items():
                try:
                    with open(info['path'], 'rb') as imf:
                        img_bytes = imf.read()
                    book.add_item(epub.EpubItem(
                        uid=f"img_{marker_id}",
                        file_name=info['file_name'],
                        media_type=info['mime'],
                        content=img_bytes,
                    ))
                except Exception as img_err:
                    self.logger.error(f"Failed to embed illustration {marker_id}: {img_err}")

            # Add navigation files
            book.add_item(epub.EpubNcx())
            book.add_item(epub.EpubNav())

            # Create output directory if it doesn't exist
            os.makedirs(os.path.dirname(output_path), exist_ok=True)

            # Write the EPUB file atomically: a concurrent reader (or a build
            # of another book whose title cleans to the same filename) must
            # never see a half-written zip at the final path.
            import threading
            tmp_path = f"{output_path}.tmp-{os.getpid()}-{threading.get_ident()}"
            try:
                epub.write_epub(tmp_path, book, {})
                os.replace(tmp_path, output_path)
            finally:
                if os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass

            self.logger.info(f"Saved all chapters as EPUB to {output_path}")
            return output_path
        except Exception as e:
            self.logger.error(f"Error saving book as EPUB: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return ""

    def _display_title(self, title: str, chapter: Union[int, str] = 0) -> str:
        """Build a display title with 'Chapter N: Title' format."""
        if chapter:
            if title:
                return f"Chapter {chapter}: {title}"
            return f"Chapter {chapter}"
        return title or "Untitled Chapter"

    def _clean_filename(self, title: str) -> str:
        """
        Generate a clean filename from a title.
        
        Args:
            title: Chapter title
            
        Returns:
            str: Cleaned filename
        """
        # Remove invalid characters for filenames
        cleaned = re.sub(r'[\\/*?:"<>|]', "", title)
        # Replace spaces with underscores
        cleaned = cleaned.replace(" ", "_")
        # Limit length
        if len(cleaned) > 50:
            cleaned = cleaned[:50]
        
        return cleaned
    
    @staticmethod
    def _strip_markdown(line: str) -> str:
        """Best-effort plain-text rendering of a Markdown line (for .txt export)."""
        if not line:
            return line
        s = line
        s = re.sub(r'^\s{0,3}#{1,6}\s+', '', s)          # headings
        s = re.sub(r'^\s{0,3}>\s?', '', s)                # blockquote
        s = re.sub(r'^\s*[-*+]\s+', '• ', s)         # bullet list → •
        s = re.sub(r'^\s*\d+\.\s+', '', s)                # ordered list marker
        if re.fullmatch(r'\s*([-*_])\1{2,}\s*', s):       # horizontal rule
            return '* * *'
        s = re.sub(r'\*\*(.+?)\*\*', r'\1', s)            # bold
        s = re.sub(r'(?<!\*)\*(?!\*)(.+?)\*', r'\1', s)   # italic
        s = re.sub(r'`(.+?)`', r'\1', s)                  # inline code
        s = re.sub(r'\[(.+?)\]\((?:[^)]*)\)', r'\1', s)   # links → text
        return s

    def _save_text(self, content: List[str], title: str, output_path: str, chapter: Union[int, str] = 0) -> str:
        """
        Save content as plain text.

        Args:
            content: List of content lines
            title: Chapter title
            output_path: Path to save the file
            chapter: Chapter number

        Returns:
            str: Path to the saved file
        """
        try:
            # Create directory if it doesn't exist
            os.makedirs(os.path.dirname(output_path), exist_ok=True)

            display_title = self._display_title(title, chapter)
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(f"{display_title}\n\n")
                for line in content:
                    f.write(f"{self._strip_markdown(line)}\n")

            self.logger.info(f"Saved text output to {output_path}")
            return output_path
        except Exception as e:
            self.logger.error(f"Error saving text output: {e}")
            return ""
    
    def _save_html(self, content: List[str], title: str, chapter: Union[int, str], output_path: str) -> str:
        """
        Save content as HTML.
        
        Args:
            content: List of content lines
            title: Chapter title
            chapter: Chapter number
            output_path: Path to save the file
            
        Returns:
            str: Path to the saved file
        """
        try:
            # Create directory if it doesn't exist
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write('<!DOCTYPE html>\n')
                f.write('<html lang="en">\n')
                f.write('<head>\n')
                f.write('    <meta charset="UTF-8">\n')
                display_title = self._display_title(title, chapter)
                f.write(f'    <title>{display_title}</title>\n')
                f.write('    <style>\n')
                f.write('        body { font-family: Arial, sans-serif; line-height: 1.6; max-width: 800px; margin: 0 auto; padding: 20px; }\n')
                f.write('        h1 { text-align: center; margin-bottom: 30px; }\n')
                f.write('        p { margin-bottom: 1em; }\n')
                f.write(_MD_BLOCK_CSS)
                f.write('    </style>\n')
                f.write('</head>\n')
                f.write('<body>\n')
                f.write(f'    <h1>{display_title}</h1>\n')

                # Render content as block-level Markdown (illustration markers,
                # which have no embedded image in single-chapter HTML, are dropped).
                text = "\n".join(l for l in content if not parse_marker(l))
                f.write(_render_markdown(text))
                f.write('\n')

                f.write('</body>\n')
                f.write('</html>\n')
            
            self.logger.info(f"Saved HTML output to {output_path}")
            return output_path
        except Exception as e:
            self.logger.error(f"Error saving HTML output: {e}")
            return ""
    
    def _save_markdown(self, content: List[str], title: str, chapter: Union[int, str], output_path: str) -> str:
        """
        Save content as Markdown.
        
        Args:
            filename_base: Base filename
            content: List of content lines
            title: Chapter title
            chapter: Chapter number
            
        Returns:
            str: Path to the saved file
        """
        try:
            # Create directory if it doesn't exist
            os.makedirs(os.path.dirname(output_path), exist_ok=True)

            display_title = self._display_title(title, chapter)
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(f"# {display_title}\n\n")
                
                for line in content:
                    if line.strip() == "":
                        f.write("\n")
                    else:
                        f.write(f"{line}\n\n")
            
            self.logger.info(f"Saved Markdown output to {output_path}")
            return output_path
        except Exception as e:
            self.logger.error(f"Error saving Markdown output: {e}")
            return ""
    
    def _save_epub(self, content: List[str], title: str, chapter: Union[int, str], book_info: Dict = None, output_path: str = None) -> str:
        """
        Save content as EPUB or append to existing EPUB.

        Args:
            content: List of content lines
            title: Chapter title
            chapter: Chapter number
            book_info: Optional dictionary with book metadata
            output_path: Optional output path
            
        Returns:
            str: Path to the saved file
        """
        # Determine book title and filename
        book_title = "Translated Book"
        book_author = "Translator"
        book_language = "en"
        
        if book_info:
            book_title = book_info.get('title', book_title)
            book_author = book_info.get('author', book_author)
            book_language = book_info.get('language', book_language)
        
        # Clean the book title for filename
        if not output_path:
            book_filename = self._clean_filename(book_title)
            output_path = os.path.join(self.output_dir, f"{book_filename}.epub")
        
        # Determine if we're creating a new EPUB or appending to existing
        append_mode = os.path.exists(output_path)
        
        try:
            if append_mode:
                # Load existing book
                book = epub.read_epub(output_path)
                self.logger.info(f"Appending to existing EPUB: {output_path}")
            else:
                # Create new book
                book = epub.EpubBook()
                book.set_title(book_title)
                book.set_language(book_language)
                book.add_author(book_author)
                
                # Add default CSS
                default_css = epub.EpubItem(
                    uid="style_default",
                    file_name="style/default.css",
                    media_type="text/css",
                    content='''
                        body { 
                            font-family: serif;
                            line-height: 1.5;
                        }
                        h1 { 
                            text-align: center;
                            margin-bottom: 1.5em;
                            margin-top: 1em;
                        }
                        p { 
                            text-indent: 1.5em;
                            margin-top: 0.5em;
                            margin-bottom: 0.5em;
                        }
                    '''
                )
                book.add_item(default_css)
                
                # Create intro chapter
                intro = epub.EpubHtml(title='Introduction', file_name='intro.xhtml', lang=book_language)
                intro.content = f'''
                    <html>
                    <head>
                        <title>Introduction</title>
                        <link rel="stylesheet" href="style/default.css" type="text/css" />
                    </head>
                    <body>
                        <h1>Introduction</h1>
                        <p>This book was translated using the {self.config.site_name} Translator tool.</p>
                        <p>Generation date: {datetime.datetime.now().strftime('%Y-%m-%d')}</p>
                    </body>
                    </html>
                '''
                # Attach the stylesheet via ebooklib's API — it regenerates the
                # <head> on serialization and drops any hand-written <link>.
                intro.add_item(default_css)
                book.add_item(intro)
                self.logger.info(f"Creating new EPUB: {output_path}")
            
            # Convert list of content lines to HTML
            html_content = ""
            for line in content:
                if line.strip() == "":
                    html_content += "<p>&nbsp;</p>\n"
                else:
                    # Escape HTML special characters
                    line = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    html_content += f"<p>{line}</p>\n"
            
            # Create chapter
            display_title = self._display_title(title, chapter)
            chapter_id = f"chapter_{chapter}"
            chapter_filename = f"chapter_{chapter}.xhtml"

            # Look up the stylesheet item so we can attach it to chapters via
            # ebooklib's API. In append mode the book was loaded from disk, so
            # `default_css` isn't in scope — fetch it by uid instead. (ebooklib
            # regenerates the <head> on serialization and drops hand-written
            # <link> tags, so the stylesheet must be attached this way.)
            css_item = book.get_item_with_id("style_default")

            # Check if chapter already exists
            chapter_exists = False
            for item in book.get_items():
                if item.file_name == chapter_filename:
                    chapter_exists = True
                    self.logger.warning(f"Chapter {chapter} already exists in EPUB, updating content")
                    item.content = f'''
                        <html>
                        <head>
                            <title>{display_title}</title>
                            <link rel="stylesheet" href="style/default.css" type="text/css" />
                        </head>
                        <body>
                            <h1>{display_title}</h1>
                            {html_content}
                        </body>
                        </html>
                    '''
                    if css_item:
                        item.add_item(css_item)
                    break

            if not chapter_exists:
                # Create new chapter
                epub_chapter = epub.EpubHtml(title=display_title, file_name=chapter_filename, lang=book_language)
                epub_chapter.content = f'''
                    <html>
                    <head>
                        <title>{display_title}</title>
                        <link rel="stylesheet" href="style/default.css" type="text/css" />
                    </head>
                    <body>
                        <h1>{display_title}</h1>
                        {html_content}
                    </body>
                    </html>
                '''
                if css_item:
                    epub_chapter.add_item(css_item)
                book.add_item(epub_chapter)

                # Add chapter to table of contents and spine
                book.spine.append(epub_chapter)
                book.toc.append(epub.Link(chapter_filename, display_title, chapter_id))
            
            # Save the EPUB file
            # Create directory if it doesn't exist
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            
            epub.write_epub(output_path, book, {})
            
            self.logger.info(f"Saved EPUB output to {output_path}")
            return output_path
        except Exception as e:
            self.logger.error(f"Error saving EPUB output: {e}")
            return ""
    
    def get_book_info(self) -> Dict:
        """
        Get or create book information for EPUB output.
        If a book_info.json file exists, load it, otherwise create default info.
        
        Returns:
            dict: Book information dictionary
        """
        book_info_path = os.path.join(self.output_dir, "book_info.json")
        
        if os.path.exists(book_info_path):
            try:
                with open(book_info_path, 'r', encoding='utf-8') as f:
                    book_info = json.load(f)
                    self.logger.info(f"Loaded book info from {book_info_path}")
                    return book_info
            except Exception as e:
                self.logger.error(f"Error loading book info: {e}")
        
        # Create default book info
        book_info = {
            "title": "Translated Book",
            "author": "Translator",
            "language": "en",
            "description": "Book translated using the Translator tool.",
            "created_date": datetime.datetime.now().strftime('%Y-%m-%d')
        }
        
        # Save the default book info
        try:
            with open(book_info_path, 'w', encoding='utf-8') as f:
                json.dump(book_info, f, indent=4, ensure_ascii=False)
            self.logger.info(f"Created default book info at {book_info_path}")
        except Exception as e:
            self.logger.error(f"Error saving default book info: {e}")
        
        return book_info
