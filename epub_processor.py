"""
EPUB Processing Module for Translator Application.
Extracts chapters from EPUB files and adds them to the translation queue.
"""
import os
import re
import json
import logging
from bs4 import BeautifulSoup
from ebooklib import epub
import html2text


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
                content = self._process_html_content(item.content)
                
                # Skip empty chapters or very short ones (likely just titles)
                if not content or len(content) < 50:
                    continue
                
                chapter_number = i
                
                # Try to extract chapter number from title
                match = re.search(r'(\d+)', title)
                if match:
                    try:
                        chapter_number = int(match.group(1))
                    except ValueError:
                        pass
                
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
                content = self._process_html_content(item.content)
                
                # Skip empty or very short content
                if not content or len(content) < 50:
                    continue
                
                # Try to extract title and chapter number from content
                title = self._extract_title_from_content(content)
                chapter_number = i
                
                # If title found, try to extract chapter number
                if title:
                    match = re.search(r'(\d+)', title)
                    if match:
                        try:
                            chapter_number = int(match.group(1))
                        except ValueError:
                            pass
                else:
                    title = f"Chapter {i}"
                
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
    
    def _process_html_content(self, html_content):
        """
        Process HTML content to extract clean text.
        
        Args:
            html_content: Raw HTML content
            
        Returns:
            str: Cleaned text content
        """
        if isinstance(html_content, bytes):
            html_content = html_content.decode('utf-8', errors='replace')
        
        # Convert HTML to text
        text = self.h2t.handle(html_content)
        
        # Clean up the text
        text = re.sub(r'\n{3,}', '\n\n', text)  # Replace multiple newlines
        text = text.strip()
        
        return text
    
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
