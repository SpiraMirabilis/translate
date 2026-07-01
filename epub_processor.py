"""
EPUB Processing Module for Translator Application.
Extracts chapters from EPUB files and adds them to the translation queue.
"""
import os
import re
import json
import logging
import posixpath
from urllib.parse import unquote, urldefrag
from bs4 import BeautifulSoup
from ebooklib import epub
import html2text
from illustrations import IllustrationCollector, make_marker, store_chapter_illustrations, MARKER_RE


# --- Chapter-number parsing -------------------------------------------------

_CN_DIGITS = {
    '零': 0, '〇': 0, '一': 1, '壹': 1, '二': 2, '贰': 2, '貳': 2,
    '两': 2, '兩': 2, '三': 3, '叁': 3, '參': 3, '四': 4, '肆': 4,
    '五': 5, '伍': 5, '六': 6, '陆': 6, '陸': 6, '七': 7, '柒': 7,
    '八': 8, '捌': 8, '九': 9, '玖': 9,
}
_CN_UNITS = {'十': 10, '拾': 10, '百': 100, '佰': 100, '千': 1000, '仟': 1000}
_CN_BIG = {'万': 10000, '萬': 10000, '亿': 100000000, '億': 100000000}

_CHAPTER_NUM_RE = re.compile(
    r'第\s*([0-9零〇一二三四五六七八九十百千万亿'
    r'壹贰貳两兩叁參肆伍陆陸柒捌玖拾佰仟萬億]+)\s*[章节節篇回卷]'
)


def parse_chinese_numeral(text):
    """Convert a Chinese-numeral string (e.g. '三百三十七') to an int.

    Handles standard place-value forms with 十/百/千 and the large-group
    markers 万/亿, including leading-ten shorthand ('十二' -> 12) and the
    financial/variant character set. Returns None if `text` contains any
    character that is not a recognized numeral.
    """
    if not text:
        return None

    total = 0     # accumulates completed 万/亿 groups
    section = 0   # value of the current group below 万
    current = 0   # pending digit not yet attached to a unit
    seen = False

    for ch in text:
        if ch in _CN_DIGITS:
            current = _CN_DIGITS[ch]
            seen = True
        elif ch in _CN_UNITS:
            section += (current if current else 1) * _CN_UNITS[ch]
            current = 0
            seen = True
        elif ch in _CN_BIG:
            section += current
            total += (section if section else 1) * _CN_BIG[ch]
            section = 0
            current = 0
            seen = True
        else:
            return None

    if not seen:
        return None
    return total + section + current


def chapter_number_from_title(title, default=None):
    """Extract a chapter number from a chapter title.

    Recognizes both Arabic ('Chapter 12', '第12章') and Chinese-numeral
    ('第三百三十七章') forms. An explicit 第…章/节/篇/回/卷 marker is preferred;
    otherwise the first Arabic run anywhere in the title is used. Returns
    `default` when nothing parseable is found.
    """
    if not title:
        return default

    match = _CHAPTER_NUM_RE.search(title)
    if match:
        token = match.group(1)
        if token.isdigit():
            return int(token)
        value = parse_chinese_numeral(token)
        if value is not None:
            return value

    match = re.search(r'(\d+)', title)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            pass

    return default


class EPUBProcessor:
    """
    A class to process EPUB files, extract chapters, and add them to the translation queue.
    """
    
    def __init__(self, config, logger, db_manager):
        """
        Initialize the EPUB processor.

        Args:
            config: TranslationConfig object with script_dir and other settings
            logger: Logger object for logging messages
            db_manager: DatabaseManager instance for queue operations
        """
        self.config = config
        self.logger = logger
        self.db_manager = db_manager
        # Per-run illustration state; populated by process_epub before extraction.
        self._collector = None
        self._epub_book = None
        self.h2t = html2text.HTML2Text()
        self.h2t.ignore_links = True
        self.h2t.ignore_images = True
        self.h2t.ignore_tables = False
        self.h2t.single_line_break = True
        self.h2t.body_width = 0  # No wrapping
    
    def load_epub(self, epub_path):
        """
        Load an EPUB file and return the book object.
        
        Args:
            epub_path: Path to the EPUB file
            
        Returns:
            epub.EpubBook: The loaded book object, or None if loading failed
        """
        try:
            book = epub.read_epub(epub_path)
            self.logger.info(f"Successfully loaded EPUB: {os.path.basename(epub_path)}")
            return book
        except Exception as e:
            self.logger.error(f"Failed to load EPUB {epub_path}: {e}")
            return None

    def get_epub_metadata(self, epub_path):
        """
        Extract metadata from an EPUB file.

        Args:
            epub_path: Path to the EPUB file

        Returns:
            dict: Dictionary containing title, author, and other metadata
        """
        try:
            book = self.load_epub(epub_path)
            if not book:
                return {'title': None, 'author': None}

            metadata = {}

            # Extract title
            title = book.get_metadata('DC', 'title')
            metadata['title'] = title[0][0] if title else None

            # Extract author
            author = book.get_metadata('DC', 'creator')
            metadata['author'] = author[0][0] if author else None

            # Extract language
            language = book.get_metadata('DC', 'language')
            metadata['language'] = language[0][0] if language else None

            # Extract publisher
            publisher = book.get_metadata('DC', 'publisher')
            metadata['publisher'] = publisher[0][0] if publisher else None

            self.logger.info(f"Extracted metadata: title='{metadata['title']}', author='{metadata['author']}'")
            return metadata

        except Exception as e:
            self.logger.error(f"Error extracting EPUB metadata: {e}")
            return {'title': None, 'author': None}

    def extract_toc(self, book):
        """
        Extract table of contents from the book.
        
        Args:
            book: epub.EpubBook object
            
        Returns:
            list: List of (title, href) tuples representing the TOC
        """
        toc = []
        for item in book.toc:
            if isinstance(item, tuple) and len(item) > 1:
                # For books with nested TOC
                section_title, subitems = item[0], item[1]
                if hasattr(section_title, 'title') and hasattr(section_title, 'href'):
                    toc.append((section_title.title, section_title.href))
                for subitem in subitems:
                    if hasattr(subitem, 'title') and hasattr(subitem, 'href'):
                        toc.append((subitem.title, subitem.href))
            elif hasattr(item, 'title') and hasattr(item, 'href'):
                toc.append((item.title, item.href))
        
        self.logger.info(f"Extracted {len(toc)} TOC entries")
        return toc
    
    def extract_chapters(self, book, toc=None):
        """
        Extract chapters from the book, using TOC if available.
        
        Args:
            book: epub.EpubBook object
            toc: Optional list of (title, href) tuples
            
        Returns:
            list: List of dicts with chapter info {title, content, number}
        """
        chapters = []
        chapter_regex = re.compile(r'chapter|第.{1,3}[章节篇回]|卷', re.IGNORECASE)
        
        # Check if we should use TOC or process all items
        if toc:
            # Use TOC to extract chapters
            for i, (title, href) in enumerate(toc, 1):
                # Find the item by href
                item = book.get_item_with_href(href)
                if not item:
                    self.logger.warning(f"Could not find item with href: {href}")
                    continue
                
                # Get content
                content = self._process_html_content(item.content, base_href=href)

                # Skip empty chapters or very short ones (likely just titles),
                # but keep image-only chapters.
                if not content or (len(content) < 50 and "⟦IMG:" not in content):
                    continue

                # Extract chapter number from title (Arabic or Chinese numerals)
                chapter_number = chapter_number_from_title(title, default=i)

                chapters.append({
                    'title': title,
                    'content': content,
                    'number': chapter_number
                })
        else:
            # Process all items as potential chapters
            items = [item for item in book.get_items() if item.get_type() == epub.ITEM_DOCUMENT]
            
            # Sort items by file name (often contains ordering information)
            items.sort(key=lambda x: x.file_name)
            
            for i, item in enumerate(items, 1):
                content = self._process_html_content(item.content, base_href=item.file_name)
                
                # Skip empty or very short content
                if not content or len(content) < 50:
                    continue
                
                # Try to extract title and chapter number from content
                title = self._extract_title_from_content(content)

                # If title found, extract chapter number (Arabic or Chinese numerals)
                if title:
                    chapter_number = chapter_number_from_title(title, default=i)
                else:
                    title = f"Chapter {i}"
                    chapter_number = i
                
                # Only treat as chapter if it resembles one
                if len(content.split('\n')) > 5 and (
                    chapter_regex.search(title) or 
                    chapter_regex.search(content[:200])
                ):
                    chapters.append({
                        'title': title,
                        'content': content,
                        'number': chapter_number
                    })
        
        # Sort chapters by number
        chapters.sort(key=lambda x: x['number'])
        
        self.logger.info(f"Extracted {len(chapters)} chapters")
        return chapters
    
    def _process_html_content(self, html_content, base_href=None):
        """
        Process HTML content to extract clean text.

        When an illustration collector is active, embedded <img>/<svg image>
        elements are resolved against the EPUB manifest, stored, and replaced
        by an inline ⟦IMG:id⟧ marker (later isolated onto its own line). With no
        collector, behaviour is unchanged (html2text drops images).

        Args:
            html_content: Raw HTML content
            base_href: The chapter item's href, used to resolve relative image
                src paths against the manifest.

        Returns:
            str: Cleaned text content
        """
        if isinstance(html_content, bytes):
            html_content = html_content.decode('utf-8', errors='replace')

        # Substitute image elements with markers before html2text runs.
        if self._collector is not None and self._epub_book is not None:
            html_content = self._substitute_images(html_content, base_href)

        # Convert HTML to text
        text = self.h2t.handle(html_content)

        # Ensure every marker sits alone on its own line (html2text may have
        # reflowed an inline marker into surrounding text).
        text = re.sub(r'[ \t]*(⟦IMG:[0-9a-f]+⟧)[ \t]*', r'\n\1\n', text)

        # Clean up the text
        text = re.sub(r'\n{3,}', '\n\n', text)  # Replace multiple newlines
        text = text.strip()

        return text

    def _substitute_images(self, html_content, base_href):
        """Replace <img>/<svg image> elements with ⟦IMG:id⟧ markers in order.

        Decorative/tiny or unresolvable images are removed. Returns the modified
        HTML string for html2text to process.
        """
        try:
            soup = BeautifulSoup(html_content, 'html.parser')
        except Exception:
            return html_content

        for el in soup.find_all(['img', 'image']):
            src = (el.get('src') or el.get('xlink:href') or el.get('href') or '').strip()
            data, mime = self._resolve_epub_image(src, base_href)
            marker_id = None
            if data:
                marker_id = self._collector.add(
                    data, mime=mime, alt=(el.get('alt') or None), original_href=src,
                )
            if marker_id:
                el.replace_with(f" {make_marker(marker_id)} ")
            else:
                el.decompose()  # decorative / unresolved → drop cleanly

        return str(soup)

    def _resolve_epub_image(self, src, base_href):
        """Resolve an image src (relative to base_href) to (bytes, mime)."""
        if not src or src.startswith('data:'):
            return None, None
        # Strip fragment/query and URL-decode.
        src = unquote(urldefrag(src)[0])
        candidates = [src]
        # Resolve relative to the chapter's directory.
        if base_href:
            base_dir = posixpath.dirname(unquote(urldefrag(base_href)[0]))
            if base_dir:
                candidates.append(posixpath.normpath(posixpath.join(base_dir, src)))
        candidates.append(posixpath.basename(src))

        for cand in candidates:
            item = self._epub_book.get_item_with_href(cand)
            if item is not None:
                try:
                    return item.get_content(), (item.media_type or '')
                except Exception:
                    return None, None
        self.logger.warning(f"Could not resolve EPUB image href: {src}")
        return None, None
    
    def _extract_title_from_content(self, content):
        """
        Try to extract chapter title from content.
        
        Args:
            content: Chapter content
            
        Returns:
            str: Extracted title or None
        """
        # Look for common chapter title patterns
        patterns = [
            r'^(Chapter \d+.{0,50})\n',
            r'^(第.{1,3}[章节篇回].{0,50})\n',
            r'^(\d+\s*[\.、]\s*.{1,50})\n'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, content)
            if match:
                return match.group(1).strip()
        
        # Try first non-empty line if it's short
        lines = content.split('\n')
        for line in lines:
            line = line.strip()
            if line and len(line) < 100:  # Reasonable title length
                return line
        
        return None
    
    def extract_cover_image(self, book):
        """
        Extract cover image from an EPUB book object.

        Args:
            book: epub.EpubBook object

        Returns:
            tuple: (image_bytes, extension) or (None, None)
        """
        ext_map = {
            "image/jpeg": ".jpg", "image/png": ".png",
            "image/gif": ".gif", "image/webp": ".webp",
            "image/svg+xml": ".svg",
        }

        # Strategy 1: OPF cover metadata -> item by ID
        cover_meta = book.get_metadata('OPF', 'cover')
        if cover_meta:
            cover_id = cover_meta[0][1].get('content', '') if len(cover_meta[0]) > 1 else cover_meta[0][0]
            if cover_id:
                for item in book.get_items():
                    if item.get_id() == cover_id:
                        mt = item.media_type or ''
                        if mt.startswith('image/'):
                            ext = ext_map.get(mt, '.jpg')
                            self.logger.info(f"Found cover via OPF metadata: {item.file_name}")
                            return item.get_content(), ext

        # Strategy 2: item with properties="cover-image"
        for item in book.get_items():
            props = getattr(item, 'properties', None) or []
            if isinstance(props, str):
                props = props.split()
            if 'cover-image' in props:
                mt = item.media_type or ''
                ext = ext_map.get(mt, '.jpg')
                self.logger.info(f"Found cover via cover-image property: {item.file_name}")
                return item.get_content(), ext

        # Strategy 3: item with "cover" in ID and is an image
        for item in book.get_items():
            item_id = (item.get_id() or '').lower()
            fname = (item.file_name or '').lower()
            mt = item.media_type or ''
            if mt.startswith('image/') and ('cover' in item_id or 'cover' in fname):
                ext = ext_map.get(mt, '.jpg')
                self.logger.info(f"Found cover by name heuristic: {item.file_name}")
                return item.get_content(), ext

        self.logger.info("No cover image found in EPUB")
        return None, None

    def save_cover_image(self, image_bytes, extension, book_id):
        """
        Save extracted cover image to covers/ directory.

        Args:
            image_bytes: Raw image bytes
            extension: File extension (e.g. '.jpg')
            book_id: Book ID for the filename

        Returns:
            str: Relative path like 'covers/42.jpg'
        """
        covers_dir = os.path.join(self.config.script_dir, "covers")
        os.makedirs(covers_dir, exist_ok=True)
        filename = f"{book_id}{extension}"
        filepath = os.path.join(covers_dir, filename)
        with open(filepath, "wb") as f:
            f.write(image_bytes)
        self.logger.info(f"Saved cover image to {filepath}")
        rel_path = f"covers/{filename}"
        try:
            import spaces
            if spaces.is_enabled(self.config):
                spaces.upload_relpath(self.config, rel_path)
        except Exception:
            pass
        return rel_path

    def add_chapters_to_queue(self, chapters, book_id=None, epub_path=None):
        """
        Add chapters to the translation queue.

        Args:
            chapters: List of chapter dicts
            book_id: Book ID (required)
            epub_path: EPUB file path for source reference

        Returns:
            int: Number of chapters added to queue
        """
        if book_id is None:
            self.logger.error("book_id is required for adding chapters to queue")
            return 0

        added_count = 0
        for chapter in chapters:
            content = chapter['content']
            content_lines = content.split('\n') if isinstance(content, str) else content

            # Add to database queue
            queue_item_id = self.db_manager.add_to_queue(
                book_id=book_id,
                content=content_lines,
                title=chapter['title'],
                chapter_number=chapter['number'],
                source=chapter.get('file_path', epub_path)
            )

            if queue_item_id:
                added_count += 1
                # Persist illustrations referenced by this chapter's content.
                if self._collector is not None:
                    try:
                        store_chapter_illustrations(
                            self.db_manager, self.config, book_id,
                            content_lines, self._collector, queue_id=queue_item_id,
                        )
                    except Exception as e:
                        self.logger.error(f"Failed to store illustrations for chapter {chapter['number']}: {e}")
            else:
                self.logger.error(f"Failed to add chapter {chapter['number']} to queue")

        self.logger.info(f"Added {added_count} chapters to queue")
        return added_count

    def process_epub(self, epub_path, book_id=None):
        """
        Process an EPUB file and add chapters to the translation queue.
        
        Args:
            epub_path: Path to the EPUB file
            book_id: Optional book ID to associate with the chapters
            
        Returns:
            tuple: (success, num_chapters, message)
        """
        # Load the EPUB file
        book = self.load_epub(epub_path)
        if not book:
            return False, 0, f"Failed to load EPUB: {epub_path}"

        # Enable illustration extraction for this run.
        self._collector = IllustrationCollector()
        self._epub_book = book

        # Extract TOC
        toc = self.extract_toc(book)
        
        # Extract chapters
        if toc:
            chapters = self.extract_chapters(book, toc)
        else:
            chapters = self.extract_chapters(book)
        
        if not chapters:
            return False, 0, "No chapters found in EPUB"
        
        # Add to queue with book_id
        num_added = self.add_chapters_to_queue(chapters, book_id, epub_path)
        
        return True, num_added, f"Successfully added {num_added} chapters to queue"
