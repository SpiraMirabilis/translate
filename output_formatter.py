"""
OutputFormatter module for Translator Application.
Handles conversion of translated content to various output formats.
"""
import os
import re
import json
import logging
import datetime
from ebooklib import epub
from typing import Dict, List, Optional, Union, Tuple


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
            return self._save_text(content, title, final_output_path)
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
    
    def _save_text(self, content: List[str], title: str, output_path: str) -> str:
        """
        Save content as plain text.
        
        Args:
            content: List of content lines
            title: Chapter title
            output_path: Path to save the file
            
        Returns:
            str: Path to the saved file
        """
        try:
            # Create directory if it doesn't exist
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(f"{title}\n\n")
                for line in content:
                    f.write(f"{line}\n")
            
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
                f.write(f'    <title>{title}</title>\n')
                f.write('    <style>\n')
                f.write('        body { font-family: Arial, sans-serif; line-height: 1.6; max-width: 800px; margin: 0 auto; padding: 20px; }\n')
                f.write('        h1 { text-align: center; margin-bottom: 30px; }\n')
                f.write('        p { margin-bottom: 1em; text-indent: 2em; }\n')
                f.write('        .empty-line { height: 1em; }\n')
                f.write('    </style>\n')
                f.write('</head>\n')
                f.write('<body>\n')
                f.write(f'    <h1>{title}</h1>\n')
                
                for line in content:
                    if line.strip() == "":
                        f.write('    <div class="empty-line"></div>\n')
                    else:
                        # Escape HTML special characters
                        line = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                        f.write(f'    <p>{line}</p>\n')
                
                f.write('</body>\n')
                f.write('</html>\n')
            
            self.logger.info(f"Saved HTML output to {output_path}")
            return output_path
        except Exception as e:
            self.logger.error(f"Error saving HTML output: {e}")
            return ""
    
    def _save_markdown(self, filename_base: str, content: List[str], title: str, chapter: Union[int, str]) -> str:
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
        output_path = os.path.join(self.output_dir, f"{filename_base}.md")
        
        try:

            # Create directory if it doesn't exist
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(f"# {title}\n\n")
                
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
    
    def _save_epub(self, filename_base: str, content: List[str], title: str, chapter: Union[int, str], book_info: Dict = None) -> str:
        """
        Save content as EPUB or append to existing EPUB.
        
        Args:
            filename_base: Base filename
            content: List of content lines
            title: Chapter title
            chapter: Chapter number
            book_info: Optional dictionary with book metadata
            
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
                        body { font-family: serif; }
                        h1 { text-align: center; margin-bottom: 1em; }
                        p { text-indent: 1.5em; margin-top: 0.5em; margin-bottom: 0.5em; }
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
                        <p>This book was translated using the Translator tool.</p>
                        <p>Translation date: {datetime.datetime.now().strftime('%Y-%m-%d')}</p>
                    </body>
                    </html>
                '''
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
            chapter_id = f"chapter_{chapter}"
            chapter_filename = f"chapter_{chapter}.xhtml"
            
            # Check if chapter already exists
            chapter_exists = False
            for item in book.get_items():
                if item.file_name == chapter_filename:
                    chapter_exists = True
                    self.logger.warning(f"Chapter {chapter} already exists in EPUB, updating content")
                    item.content = f'''
                        <html>
                        <head>
                            <title>{title}</title>
                            <link rel="stylesheet" href="style/default.css" type="text/css" />
                        </head>
                        <body>
                            <h1>{title}</h1>
                            {html_content}
                        </body>
                        </html>
                    '''
                    break
            
            if not chapter_exists:
                # Create new chapter
                epub_chapter = epub.EpubHtml(title=title, file_name=chapter_filename, lang=book_language)
                epub_chapter.content = f'''
                    <html>
                    <head>
                        <title>{title}</title>
                        <link rel="stylesheet" href="style/default.css" type="text/css" />
                    </head>
                    <body>
                        <h1>{title}</h1>
                        {html_content}
                    </body>
                    </html>
                '''
                book.add_item(epub_chapter)
                
                # Add chapter to table of contents and spine
                book.spine.append(epub_chapter)
                book.toc.append(epub.Link(chapter_filename, title, chapter_id))
            
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
