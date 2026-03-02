import os
from epub_processor import EPUBProcessor
from typing import Dict, List, Optional, Any, Union, Tuple
from abc import ABC, abstractmethod
from database import DatabaseManager
from logger import Logger
from translation_engine import TranslationEngine
from ui import UserInterface
from output_formatter import OutputFormatter
from providers import get_factory
import json
import sqlite3
import re
import curses
import tempfile
import subprocess
import platform
import threading
import time

class CommandLineInterface(UserInterface):
    """Command-line interface implementation"""
    
    def __init__(self, translator: TranslationEngine, entity_manager: DatabaseManager, logger: Logger):
        super().__init__(translator, entity_manager, logger)
        self.import_optional_modules()
    
    def import_optional_modules(self):
        """Import modules needed for CLI that might not be available in all UI versions"""
        try:
            import questionary
            from rich import print_json
            self.questionary = questionary
            self.print_json = print_json
            self.has_rich_ui = True
        except ImportError:
            self.has_rich_ui = False
            print("Warning: Rich UI components (questionary/rich) not available. Using basic interface.")
    
    def play_notification_sound(self):
        """Play a notification sound to alert the user about new entities"""
        def play_sound():
            try:
                system = platform.system()
                if system == "Darwin":  # macOS
                    # Use the Glass sound for new entities notification
                    subprocess.run(["afplay", "/System/Library/Sounds/Glass.aiff"], 
                                 check=True, capture_output=True)
                elif system == "Linux":
                    # Try to use paplay (PulseAudio) or aplay (ALSA)
                    try:
                        subprocess.run(["paplay", "/usr/share/sounds/alsa/Front_Right.wav"], 
                                     check=True, capture_output=True)
                    except (subprocess.CalledProcessError, FileNotFoundError):
                        try:
                            subprocess.run(["aplay", "/usr/share/sounds/alsa/Front_Right.wav"], 
                                         check=True, capture_output=True)
                        except (subprocess.CalledProcessError, FileNotFoundError):
                            # Fallback to system bell
                            print("\a")  # ASCII bell character
                elif system == "Windows":
                    # Use winsound for Windows
                    try:
                        import winsound
                        winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
                    except ImportError:
                        print("\a")  # ASCII bell character fallback
                else:
                    # Unknown system, use ASCII bell
                    print("\a")
            except Exception as e:
                # If all else fails, use the ASCII bell character
                self.logger.debug(f"Error playing notification sound: {e}")
                print("\a")
        
        # Play sound in a separate thread to avoid blocking the UI
        sound_thread = threading.Thread(target=play_sound, daemon=True)
        sound_thread.start()
    
    def get_input(self) -> List[str]:
        """Get input text from CLI - clipboard, file, or manual entry"""
        import argparse
        
        # Create the parser
        parser = argparse.ArgumentParser(description="Process input from clipboard, file, or manual entry.")
        
        # Create a mutually exclusive group, this is for the input option
        group = parser.add_mutually_exclusive_group()
        
        # Add arguments to the group
        group.add_argument("--clipboard", action="store_true", help="Process input from the clipboard")
        group.add_argument("--resume", action="store_true", help="Take input from the queue and translate sequentially (optional: --book-id to process specific book)")
        group.add_argument("--file", type=str, help="Process input from a specified file")
        group.add_argument("--epub", type=str, help="Process an EPUB file and add chapters to the queue")
        parser.add_argument("--create-book-from-epub", action="store_true", 
                        help="Create a new book from EPUB metadata when processing/queuing EPUB file")
        

        # directory input and options
        group.add_argument("--dir", type=str, help="Process all text files in a directory and add to queue")
        parser.add_argument("--sort", type=str, choices=["auto", "name", "modified", "none"], default="auto",
                    help="Sorting strategy for directory files (default: auto)")
        parser.add_argument("--pattern", type=str, default="*.txt",
                    help="File pattern for directory processing (default: *.txt)")
        
        # Book management
        book_group = parser.add_argument_group('Book Management')
        book_group.add_argument("--create-book", type=str, help="Create a new book with the specified title")
        book_group.add_argument("--book-author", type=str, help="Specify author when creating a book")
        book_group.add_argument("--book-language", type=str, help="Specify language code when creating a book")
        book_group.add_argument("--book-description", type=str, help="Specify description when creating a book")
        
        book_group.add_argument("--list-books", action="store_true", help="List all books in the database")
        book_group.add_argument("--book-info", type=int, help="Get detailed information about a book by ID")
        book_group.add_argument("--edit-book", type=int, help="Edit book information by ID")
        book_group.add_argument("--delete-book", type=int, help="Delete a book and all its chapters by ID")

        # book specific model group
        parser.add_argument("--show-prompt-template", type=int, help="Show the current prompt template for a book (by ID)")
        parser.add_argument("--set-prompt-template", type=int, help="Set a custom prompt template for a book (by ID)")
        parser.add_argument("--prompt-file", type=str, help="Load prompt template from a file")
        parser.add_argument("--export-default-prompt", type=str, help="Export the default prompt template to a file")
        parser.add_argument("--edit-prompt", type=int, help="Edit the prompt template for a book using your system editor")
        parser.add_argument("--no-review", action="store_true",
                            help="Disable the entity review process at end of each translated chapter" )
        parser.add_argument("--silent-notifications", action="store_true",
                            help="Disable audio notifications when new entities are found for review")
        
        # Chapter management
        chapter_group = parser.add_argument_group('Chapter Management')
        chapter_group.add_argument("--book-id", type=int, help="Specify book ID for translation or chapter operations")
        chapter_group.add_argument("--chapter-number", type=int, help="Specify chapter number for translation or retrieval")
        chapter_group.add_argument("--list-chapters", type=int, help="List all chapters for a book by ID")
        chapter_group.add_argument("--get-chapter", action="store_true", 
                                help="Get a specific chapter (requires --book-id and --chapter-number)")
        chapter_group.add_argument("--delete-chapter", action="store_true", 
                                help="Delete a specific chapter (requires --book-id and --chapter-number)")
        chapter_group.add_argument("--export-book", type=int, 
                                help="Export all chapters of a book (book ID) to specified format")
        chapter_group.add_argument("--retranslate", action="store_true",
                    help="Retranslate a chapter (requires --book-id and --chapter-number)")
        chapter_group.add_argument("--edit-chapter-translation", action="store_true",
                    help="Edit the translation of a chapter using your system editor (requires --book-id and --chapter-number)")


        # output options
        parser.add_argument("--format", type=str, choices=["text", "html", "markdown", "epub"], default="text",
                   help="Output format for translation results (default: text)")
        parser.add_argument("--epub-title", type=str, help="Book title for EPUB output")
        parser.add_argument("--epub-author", type=str, help="Book author for EPUB output")
        parser.add_argument("--epub-language", type=str, default="en", help="Book language code for EPUB output (default: en)")
        parser.add_argument("--edit-epub-info", action="store_true", help="Edit book information for EPUB output")
        
        # Queue argument and manipulation
        parser.add_argument("--queue", action="store_true", help="Add a chapter to the queue for later sequential translation (requires --book-id)")
        parser.add_argument("--list-queue", action="store_true",
                        help="List all items in the translation queue (optional: --book-id to filter)")
        parser.add_argument("--clear-queue", action="store_true",
                        help="Clear the translation queue (optional: --book-id to clear for specific book only)")
        
        # SQLite arguments
        parser.add_argument("--export-json", type=str, help="Export SQLite database to JSON file")
        parser.add_argument("--import-json", type=str, help="Import entities from JSON file to SQLite database")
        parser.add_argument("--check-duplicates", action="store_true", help="Check for duplicate entities in the database")

        # Entity review
        parser.add_argument("--review-entities", action="store_true",
                            help="Review all entities in the database interactively")
        parser.add_argument("--entity-book-id", type=int,
                            help="Filter entities by book ID (use with --review-entities)")
        parser.add_argument("--entity-category", type=str,
                            choices=["characters", "places", "organizations", "abilities", "titles", "equipment", "creatures"],
                            help="Filter entities by category (use with --review-entities)")
        
        # Model arguments
        try:
            factory = get_factory()
            supported_providers = ', '.join(factory.get_supported_providers())
            model_help = f"Specify model for translation (format: [provider:]model). Supported providers: {supported_providers}"
            advice_help = f"Specify model for entity translation advice (format: [provider:]model). Supported providers: {supported_providers}"
        except:
            model_help = "Specify model for translation (format: [provider:]model, e.g., oai:gpt-4 or claude:claude-3-5-sonnet)"
            advice_help = "Specify model for entity translation advice (format: [provider:]model)"
        
        parser.add_argument("--model", type=str, help=model_help)
        parser.add_argument("--advice-model", type=str, help=advice_help)
        parser.add_argument("--cleaning-model", type=str, help="Specify model for entity cleaning/classification (format: [provider:]model). Uses --model if not specified.")
        parser.add_argument("--key", type=str,
                    help="Specify API key (for the provider specified in --model)")
        parser.add_argument("--list-providers", action="store_true",
                    help="List all supported model providers and their default models")

        parser.add_argument("--no-stream", action="store_true",
                    help="Disable streaming API and progress tracking (slightly faster for very short texts)")
        parser.add_argument("--no-clean", action="store_true",
                    help="Disable automatic cleaning of generic nouns from new entities during post-translation review")
        
        args = parser.parse_args()
   
        if args.no_review:
            self.no_review = True
        else:
            self.no_review = False

        if args.no_stream:
            self.stream = False
        else:
            self.stream = True

        if args.no_clean:
            self.no_clean = True
        else:
            self.no_clean = False

        # Store cleaning model spec (will be used by entity cleaning)
        self.cleaning_model = args.cleaning_model

        if args.silent_notifications:
            self.silent_notifications = True
        else:
            self.silent_notifications = False

        # Book management
        if args.create_book:
            self._create_book(args.create_book, args.book_author, args.book_language, args.book_description)
            exit(0)
            
        if args.list_books:
            self._list_books()
            exit(0)
            
        if args.book_info:
            self._show_book_info(args.book_info)
            exit(0)
            
        if args.edit_book:
            self._edit_book(args.edit_book)
            exit(0)
            
        if args.delete_book:
            self._delete_book(args.delete_book)
            exit(0)

        # Book specific prompt section
        if args.edit_prompt:
            self._edit_prompt_template(args.edit_prompt)
            exit(0)

        if args.show_prompt_template:
            self._show_prompt_template(args.show_prompt_template)
            exit(0)

        if args.set_prompt_template:
            if not args.prompt_file:
                print("Error: --set-prompt-template requires --prompt-file")
                exit(1)
            self._set_prompt_template(args.set_prompt_template, args.prompt_file)
            exit(0)

        if args.export_default_prompt:
            self._export_default_prompt(args.export_default_prompt)
            exit(0)
        
        # List providers
        if args.list_providers:
            self._list_providers()
            exit(0)
        
        # Chapter management
        if args.list_chapters:
            self._list_chapters(args.list_chapters)
            exit(0)
            
        if args.get_chapter:
            if not args.book_id or not args.chapter_number:
                print("Error: --get-chapter requires --book-id and --chapter-number")
                exit(1)
            self._get_chapter(args.book_id, args.chapter_number, args.format or "text")
            exit(0)
            
        if args.delete_chapter:
            if not args.book_id or not args.chapter_number:
                print("Error: --delete-chapter requires --book-id and --chapter-number")
                exit(1)
            self._delete_chapter(args.book_id, args.chapter_number)
            exit(0)

        if args.edit_chapter_translation:
            if not args.book_id or not args.chapter_number:
                print("Error: --edit-chapter-translation requires --book-id and --chapter-number")
                exit(1)
            self._edit_chapter_translation(args.book_id, args.chapter_number)
            exit(0)

        if args.export_book:
            self._export_book(args.export_book, args.format or "text")
            exit(0)
        
        # Store book_id if specified
        if args.book_id:
            # Verify book exists
            book = self.entity_manager.get_book(book_id=args.book_id)
            if not book:
                print(f"Error: Book with ID {args.book_id} not found")
                exit(1)
            self.book_id = args.book_id
            self.book_title = book["title"]
        else:
            self.book_id = None
            self.book_title = None
        
        # Store chapter_number if specified
        if args.chapter_number:
            self.chapter_number = args.chapter_number
        else:
            self.chapter_number = None

        # Handle retranslation
        if args.retranslate:
            if not args.book_id or not args.chapter_number:
                print("Error: --retranslate requires --book-id and --chapter-number")
                exit(1)
            
            return self._retranslate_chapter(args.book_id, args.chapter_number)
        
    # Rest of the method...

        # CLI provided API keys
        if args.key:
            # Determine which provider this key is for using the factory
            factory = get_factory()
            
            if args.model:
                provider_name, _ = self.translator.config.parse_model_spec(args.model)
            else:
                # If no model specified, use the provider from translation_model
                provider_name, _ = self.translator.config.parse_model_spec(self.translator.config.translation_model)
            
            # Resolve provider name through aliases
            resolved_name = factory._resolve_provider_name(provider_name)
            
            # Get the API key environment variable for this provider
            if resolved_name in factory.config['providers']:
                api_key_env = factory.config['providers'][resolved_name].get('api_key_env')
                if api_key_env:
                    os.environ[api_key_env] = args.key
                    print(f"Set {api_key_env} for provider '{provider_name}'")
                else:
                    print(f"Warning: No API key environment variable configured for provider '{provider_name}'")
            else:
                print(f"Warning: Unknown provider '{provider_name}'. Available providers: {factory.get_supported_providers()}")

        # CLI provided model
        if args.model:
            self.translator.config.translation_model = args.model

        # Handle advice model override
        if args.advice_model:
            self.translator.config.advice_model = args.advice_model

        # Process directory
        if args.dir:
            if not args.book_id:
                print("Error: --dir requires --book-id to associate chapters with a book")
                print("Use --list-books to see available books or --create-book to create a new one")
                exit(1)
            self._process_directory(args.dir, args.book_id, args.sort, args.pattern)
            exit(0)  # Exit after processing directory

        # edit epub info from --edit-book-info
        if args.edit_epub_info:
            self._edit_book_info()
            exit(0)

        # List queue contents
        if args.list_queue:
            self._list_queue_contents(book_id=args.book_id)
            exit(0)

        # Clear queue
        if args.clear_queue:
            self._clear_queue(book_id=args.book_id)
            exit(0)
        
        # Store the format in a class variable
        self.output_format = args.format

        # Create book_info if EPUB format is selected
        if self.output_format == "epub":
            self.book_info = self._get_book_info()
            
            # Override with command line arguments if provided
            if args.epub_title:
                self.book_info["title"] = args.epub_title
            if args.book_author:
                self.book_info["author"] = args.epub_author
            if args.book_language:
                self.book_info["language"] = args.epub_language
        else:
            self.book_info = None

        
        # Handle SQLite database management commands
        if args.export_json:
            self.logger.info(f"Exporting SQLite database to {args.export_json}")
            if self.entity_manager.export_to_json(args.export_json):
                print(f"Database successfully exported to {args.export_json}")
            else:
                print("Export failed. Check logs for details.")
            exit(0)
            
        if args.import_json:
            self.logger.info(f"Importing entities from {args.import_json} to SQLite database")
            if self.entity_manager.import_from_json(args.import_json):
                print(f"Successfully imported entities from {args.import_json}")
            else:
                print("Import failed. Check logs for details.")
            exit(0)
            
        if args.check_duplicates:
            self.check_database_duplicates()
            exit(0)

        # Handle entity review
        if args.review_entities:
            if not self.has_rich_ui:
                print("Error: Entity review requires questionary. Install: pip install questionary rich")
                exit(1)

            if args.entity_book_id:
                book = self.entity_manager.get_book(book_id=args.entity_book_id)
                if not book:
                    print(f"Error: Book with ID {args.entity_book_id} not found")
                    exit(1)

            self._review_all_entities(
                book_id=args.entity_book_id,
                category_filter=args.entity_category
            )
            exit(0)

        # Get the input based on the arguments
        if args.clipboard:
            import pyperclip
            self.logger.info("Processing input from the clipboard.")
            pretext = pyperclip.paste().splitlines()
        elif args.file:
            self.logger.info(f"Processing input from the file: {args.file}")
            pretext = self.file_to_array(args.file)
        elif args.resume:
            # Get book_id filter if provided
            filter_book_id = args.book_id if args.book_id else None

            # Get next queue item from database
            queue_item = self.entity_manager.get_next_queue_item(book_id=filter_book_id)

            if not queue_item:
                if filter_book_id:
                    print(f"No items in queue for book ID {filter_book_id}.")
                else:
                    print("No items in queue to resume.")
                exit(1)

            # Store queue item for later removal (in ui.py)
            self._current_queue_item = queue_item

            # Set book context
            self.book_id = queue_item['book_id']
            self.book_title = queue_item['book_title']
            self.chapter_number = queue_item.get('chapter_number')

            self.logger.info(f"Processing queue item for book '{self.book_title}' (ID: {self.book_id})")
            print(f"Processing: {queue_item['title']} from '{self.book_title}'")

            # Get content (already deserialized from database method)
            pretext = queue_item['content']
        elif args.epub:
            if not args.book_id and not args.create_book_from_epub:
                print("When ingesting an --epub you MUST select either --book-id # or --create-book-from-epub to properly associate queued chapters with correct book")
                exit(1)
            elif args.book_id and args.create_book_from_epub:
                print("When ingesting an epub with --epub, --book-id and --create-book-from-epub are mutually exclusive. choose one or the other.")
                exit(1)
            book_id = args.book_id
            create_book = args.create_book_from_epub
            self._process_epub_file(args.epub, book_id, create_book)
            exit(0)  # Exit after processing EPUB
        else:
            # Manual entry
            print("Enter/Paste your content. Type ENDEND or Ctrl-D out to start translating.")
            pretext = []
            while True:
                try:
                    line = input()
                    if line == "ENDEND":
                        break
                    pretext.append(line)
                except EOFError:
                    break
        
        # Handle queue option - this should happen for all input types
        if args.queue:
            # Validate book_id is provided
            if not args.book_id:
                print("Error: --queue requires --book-id to associate chapters with a book")
                print("Use --list-books to see available books or --create-book to create a new one")
                exit(1)

            # Verify book exists (book_id is already set in self.book_id from earlier validation)
            # Add to queue using database
            queue_item_id = self.entity_manager.add_to_queue(
                book_id=self.book_id,
                content=pretext,
                title=self.book_title,
                chapter_number=self.chapter_number,
                source="CLI input"
            )

            if queue_item_id:
                queue_count = self.entity_manager.get_queue_count()
                self.logger.info(f"Added to queue (total: {queue_count} items)")
                print(f"Added to queue for book '{self.book_title}' (ID: {self.book_id})")
                print(f"Total queue size: {queue_count}")
            else:
                print("Error: Failed to add to queue")
                exit(1)

            exit(0)  # Exit after queuing - don't continue to translation!
        
        return pretext
    
    def _process_epub_file(self, epub_path, book_id=None, create_book=False):
        """Process an EPUB file and add chapters to the queue with book association."""
        try:
            # Initialize EPUB processor
            processor = EPUBProcessor(self.entity_manager.config, self.logger, self.entity_manager)
            
            # Load basic EPUB metadata if needed for book creation
            if create_book:
                book_metadata = processor.get_epub_metadata(epub_path)
                
                if not book_id:  # Only create a book if one wasn't specified
                    # Create a new book from EPUB metadata
                    book_title = book_metadata.get('title', os.path.basename(epub_path))
                    book_author = book_metadata.get('author', 'Unknown')
                    
                    # Create book in database
                    book_id = self.entity_manager.create_book(
                        title=book_title,
                        author=book_author,
                        language="en",  # Default target language
                        source_language="zh",  # Default source language
                        description=f"Imported from {os.path.basename(epub_path)}"
                    )
                    
                    if book_id:
                        print(f"Created new book '{book_title}' (ID: {book_id}) from EPUB metadata")
                    else:
                        print("Failed to create book from EPUB metadata")
            
            # Validate book_id if provided
            if book_id:
                book = self.entity_manager.get_book(book_id=book_id)
                if not book:
                    print(f"Error: Book with ID {book_id} not found")
                    return
                
                print(f"Processing EPUB file: {epub_path} for book '{book['title']}' (ID: {book_id})")
            else:
                print(f"Processing EPUB file: {epub_path} (no book association)")
            
            # Process the EPUB with the book_id
            success, num_chapters, message = processor.process_epub(epub_path, book_id)
            
            if success:
                print(f"Success! {message}")
                
                # Show queue summary
                self._list_queue_contents(summary_only=True)
            else:
                print(f"Failed! {message}")
                
        except Exception as e:
            self.logger.error(f"Error processing EPUB: {e}")
            print(f"Error processing EPUB: {e}")
            import traceback
            traceback.print_exc()
    
    def check_database_duplicates(self):
        """
        Check for entity duplications in the database, report them, and
        provide interactive resolution options.
        """
        
        if not self.has_rich_ui:
            print("Rich UI components not available. Reporting duplicates without interactive resolution.")
            self._report_database_duplicates()
            return
        
        # Connect to the database
        conn = sqlite3.connect(self.entity_manager.db_path)
        cursor = conn.cursor()
        
        # Check for duplicate untranslated entities
        print("\n=== Checking for Duplicate Untranslated Entities ===")
        cursor.execute('''
        SELECT untranslated, COUNT(*) as count, GROUP_CONCAT(category) as categories
        FROM entities
        GROUP BY untranslated
        HAVING COUNT(*) > 1
        ''')
        
        duplicate_untranslated = cursor.fetchall()
        if duplicate_untranslated:
            print(f"Found {len(duplicate_untranslated)} duplicate untranslated entities:")
            
            for idx, row in enumerate(duplicate_untranslated, 1):
                untranslated, count, categories = row
                print(f"\n{idx}. '{untranslated}' appears in {count} categories: {categories}")
                
                # Get details for this duplicate
                cursor.execute('''
                SELECT category, translation, last_chapter
                FROM entities
                WHERE untranslated = ?
                ORDER BY category
                ''', (untranslated,))
                
                instances = cursor.fetchall()
                for i, (category, translation, last_chapter) in enumerate(instances):
                    print(f"   {chr(97+i)}) In '{category}' as '{translation}' (Chapter {last_chapter})")
                
                # Ask if user wants to resolve this duplicate
                resolve = self.questionary.confirm(
                    f"Do you want to resolve this duplicate entity '{untranslated}'?"
                ).ask()
                
                if resolve:
                    # Ask what action to take
                    action = self.questionary.select(
                        "How would you like to resolve this duplicate?",
                        choices=[
                            "Keep one instance and delete others",
                            "Rename instances to be distinct",
                            "Get LLM suggestions for better translations",
                            "Skip for now"
                        ]
                    ).ask()
                    
                    if action == "Keep one instance and delete others":
                        # Let user choose which instance to keep
                        keep_choices = [f"{category}: '{translation}'" for category, translation, _ in instances]
                        keep_choice = self.questionary.select(
                            "Which instance would you like to keep?",
                            choices=keep_choices
                        ).ask()
                        
                        keep_idx = keep_choices.index(keep_choice)
                        keep_category = instances[keep_idx][0]
                        
                        # Delete all other instances
                        for category, _, _ in instances:
                            if category != keep_category:
                                cursor.execute('''
                                DELETE FROM entities
                                WHERE untranslated = ? AND category = ?
                                ''', (untranslated, category))
                        
                        conn.commit()
                        print(f"Kept '{untranslated}' in '{keep_category}' and deleted other instances.")
                    
                    elif action == "Rename instances to be distinct":
                        for category, translation, _ in instances:
                            new_translation = self.questionary.text(
                                f"Enter new translation for '{untranslated}' in '{category}' (current: '{translation}'):",
                                default=translation
                            ).ask()
                            
                            if new_translation != translation:
                                cursor.execute('''
                                UPDATE entities
                                SET translation = ?
                                WHERE untranslated = ? AND category = ?
                                ''', (new_translation, untranslated, category))
                                print(f"Updated translation to '{new_translation}' for '{untranslated}' in '{category}'.")
                        
                        conn.commit()
                    
                    elif action == "Get LLM suggestions for better translations":
                        # Get suggestions from LLM for each instance
                        for category, translation, _ in instances:
                            # Create a node object similar to what get_translation_options expects
                            node = {
                                'untranslated': untranslated,
                                'translation': translation,
                                'category': category
                            }
                            
                            print(f"\nGetting suggestions for '{untranslated}' in '{category}'...")
                            advice = self.translator.get_translation_options(node, [])
                            
                            print("\nLLM suggests:")
                            print(f"  \"{advice['message']}\"\n")
                            
                            # Add options to choose from
                            translation_options = advice['options'] + ["Keep current translation", "Custom translation"]
                            
                            chosen_translation = self.questionary.select(
                                f"Choose a translation for '{untranslated}' in '{category}':",
                                choices=translation_options
                            ).ask()
                            
                            if chosen_translation == "Keep current translation":
                                print(f"Keeping current translation '{translation}'.")
                            elif chosen_translation == "Custom translation":
                                custom_val = self.questionary.text(
                                    "Enter your custom translation:",
                                    default=translation
                                ).ask()
                                
                                if custom_val and custom_val != translation:
                                    cursor.execute('''
                                    UPDATE entities
                                    SET translation = ?
                                    WHERE untranslated = ? AND category = ?
                                    ''', (custom_val, untranslated, category))
                                    print(f"Updated translation to '{custom_val}' for '{untranslated}' in '{category}'.")
                            else:
                                # User selected one of the suggested translations
                                cursor.execute('''
                                UPDATE entities
                                SET translation = ?
                                WHERE untranslated = ? AND category = ?
                                ''', (chosen_translation, untranslated, category))
                                print(f"Updated translation to '{chosen_translation}' for '{untranslated}' in '{category}'.")
                        
                        conn.commit()
        else:
            print("No duplicate untranslated entities found.")
        
        # Now check for duplicate translations
        print("\n=== Checking for Duplicate Translations ===")
        cursor.execute('''
        SELECT translation, COUNT(*) as count, GROUP_CONCAT(untranslated) as untranslated_list, 
            GROUP_CONCAT(category) as categories
        FROM entities
        GROUP BY translation
        HAVING COUNT(*) > 1
        ''')
        
        duplicate_translations = cursor.fetchall()
        if duplicate_translations:
            print(f"Found {len(duplicate_translations)} duplicate translations:")
            
            for idx, row in enumerate(duplicate_translations, 1):
                translation, count, untranslated_list, categories = row
                print(f"\n{idx}. '{translation}' is used for {count} different entities:")
                
                # Get detailed information for each duplicate
                cursor.execute('''
                SELECT category, untranslated, last_chapter
                FROM entities
                WHERE translation = ?
                ORDER BY category
                ''', (translation,))
                
                details = cursor.fetchall()
                for i, (category, untranslated, last_chapter) in enumerate(details):
                    print(f"   {chr(97+i)}) In '{category}': '{untranslated}' (Chapter {last_chapter})")
                
                # Ask if user wants to resolve this duplicate
                resolve = self.questionary.confirm(
                    f"Do you want to resolve this duplicate translation '{translation}'?"
                ).ask()
                
                if resolve:
                    # Ask what action to take
                    action = self.questionary.select(
                        "How would you like to resolve this duplicate?",
                        choices=[
                            "Rename translations to be distinct",
                            "Get LLM suggestions for better translations",
                            "Skip for now"
                        ]
                    ).ask()
                    
                    if action == "Rename translations to be distinct":
                        for category, untranslated, _ in details:
                            new_translation = self.questionary.text(
                                f"Enter new translation for '{untranslated}' in '{category}' (current: '{translation}'):",
                                default=translation
                            ).ask()
                            
                            if new_translation != translation:
                                cursor.execute('''
                                UPDATE entities
                                SET translation = ?
                                WHERE untranslated = ? AND category = ?
                                ''', (new_translation, untranslated, category))
                                print(f"Updated translation to '{new_translation}' for '{untranslated}' in '{category}'.")
                        
                        conn.commit()
                    
                    elif action == "Get LLM suggestions for better translations":
                        # Get suggestions from LLM for each instance
                        for category, untranslated, _ in details:
                            # Create a node object similar to what get_translation_options expects
                            node = {
                                'untranslated': untranslated,
                                'translation': translation,
                                'category': category
                            }
                            
                            print(f"\nGetting suggestions for '{untranslated}' in '{category}'...")
                            advice = self.translator.get_translation_options(node, [])
                            
                            print("\nLLM suggests:")
                            print(f"  \"{advice['message']}\"\n")
                            
                            # Add options to choose from
                            translation_options = advice['options'] + ["Keep current translation", "Custom translation"]
                            
                            chosen_translation = self.questionary.select(
                                f"Choose a translation for '{untranslated}' in '{category}':",
                                choices=translation_options
                            ).ask()
                            
                            if chosen_translation == "Keep current translation":
                                print(f"Keeping current translation '{translation}'.")
                            elif chosen_translation == "Custom translation":
                                custom_val = self.questionary.text(
                                    "Enter your custom translation:",
                                    default=translation
                                ).ask()
                                
                                if custom_val and custom_val != translation:
                                    cursor.execute('''
                                    UPDATE entities
                                    SET translation = ?
                                    WHERE untranslated = ? AND category = ?
                                    ''', (custom_val, untranslated, category))
                                    print(f"Updated translation to '{custom_val}' for '{untranslated}' in '{category}'.")
                            else:
                                # User selected one of the suggested translations
                                cursor.execute('''
                                UPDATE entities
                                SET translation = ?
                                WHERE untranslated = ? AND category = ?
                                ''', (chosen_translation, untranslated, category))
                                print(f"Updated translation to '{chosen_translation}' for '{untranslated}' in '{category}'.")
                        
                        conn.commit()
        else:
            print("No duplicate translations found.")
        
        # Apply changes and update in-memory cache
        conn.close()
        self.entity_manager._load_entities()
        
        print("\nDuplicate check and resolution completed.")

    def _report_database_duplicates(self):
        """Non-interactive version of check_database_duplicates that just reports issues"""
        
        # Connect to the database
        conn = sqlite3.connect(self.entity_manager.db_path)
        cursor = conn.cursor()
        
        # Check for duplicate untranslated entities
        print("\n=== Duplicate Untranslated Entities ===")
        cursor.execute('''
        SELECT untranslated, COUNT(*) as count, GROUP_CONCAT(category) as categories
        FROM entities
        GROUP BY untranslated
        HAVING COUNT(*) > 1
        ''')
        
        duplicate_untranslated = cursor.fetchall()
        if duplicate_untranslated:
            print(f"Found {len(duplicate_untranslated)} duplicate untranslated entities:")
            for row in duplicate_untranslated:
                untranslated, count, categories = row
                print(f"  '{untranslated}' appears in {count} categories: {categories}")
                
                # List the details
                cursor.execute('''
                SELECT category, translation
                FROM entities
                WHERE untranslated = ?
                ORDER BY category
                ''', (untranslated,))
                
                details = cursor.fetchall()
                for category, translation in details:
                    print(f"    - {category}: '{translation}'")
        else:
            print("No duplicate untranslated entities found.")
        
        # Check for duplicate translations
        print("\n=== Duplicate Translations ===")
        cursor.execute('''
        SELECT translation, COUNT(*) as count, GROUP_CONCAT(untranslated) as untranslated_list
        FROM entities
        GROUP BY translation
        HAVING COUNT(*) > 1
        ''')
        
        duplicate_translations = cursor.fetchall()
        if duplicate_translations:
            print(f"Found {len(duplicate_translations)} duplicate translations:")
            for row in duplicate_translations:
                translation, count, untranslated_list = row
                print(f"  '{translation}' is used for {count} different entities:")
                
                # List the details
                cursor.execute('''
                SELECT category, untranslated
                FROM entities
                WHERE translation = ?
                ORDER BY category, untranslated
                ''', (translation,))
                
                details = cursor.fetchall()
                for category, untranslated in details:
                    print(f"    - {category}: '{untranslated}'")
        else:
            print("No duplicate translations found.")
        
        conn.close()

    # Book private methods
    def _create_book(self, title, author=None, language=None, description=None):
        """Create a new book in the database"""
        book_id = self.entity_manager.create_book(
            title, 
            author=author,
            language=language or 'en',
            description=description
        )
        
        if book_id:
            print(f"Book created: '{title}' (ID: {book_id})")
            print(f"Use --book-id {book_id} when translating chapters for this book")
        else:
            print(f"Failed to create book: '{title}'")

    def _list_books(self):
        """List all books in the database"""
        books = self.entity_manager.list_books()
        
        if not books:
            print("No books found in the database.")
            return
        
        print(f"Found {len(books)} books:\n")
        
        for book in books:
            print(f"ID: {book['id']} - {book['title']}")
            if book['author']:
                print(f"  Author: {book['author']}")
            print(f"  Language: {book['language']}")
            print(f"  Created: {book['created_date']}")
            print(f"  Chapters: {book['chapter_count']}")
            print()

    def _show_book_info(self, book_id):
        """Show detailed information about a book"""
        book = self.entity_manager.get_book(book_id=book_id)
        
        if not book:
            print(f"Book with ID {book_id} not found.")
            return
        
        print(f"Book: {book['title']} (ID: {book['id']})")
        print(f"Author: {book['author'] or 'Unknown'}")
        print(f"Language: {book['language']}")
        print(f"Source Language: {book['source_language']}")
        print(f"Target Language: {book['target_language']}")
        print(f"Created: {book['created_date']}")
        print(f"Last Modified: {book['modified_date']}")
        
        if book['description']:
            print(f"\nDescription: {book['description']}")
        
        # Get chapters count
        chapters = self.entity_manager.list_chapters(book_id)
        print(f"\nChapters: {len(chapters)}")
        
        if chapters:
            print("\nFirst 5 chapters:")
            for i, chapter in enumerate(chapters[:5]):
                print(f"  Chapter {chapter['chapter']}: {chapter['title']}")

    def _edit_book(self, book_id):
        """Edit book information interactively"""
        book = self.entity_manager.get_book(book_id=book_id)
        
        if not book:
            print(f"Book with ID {book_id} not found.")
            return
        
        print(f"Editing book: {book['title']} (ID: {book['id']})")
        
        if self.has_rich_ui:
            # Interactive editing with questionary
            new_title = self.questionary.text(
                "Title:",
                default=book['title']
            ).ask()
            
            new_author = self.questionary.text(
                "Author:",
                default=book['author'] or ""
            ).ask()
            
            new_language = self.questionary.text(
                "Language code (e.g., en, zh, ja):",
                default=book['language']
            ).ask()
            
            new_description = self.questionary.text(
                "Description:",
                default=book['description'] or ""
            ).ask()
            
            new_source_language = self.questionary.text(
                "Source language code:",
                default=book['source_language']
            ).ask()
            
            new_target_language = self.questionary.text(
                "Target language code:",
                default=book['target_language']
            ).ask()
        else:
            # Basic input
            print("\nEnter new values (press Enter to keep current values):")
            new_title = input(f"Title [{book['title']}]: ") or book['title']
            new_author = input(f"Author [{book['author'] or ''}]: ") or book['author']
            new_language = input(f"Language code [{book['language']}]: ") or book['language']
            new_description = input(f"Description [{book['description'] or ''}]: ") or book['description']
            new_source_language = input(f"Source language code [{book['source_language']}]: ") or book['source_language']
            new_target_language = input(f"Target language code [{book['target_language']}]: ") or book['target_language']
        
        # Update the book
        result = self.entity_manager.update_book(
            book_id,
            title=new_title,
            author=new_author if new_author else None,
            language=new_language,
            description=new_description if new_description else None,
            source_language=new_source_language,
            target_language=new_target_language
        )
        
        if result:
            print(f"Successfully updated book: {new_title}")
        else:
            print("Failed to update book information.")

    def _delete_book(self, book_id):
        """Delete a book and all its chapters"""
        book = self.entity_manager.get_book(book_id=book_id)
        
        if not book:
            print(f"Book with ID {book_id} not found.")
            return
        
        # Get chapters count
        chapters = self.entity_manager.list_chapters(book_id)
        
        # Confirm deletion
        if self.has_rich_ui:
            confirm = self.questionary.confirm(
                f"Are you sure you want to delete '{book['title']}' with {len(chapters)} chapter(s)? This cannot be undone."
            ).ask()
        else:
            confirm = input(f"Are you sure you want to delete '{book['title']}' with {len(chapters)} chapter(s)? This cannot be undone. (y/n): ")
            confirm = confirm.lower() == 'y'
        
        if not confirm:
            print("Operation cancelled.")
            return
        
        result = self.entity_manager.delete_book(book_id)
        
        if result:
            print(f"Successfully deleted book: {book['title']} (ID: {book_id}) and all its chapters.")
        else:
            print(f"Failed to delete book: {book['title']} (ID: {book_id}).")

    def _list_chapters(self, book_id):
        """List all chapters for a book"""
        book = self.entity_manager.get_book(book_id=book_id)
        
        if not book:
            print(f"Book with ID {book_id} not found.")
            return
        
        chapters = self.entity_manager.list_chapters(book_id)
        
        if not chapters:
            print(f"No chapters found for book: {book['title']}")
            return
        
        print(f"Chapters for '{book['title']}' (ID: {book_id}):\n")
        
        for chapter in chapters:
            print(f"Chapter {chapter['chapter']}: {chapter['title']}")
            print(f"  ID: {chapter['id']}")
            print(f"  Translated: {chapter['translation_date']}")
            print(f"  Model: {chapter['model']}")
            print()

    def _get_chapter(self, book_id, chapter_number, format="text"):
        """Get and display a specific chapter"""
        chapter = self.entity_manager.get_chapter(book_id=book_id, chapter_number=chapter_number)
        
        if not chapter:
            print(f"Chapter {chapter_number} not found for book ID {book_id}.")
            return
        
        # Initialize OutputFormatter if needed
        if not hasattr(self, 'output_formatter'):
            self.output_formatter = OutputFormatter(self.entity_manager.config, self.logger)
        
        # Set format
        self.output_format = format
        
        # Get book info if needed for EPUB
        if format == 'epub':
            book = self.entity_manager.get_book(book_id=book_id)
            self.book_info = {
                "title": book["title"],
                "author": book["author"] or "Translator",
                "language": book["language"],
                "description": book["description"] or ""
            }
        else:
            self.book_info = None
        
        # Format and display
        output_path = self.output_formatter.save_output(
            chapter, 
            format=format,
            book_info=self.book_info
        )
        
        print(f"Chapter {chapter_number}: {chapter['title']}")
        print(f"Exported in {format.upper()} format to: {output_path}")

    def _delete_chapter(self, book_id, chapter_number):
        """Delete a specific chapter"""
        # Get chapter info first
        chapter = self.entity_manager.get_chapter(book_id=book_id, chapter_number=chapter_number)
        
        if not chapter:
            print(f"Chapter {chapter_number} not found for book ID {book_id}.")
            return
        
        # Confirm deletion
        if self.has_rich_ui:
            confirm = self.questionary.confirm(
                f"Are you sure you want to delete Chapter {chapter_number}: '{chapter['title']}'? This cannot be undone."
            ).ask()
        else:
            confirm = input(f"Are you sure you want to delete Chapter {chapter_number}: '{chapter['title']}'? This cannot be undone. (y/n): ")
            confirm = confirm.lower() == 'y'
        
        if not confirm:
            print("Operation cancelled.")
            return
        
        result = self.entity_manager.delete_chapter(book_id=book_id, chapter_number=chapter_number)
        
        if result:
            print(f"Successfully deleted Chapter {chapter_number}: '{chapter['title']}'.")
        else:
            print(f"Failed to delete chapter.")

    def _edit_chapter_translation(self, book_id, chapter_number):
        """Edit the translation of a chapter using the system editor"""
        # Get the chapter
        chapter = self.entity_manager.get_chapter(book_id=book_id, chapter_number=chapter_number)

        if not chapter:
            print(f"Chapter {chapter_number} not found for book ID {book_id}.")
            return

        # Get the book info for display
        book = self.entity_manager.get_book(book_id=book_id)

        # Get translated content (stored as a list)
        translated_content = chapter.get('content', [])

        # Convert to string for editing
        if isinstance(translated_content, list):
            content_text = '\n'.join(translated_content)
        else:
            content_text = str(translated_content)

        # Add header with instructions
        content_with_instructions = (
            f"# Editing translation for: {book['title']} - Chapter {chapter_number}: {chapter['title']}\n"
            f"# Save and exit when done\n"
            f"# Lines starting with '#' will be removed automatically\n\n"
            + content_text
        )

        # Open editor
        edited_content = self.edit_text_with_system_editor(content_with_instructions)

        # Remove instruction comments
        edited_lines = [
            line for line in edited_content.split("\n")
            if not line.strip().startswith("# ")
        ]

        # Save the edited translation back to the database
        result = self.entity_manager.save_chapter(
            book_id=book_id,
            chapter_number=chapter_number,
            title=chapter['title'],
            untranslated_content=chapter['untranslated'],
            translated_content=edited_lines,
            summary=chapter.get('summary'),
            translation_model=chapter.get('model')
        )

        if result:
            print(f"Successfully updated translation for Chapter {chapter_number}: '{chapter['title']}'.")
        else:
            print("Failed to update chapter translation.")

    def _export_book(self, book_id, format="text"):
        """Export all chapters of a book to the specified format"""
        book = self.entity_manager.get_book(book_id=book_id)
        
        if not book:
            print(f"Book with ID {book_id} not found.")
            return
        
        chapters = self.entity_manager.list_chapters(book_id)
        
        if not chapters:
            print(f"No chapters found for book: {book['title']}")
            return
        
        print(f"Exporting {len(chapters)} chapters from '{book['title']}' to {format.upper()} format...")
        
        # Initialize OutputFormatter if needed
        if not hasattr(self, 'output_formatter'):
            self.output_formatter = OutputFormatter(self.entity_manager.config, self.logger)
        
        # Set format
        self.output_format = format
        
        # Create book-specific output directory
        book_dir = os.path.join(self.entity_manager.config.script_dir, "output", self.output_formatter._clean_filename(book['title']))
        if not os.path.exists(book_dir):
            os.makedirs(book_dir)
        
        # For EPUB, prepare book info and only process once
        if format == 'epub':
            # Prepare book info
            self.book_info = {
                "title": book["title"],
                "author": book["author"] or "Translator",
                "language": book["language"],
                "description": book["description"] or ""
            }
            
            # Create list of chapters
            all_chapters = []
            for chapter_info in sorted(chapters, key=lambda x: x['chapter']):
                chapter_data = self.entity_manager.get_chapter(chapter_id=chapter_info['id'])
                if chapter_data:
                    all_chapters.append(chapter_data)
            
            # Export as a single EPUB
            if all_chapters:
                # For EPUB, pass all chapters to a special method
                output_path = self.output_formatter.save_book_as_epub(all_chapters, self.book_info)
                print(f"Exported book to: {output_path}")
            else:
                print("No chapter data found to export.")
        else:
            # For other formats, process each chapter individually
            for chapter_info in sorted(chapters, key=lambda x: x['chapter']):
                chapter_data = self.entity_manager.get_chapter(chapter_id=chapter_info['id'])
                if not chapter_data:
                    print(f"Error retrieving chapter {chapter_info['chapter']}")
                    continue
                
                # Set chapter-specific output path
                chapter_filename = f"chapter_{chapter_data['chapter']:03d}_{self.output_formatter._clean_filename(chapter_data['title'])}"
                output_path = os.path.join(book_dir, f"{chapter_filename}.{format}")
                
                # Format and save
                result_path = self.output_formatter.save_output(
                    chapter_data, 
                    format=format,
                    book_info=None,
                    output_path=output_path
                )
                
                print(f"Exported Chapter {chapter_data['chapter']}: {chapter_data['title']} to {result_path}")
            
            print(f"\nAll chapters exported to directory: {book_dir}")

    def _retranslate_chapter(self, book_id, chapter_number):
        """Retrieve and retranslate a specific chapter"""
        # Get the chapter from the database
        chapter = self.entity_manager.get_chapter(book_id=book_id, chapter_number=chapter_number)

        if not chapter:
            print(f"Chapter {chapter_number} not found for book ID {book_id}")
            exit(1)

        print(f"Retranslating chapter {chapter_number}...")
        print(f"Note: Original chapter will be replaced only if retranslation succeeds")

        # Get the untranslated content
        # The chapter will be automatically updated by save_chapter() if translation succeeds
        if isinstance(chapter['untranslated'], list):
            return chapter['untranslated']
        else:
            # If it's a string, split it into lines
            return chapter['untranslated'].split('\n')
        
    
    # Book specific prompt template private methods

    def _edit_prompt_template(self, book_id):
        """Edit the prompt template for a book using the system editor"""
        book = self.entity_manager.get_book(book_id=book_id)
        
        if not book:
            print(f"Book with ID {book_id} not found.")
            return
        
        # Get current template
        current_template = self.entity_manager.get_book_prompt_template(book_id)
        
        if not current_template:
            # Export default template with placeholder
            entities_json = {
                "characters": {},
                "places": {},
                "organizations": {},
                "abilities": {},
                "titles": {},
                "equipment": {}
            }
            
            # Get the default template
            default_template = self.translator.generate_system_prompt([], entities_json, do_count=False)
            
            # Replace with placeholder
            current_template = default_template.replace(
                json.dumps(entities_json, ensure_ascii=False, indent=4),
                "{{ENTITIES_JSON}}"
            )
        
        # Add header with instructions
        template_with_instructions = (
            "# Edit this prompt template for book: " + book['title'] + "\n"
            "# Make sure to keep the {{ENTITIES_JSON}} placeholder where you want entities to appear\n"
            "# Save and exit when done\n\n"
            + current_template
        )
        
        # Open editor
        edited_template = self.edit_text_with_system_editor(template_with_instructions)
        
        # Remove instruction comments
        edited_template = "\n".join([
            line for line in edited_template.split("\n") 
            if not line.strip().startswith("# ")
        ])
        
        # Check for placeholder
        if "{{ENTITIES_JSON}}" not in edited_template:
            print("Error: Prompt template must contain the {{ENTITIES_JSON}} placeholder.")
            return
        
        # Save the edited template
        result = self.entity_manager.set_book_prompt_template(book_id, edited_template)
        
        if result:
            print(f"Successfully updated prompt template for '{book['title']}' (ID: {book_id}).")
        else:
            print("Failed to update prompt template.")

    def _show_prompt_template(self, book_id):
        """Show the current prompt template for a book"""
        book = self.entity_manager.get_book(book_id=book_id)
        
        if not book:
            print(f"Book with ID {book_id} not found.")
            return
        
        template = self.entity_manager.get_book_prompt_template(book_id)
        
        print(f"Prompt template for '{book['title']}' (ID: {book_id}):")
        
        if template:
            print("\n" + template)
        else:
            print("This book is using the default prompt template.")
            print("You can set a custom template with --set-prompt-template and --prompt-file")

    def _set_prompt_template(self, book_id, prompt_file):
        """Set a custom prompt template for a book"""
        book = self.entity_manager.get_book(book_id=book_id)
        
        if not book:
            print(f"Book with ID {book_id} not found.")
            return
        
        try:
            with open(prompt_file, 'r', encoding='utf-8') as f:
                template = f.read()
            
            # Check if the template contains the required placeholder
            if "{{ENTITIES_JSON}}" not in template:
                print("Error: Prompt template must contain the {{ENTITIES_JSON}} placeholder.")
                print("This is where the entities dictionary will be inserted.")
                return
            
            result = self.entity_manager.set_book_prompt_template(book_id, template)
            
            if result:
                print(f"Successfully set custom prompt template for '{book['title']}' (ID: {book_id}).")
            else:
                print(f"Failed to set prompt template for book.")
        except Exception as e:
            print(f"Error reading prompt file: {e}")

    def _export_default_prompt(self, output_file):
        """Export the default prompt template to a file"""
        # Create a small placeholder entities JSON for the template
        entities_json = {
            "characters": {},
            "places": {},
            "organizations": {},
            "abilities": {},
            "titles": {},
            "equipment": {}
        }
        
        # Get the default template from the translator
        default_template = self.translator.generate_system_prompt([], entities_json, do_count=False)
        
        # Replace the actual entities JSON with the placeholder
        template_with_placeholder = default_template.replace(
            json.dumps(entities_json, ensure_ascii=False, indent=4),
            "{{ENTITIES_JSON}}"
        )
        
        try:
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(template_with_placeholder)
            
            print(f"Default prompt template exported to {output_file}")
            print("You can modify this file and use it with --set-prompt-template")
        except Exception as e:
            print(f"Error exporting default prompt: {e}")
    
    def _list_providers(self):
        """List all supported model providers and their configurations"""
        try:
            factory = get_factory()
            
            print("Supported Model Providers:")
            print("=" * 50)
            
            for provider_name, config in factory.config['providers'].items():
                print(f"\nProvider: {provider_name}")
                print(f"  Class: {config.get('class', 'Unknown')}")
                
                if 'base_url' in config:
                    print(f"  Base URL: {config['base_url']}")
                
                if 'api_key_env' in config:
                    print(f"  API Key Env: {config['api_key_env']}")
                
                if 'default_model' in config:
                    print(f"  Default Model: {config['default_model']}")
                
                if 'models' in config:
                    print(f"  Available Models: {', '.join(config['models'])}")
            
            # Show aliases
            if 'aliases' in factory.config and factory.config['aliases']:
                print(f"\nAliases:")
                for alias, target in factory.config['aliases'].items():
                    print(f"  {alias} -> {target}")
            
            print(f"\nExample usage:")
            print(f"  python translator.py --model openai:gpt-4-turbo --file chapter.txt")
            print(f"  python translator.py --model claude:claude-3-5-sonnet-20241022 --file chapter.txt")
            print(f"  python translator.py --model deepseek:deepseek-chat --file chapter.txt")
            
        except Exception as e:
            print(f"Error listing providers: {e}")
            import traceback
            traceback.print_exc()


    def edit_text_with_system_editor(self, initial_text=""):
        """
        Opens the system's default editor to edit text.
        Works cross-platform by detecting the appropriate editor.
        """
        # Create a temporary file
        fd, path = tempfile.mkstemp(suffix=".txt")
        try:
            with os.fdopen(fd, 'w') as f:
                f.write(initial_text)
            
            # Determine which editor to use
            editor = None
            if platform.system() == 'Windows':
                # Try to use notepad or notepad++ on Windows
                editor = 'notepad.exe'
                if os.path.exists("C:\\Program Files\\Notepad++\\notepad++.exe"):
                    editor = "C:\\Program Files\\Notepad++\\notepad++.exe"
                elif os.path.exists("C:\\Program Files (x86)\\Notepad++\\notepad++.exe"):
                    editor = "C:\\Program Files (x86)\\Notepad++\\notepad++.exe"
            else:
                # Use environment variables on Unix-like systems
                editor = os.environ.get('EDITOR', 'vi')
            
            # Show instructions
            print(f"Opening editor ({editor}) to edit the text.")
            print("Save the file and exit the editor when you're done.")
            
            # Launch the editor
            if platform.system() == 'Windows':
                subprocess.call([editor, path])
            else:
                subprocess.call([editor, path])
            
            # Read the edited content
            with open(path, 'r') as f:
                return f.read()
                
        finally:
            # Clean up the temporary file
            try:
                os.unlink(path)
            except:
                pass

    def file_to_array(self, filename):
        """Convert a file to an array of lines"""
        with open(filename, 'r', encoding='utf-8') as file:
            lines = file.readlines()
        # Strip newline characters
        return [line.strip() for line in lines]
    
    def display_current_data(self, data):
        """Prints the current data structure to the console."""
        print("\nTotally New Entities In This Chapter:")
        if self.has_rich_ui:
            self.print_json(data=data)
        else:
            print(json.dumps(data, indent=4, ensure_ascii=False))
    
    def _filter_existing_entities(self, data: Dict):
        """
        Filter out entities that already exist in the database for this book or as global entities.
        This modifies the data dictionary in-place.

        Args:
            data: Dict of new entities by category (from translation)

        Returns:
            Number of entities filtered out
        """
        import sqlite3

        # Collect all untranslated keys to check
        all_untranslated = set()
        for category, entities in data.items():
            all_untranslated.update(entities.keys())

        if not all_untranslated:
            return 0

        # Get current book_id (if available)
        current_book_id = getattr(self, 'book_id', None)

        # Query database to find which entities already exist for this book or globally
        existing_entities = {}
        try:
            conn = sqlite3.connect(self.entity_manager.db_path)
            cursor = conn.cursor()

            for untranslated in all_untranslated:
                # Check if entity exists with matching book_id OR book_id is NULL (global)
                if current_book_id is not None:
                    cursor.execute('''
                    SELECT category, translation FROM entities
                    WHERE untranslated = ? AND (book_id = ? OR book_id IS NULL)
                    LIMIT 1
                    ''', (untranslated, current_book_id))
                else:
                    # If no book_id, just check for global entities
                    cursor.execute('''
                    SELECT category, translation FROM entities
                    WHERE untranslated = ? AND book_id IS NULL
                    LIMIT 1
                    ''', (untranslated,))

                row = cursor.fetchone()
                if row:
                    existing_entities[untranslated] = {
                        'category': row[0],
                        'translation': row[1]
                    }

            conn.close()

        except sqlite3.Error as e:
            self.logger.error(f"Error checking existing entities: {e}")
            return 0

        if not existing_entities:
            return 0

        # Show what's being filtered out
        print(f"\n📋 Filtering out {len(existing_entities)} entities that already exist in database:")
        for i, (untranslated, info) in enumerate(sorted(existing_entities.items())[:10]):
            print(f"  • {untranslated} → {info['translation']} (already in {info['category']})")
        if len(existing_entities) > 10:
            print(f"  ... and {len(existing_entities) - 10} more")

        # Remove existing entities from data structure
        filtered_count = 0
        for category, entities in data.items():
            for untranslated in list(entities.keys()):
                if untranslated in existing_entities:
                    del entities[untranslated]
                    filtered_count += 1

        print(f"✅ Filtered out {filtered_count} existing entities.\n")
        return filtered_count

    def _auto_clean_new_entities(self, data: Dict):
        """
        Automatically clean non-proper noun entities from new entity data before review.
        This modifies the data dictionary in-place.

        Args:
            data: Dict of new entities by category (from translation)

        Returns:
            Number of entities deleted
        """
        # Build entity dict for classification
        entity_dict = {}

        for category, entities in data.items():
            for untranslated, entity_data in entities.items():
                translated = entity_data.get('translation', '')
                entity_dict[untranslated] = translated

        if not entity_dict:
            return 0

        # Count entities
        initial_count = len(entity_dict)
        print(f"\n🧹 Auto-cleaning {initial_count} new entities...")

        # Classify proper nouns
        proper_nouns = self._classify_proper_nouns(entity_dict)

        if proper_nouns is None:
            # Classification failed, skip cleanup
            return 0

        # Identify entities to delete
        to_delete_keys = [k for k in entity_dict.keys() if k not in proper_nouns]

        if not to_delete_keys:
            print("✅ All new entities are proper nouns. No cleanup needed.")
            return 0

        print(f"\n📊 Classification Results:")
        print(f"  ✅ Proper nouns: {len(proper_nouns)}")
        print(f"  ❌ Generic terms to remove: {len(to_delete_keys)}")

        # Show sample of what will be deleted
        print(f"\n📋 Removing generic terms:")
        for i, untranslated in enumerate(sorted(to_delete_keys)[:10]):
            translated = entity_dict[untranslated]
            print(f"  • {untranslated} → {translated}")
        if len(to_delete_keys) > 10:
            print(f"  ... and {len(to_delete_keys) - 10} more")

        # Delete entities from data structure in-place, recording removed translations
        deleted_count = 0
        self._cleaned_translations = {}
        for category, entities in data.items():
            for untranslated in list(entities.keys()):  # Use list() to avoid modification during iteration
                if untranslated in to_delete_keys:
                    translation = entities[untranslated].get('translation', '')
                    if translation:
                        self._cleaned_translations[untranslated] = translation
                    del entities[untranslated]
                    deleted_count += 1

        print(f"\n✅ Removed {deleted_count} generic terms from review.\n")
        return deleted_count

    def _decase_cleaned_entities(self, text: List[str]) -> List[str]:
        """
        Lowercase the English translations of entities that were removed by auto-clean.
        Skips occurrences that appear at a sentence start (preceded by .!? or newline).
        text is a list of paragraph strings, matching the shape of end_object['content'].
        """
        cleaned = getattr(self, '_cleaned_translations', {})
        if not cleaned:
            return text

        def make_replacer(paragraph, lower):
            def replacer(match):
                preceding = paragraph[max(0, match.start() - 2):match.start()]
                if re.search(r'[.!?\n]\s?$', preceding):
                    return match.group(0)  # sentence start — leave capitalised
                return lower
            return replacer

        for untranslated, translation in cleaned.items():
            if not translation:
                continue
            lower = translation[0].lower() + translation[1:]
            if lower == translation:
                continue  # already lowercase, nothing to do

            pattern = re.compile(r'\b' + re.escape(translation) + r'\b')
            for i in range(len(text)):
                text[i] = re.sub(pattern, make_replacer(text[i], lower), text[i])

        return text

    def _fix_partial_translations(self, content: List[str]) -> List[str]:
        """
        Detect lines containing untranslated CJK characters and fix them
        using the cleaning model. Batches all affected lines into a single
        API call, returning the content list with fixed lines spliced back in.
        """
        cjk_pattern = re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]')
        affected_indices = [i for i, line in enumerate(content) if cjk_pattern.search(line)]

        if not affected_indices:
            return content

        print(f"\n🔧 Found {len(affected_indices)} partially translated line(s) containing CJK characters:")
        for i in affected_indices:
            preview = content[i][:80] + ('...' if len(content[i]) > 80 else '')
            print(f"  Line {i}: {preview}")

        lines_to_fix = [content[i] for i in affected_indices]

        system_prompt = (
            "You are a translation repair assistant. "
            "You will receive a JSON array of English sentences that each contain one or more "
            "untranslated Chinese characters or words. For each sentence, translate the Chinese "
            "fragments into English in context, preserving all surrounding English text exactly. "
            "Return only a JSON array of the repaired sentences in the same order, with no "
            "explanation or markdown."
        )
        user_prompt = json.dumps(lines_to_fix, ensure_ascii=False, indent=2)

        try:
            from providers import create_provider
            from config import TranslationConfig
            config = TranslationConfig()

            if hasattr(self, 'cleaning_model') and self.cleaning_model:
                model_spec = self.cleaning_model
            else:
                model_spec = config.translation_model

            provider_name, model_name = config.parse_model_spec(model_spec)
            provider = create_provider(provider_name)

            print(f"🤖 Repairing with {model_name}...")

            response = provider.chat_completion(
                model=model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.0
            )

            raw = provider.get_response_content(response).strip()

            # Strip markdown code fences if present
            if raw.startswith("```"):
                lines = raw.split("\n")
                raw = "\n".join(lines[1:-1]) if len(lines) > 2 else raw
                if raw.startswith("json"):
                    raw = raw[4:].strip()

            fixed_lines = json.loads(raw)

            if not isinstance(fixed_lines, list) or len(fixed_lines) != len(affected_indices):
                raise ValueError(f"Expected {len(affected_indices)} fixed lines, got {len(fixed_lines) if isinstance(fixed_lines, list) else type(fixed_lines)}")

            result = list(content)
            for idx, fixed in zip(affected_indices, fixed_lines):
                result[idx] = fixed

            print(f"✅ Repaired {len(affected_indices)} partially translated line(s).")
            return result

        except Exception as e:
            print(f"⚠️  Could not repair partial translations: {e}")
            print("Continuing with partially translated content.")
            return content

    def review_entities(self, data, untranslated_text=[]):
        """
        Using questionary to display interactive prompts.
        Returns a dictionary of edited data.
        """
        # Check if there are any entities to review
        has_entities = any(data.get(category, {}) for category in data)

        if has_entities and not self.no_review and not getattr(self, 'silent_notifications', False):
            # Play notification sound when new entities are found (unless silenced or no_review mode)
            self.play_notification_sound()

        # First, filter out entities that already exist in the database
        if has_entities:
            filtered_count = self._filter_existing_entities(data)
            if filtered_count > 0:
                # Recheck if there are any entities left after filtering
                has_entities = any(data.get(category, {}) for category in data)
                if not has_entities:
                    print("✅ All new entities already exist in database.")
                    return {}

        # Auto-clean non-proper nouns before review (unless disabled)
        if has_entities and not getattr(self, 'no_clean', False):
            cleaned_count = self._auto_clean_new_entities(data)
            if cleaned_count > 0:
                # Recheck if there are any entities left after cleaning
                has_entities = any(data.get(category, {}) for category in data)
                if not has_entities:
                    print("✅ All new entities were generic terms and have been auto-cleaned.")
                    return {}

        # Now check if we should skip the interactive review
        if self.no_review:
            print("Review disabled, skipping entity review.")
            return {}
        if not self.has_rich_ui:
            print("Rich UI components not available. Skipping entity review.")
            return {}

        edited_data = {'characters': {}, 'places': {}, 'organizations': {}, 'abilities': {}, 'titles': {}, 'equipment': {}, 'creatures': {}}
        categories = ['characters', 'places', 'organizations', 'abilities', 'titles', 'equipment', 'creatures']
        
        while True:
            # 1. Display current data
            self.display_current_data(data)
            
            # 2. Ask if user wants to make changes (yes/no)
            make_changes = self.questionary.confirm(
                "Do you want to make any changes?"
            ).ask()  # returns True/False
            
            if not make_changes:
                break
            
            # 3. Select a category
            if not data:
                print("No categories available.")
                break
            
            # Filter out empty categories
            non_empty_categories = [category for category in data if data[category]]
            # Let the user pick from a list, or choose "Exit"
            category_choice = self.questionary.select(
                "Select a category to edit:",
                choices=non_empty_categories + ["Exit"]
            ).ask()
            
            if category_choice == "Exit":
                break
            
            selected_category = category_choice
            
            # 4. Select an item within that category
            items = data[selected_category]
            if not items:
                print(f"No items in category '{selected_category}'.")
                continue
            
            # Build a list of questionary Choices, each with a title like "Frodo (Фродо)"
            # but the underlying value is just "Frodo".
            item_choices = []
            for key, item_data in items.items():
                translation = item_data.get("translation", "")
                display_title = f"{key} ({translation})" if translation else key
                item_choices.append(self.questionary.Choice(title=display_title, value=key))
            
            # Add a "Back" option
            item_choices.append("Back")
            
            item_choice = self.questionary.select(
                f"Select an item in '{selected_category}' to manage:",
                choices=item_choices
            ).ask()
            
            if item_choice == "Back":
                continue
            
            selected_item_key = item_choice
            selected_item = items[selected_item_key]
            
            # 5. Ask what the user wants to do with this item
            action = self.questionary.select(
                f"What do you want to do with '{selected_item_key}'?",
                choices=["Edit item", "Delete item", "Change category", "Go back"]
            ).ask()
            
            if action == "Go back":
                continue
            
            if action == "Delete item":
                del data[selected_category][selected_item_key]
                print(f"Item '{selected_item_key}' deleted.")
                edited_data[selected_category][selected_item_key] = {"deleted": True}
                
                # Also delete from SQLite database
                self.entity_manager.delete_entity(selected_category, selected_item_key)
                continue
            
            if action == "Change category":
                # Prompt for the new category, excluding empty categories and the current category
                non_empty_categories = [
                    cat for cat in categories
                    if cat != selected_category
                ]
                new_category = self.questionary.select(
                    "Select the new category to move this item:",
                    choices=non_empty_categories
                ).ask()
                
                if not new_category:
                    # user canceled or ctrl-c
                    continue
                
                # Remove from old category
                del data[selected_category][selected_item_key]
                edited_data[selected_category][selected_item_key] = {"deleted": True}
                # Move/add to new category (create if doesn't exist)
                data.setdefault(new_category, {})
                data[new_category][selected_item_key] = selected_item
                
                # Record the change
                edited_data.setdefault(new_category, {})
                edited_data[new_category][selected_item_key] = selected_item
                
                # Update category in SQLite database
                self.entity_manager.change_entity_category(selected_category, selected_item_key, new_category)
                
                print(f"Moved '{selected_item_key}' from '{selected_category}' to '{new_category}'.")
                continue
            
            # If user chose "Edit item"
            print(f"\nEditing item: {selected_item_key}")
            was_item_edited = False
            
            # For each field in the item
            for field, value in list(selected_item.items()):
                if field == "translation":
                    # Ask if user wants LLM translation
                    wants_llm = self.questionary.confirm(
                        f"Do you want to ask the LLM for translation options for '{selected_item_key}'?"
                    ).ask()
                    
                    if wants_llm:
                        node = selected_item.copy()
                        node['category'] = selected_category
                        node['untranslated'] = selected_item_key
                        advice = self.translator.get_translation_options(node, untranslated_text)
                        
                        print("\nLLM says:")
                        print(f"  \"{advice['message']}\"\n")
                        
                        # Add an extra "Custom" option
                        translation_options = advice['options'] + ["Custom Translation [Your Input]", "Skip"]
                        
                        # Display translations as a list
                        chosen_translation = self.questionary.select(
                            "Choose a translation option:",
                            choices=translation_options
                        ).ask()
                        
                        if chosen_translation == "Skip":
                            pass
                        elif chosen_translation == "Custom Translation [Your Input]":
                            # user types a custom translation
                            custom_val = self.questionary.text(
                                "Enter your custom translation (press Enter to cancel):"
                            ).ask()
                            if custom_val:
                                # Check if this translation already exists
                                existing = self.entity_manager.get_entity_by_translation(custom_val)
                                if existing and existing[1] != selected_item_key:
                                    # Show a warning
                                    existing_category, existing_key, _ = existing
                                    print(f"Warning: This translation is already used for '{existing_key}' in '{existing_category}'")
                                    proceed = self.questionary.confirm(
                                        "Do you want to proceed with this translation anyway?"
                                    ).ask()
                                    
                                    if not proceed:
                                        continue
                                
                                selected_item[field] = custom_val
                                selected_item["incorrect_translation"] = value
                                was_item_edited = True
                        else:
                            # Check if this translation already exists
                            existing = self.entity_manager.get_entity_by_translation(chosen_translation)
                            if existing and existing[1] != selected_item_key:
                                # Show a warning
                                existing_category, existing_key, _ = existing
                                print(f"Warning: This translation is already used for '{existing_key}' in '{existing_category}'")
                                proceed = self.questionary.confirm(
                                    "Do you want to proceed with this translation anyway?"
                                ).ask()
                                
                                if not proceed:
                                    continue
                            
                            # user picked one of the suggested translations
                            selected_item["incorrect_translation"] = value
                            selected_item[field] = chosen_translation
                            was_item_edited = True
                    
                    else:
                        # Simply prompt for updating the value
                        new_val = self.questionary.text(
                            f"{field} (current: {value}). Press Enter to keep, or type new value:",
                            default=""
                        ).ask()
                        if new_val:
                            # Check if this translation already exists
                            existing = self.entity_manager.get_entity_by_translation(new_val)
                            if existing and existing[1] != selected_item_key:
                                # Show a warning
                                existing_category, existing_key, _ = existing
                                print(f"Warning: This translation is already used for '{existing_key}' in '{existing_category}'")
                                proceed = self.questionary.confirm(
                                    "Do you want to proceed with this translation anyway?"
                                ).ask()
                                
                                if not proceed:
                                    continue
                            
                            self.logger.debug(f"selected_item_key = {selected_item_key}")
                            self.logger.debug(f"selected_item = {selected_item}")
                            selected_item[field] = new_val
                            selected_item["incorrect_translation"] = value
                            was_item_edited = True
                
                else:
                    # For non-translation fields
                    new_val = self.questionary.text(
                        f"{field} (current: {value}). Press Enter to keep, or type new value:",
                        default=""
                    ).ask()
                    
                    if new_val:
                        # If it's an int field, try to convert
                        if isinstance(value, int):
                            try:
                                selected_item[field] = int(new_val)
                            except ValueError:
                                print(f"Invalid input for {field}. Keeping original value.")
                                continue
                        else:
                            selected_item[field] = new_val
                        was_item_edited = True
            
            # Save item changes if it was edited
            if was_item_edited:
                self.logger.debug(f"selected_item_key = {selected_item_key}")
                self.logger.debug(f"selected_item = {selected_item}")
                self.logger.debug("Final data: %s", data)
                data[selected_category][selected_item_key] = selected_item
                edited_data.setdefault(selected_category, {})
                edited_data[selected_category][selected_item_key] = selected_item
                
                # Update entity in SQLite database
                translation = selected_item.get("translation", "")
                last_chapter = selected_item.get("last_chapter", "")
                incorrect_translation = selected_item.get("incorrect_translation", None)
                gender = selected_item.get("gender", None)
                book_id = selected_item.get("book_id", None)

                self.entity_manager.update_entity(
                    selected_category,
                    selected_item_key,
                    translation=translation,
                    last_chapter=last_chapter,
                    incorrect_translation=incorrect_translation,
                    gender=gender,
                    book_id=book_id
                )
        
        return edited_data

    def _classify_proper_nouns(self, entities: Dict[str, str], model_spec: str = None):
        """
        Send entities to AI model to classify which are proper nouns.

        Args:
            entities: Dictionary of untranslated:translated entities
            model_spec: Optional model spec (provider:model). Uses TRANSLATION_MODEL if not specified.

        Returns:
            Set of untranslated entity keys that are proper nouns, or None if classification fails
        """
        from providers import create_provider
        from config import TranslationConfig

        # Load the cleaning prompt from file
        config = TranslationConfig()
        cleaning_prompt_path = os.path.join(config.script_dir, "cleaning_prompt.txt")

        try:
            if os.path.exists(cleaning_prompt_path):
                with open(cleaning_prompt_path, 'r', encoding='utf-8') as file:
                    system_prompt = file.read()
            else:
                print(f"Error: cleaning_prompt.txt not found at {cleaning_prompt_path}")
                print("Please ensure cleaning_prompt.txt exists in the script directory.")
                return None
        except Exception as e:
            print(f"Error loading cleaning prompt from file: {e}")
            print("Please check that cleaning_prompt.txt is readable and properly formatted.")
            return None

        user_prompt = f"""Classify which of these entities are proper nouns. Return only a JSON array of the Chinese keys (untranslated text) for entries that are proper nouns:

{json.dumps(entities, ensure_ascii=False, indent=2)}

Return format: ["key1", "key2", ...]
"""

        try:
            # Determine which model to use for cleaning
            # Priority: 1. Passed model_spec, 2. self.cleaning_model, 3. Translation model
            if model_spec is None:
                if hasattr(self, 'cleaning_model') and self.cleaning_model:
                    model_spec = self.cleaning_model
                else:
                    model_spec = config.translation_model

            # Parse the model spec to get provider and model name
            provider_name, model = config.parse_model_spec(model_spec)

            # Create provider instance
            provider = create_provider(provider_name)

            print(f"\n🤖 Analyzing {len(entities)} entities with {model}...")

            # Make API call
            response = provider.chat_completion(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.0  # Use deterministic output
            )

            # Extract response content
            content = provider.get_response_content(response)

            # Parse JSON response
            content = content.strip()

            # Handle markdown code blocks
            if content.startswith("```"):
                lines = content.split("\n")
                content = "\n".join(lines[1:-1]) if len(lines) > 2 else content
                if content.startswith("json"):
                    content = content[4:].strip()

            proper_nouns = json.loads(content)

            if not isinstance(proper_nouns, list):
                raise ValueError("Response is not a JSON array")

            return set(proper_nouns)

        except Exception as e:
            print(f"\n⚠️  Error during AI classification: {e}")
            print("Skipping automatic cleanup. You can manually review entities.")
            return None

    def _review_all_entities(self, book_id=None, category_filter=None):
        """
        Review all entities in the database interactively.

        Args:
            book_id: Optional book ID to filter entities
            category_filter: Optional category to filter entities
        """
        # Load all entities from database
        all_entities = self.entity_manager.get_all_entities_for_review(
            book_id=book_id,
            category=category_filter
        )

        # Count total entities
        total_count = sum(len(entities) for entities in all_entities.values())

        if total_count == 0:
            print("No entities found in the database.")
            if book_id:
                print(f"(Filtered by book_id={book_id})")
            if category_filter:
                print(f"(Filtered by category={category_filter})")
            return

        # Display filter info
        print(f"\n=== Entity Review ===")
        print(f"Total entities: {total_count}")
        if book_id:
            book = self.entity_manager.get_book(book_id=book_id)
            if book:
                print(f"Filtered by book: {book.get('title', 'Unknown')} (ID: {book_id})")
        if category_filter:
            print(f"Filtered by category: {category_filter}")
        print()

        # Main menu loop
        while True:
            choice = self.questionary.select(
                "What would you like to do?",
                choices=[
                    "Browse by Category",
                    "Search Entities",
                    "Add New Entity",
                    "Exit"
                ]
            ).ask()

            if choice == "Exit":
                print("Exiting entity review.")
                break

            # Get selected entity
            selected_entity = None
            if choice == "Browse by Category":
                selected_entity = self._browse_entities_by_category(all_entities)
            elif choice == "Search Entities":
                results = self._search_entities(all_entities)
                if results:
                    selected_entity = self._display_search_results(results)
            elif choice == "Add New Entity":
                self._add_new_entity(all_entities, book_id)

            # If an entity was selected, show action menu
            if selected_entity:
                category, untranslated, entity_data = selected_entity
                self._perform_entity_action(category, untranslated, entity_data, all_entities)

    def _browse_entities_by_category(self, all_entities):
        """
        Browse entities by category.

        Args:
            all_entities: Dict of all entities by category

        Returns:
            Tuple of (category, untranslated, entity_data) or None
        """
        # Filter out empty categories
        non_empty_categories = [
            cat for cat in all_entities
            if all_entities[cat]
        ]

        if not non_empty_categories:
            print("No entities available.")
            return None

        # Select category
        category_choices = non_empty_categories + ["Back"]
        category = self.questionary.select(
            "Select a category:",
            choices=category_choices
        ).ask()

        if category == "Back":
            return None

        # Get entities in this category
        entities = all_entities[category]
        if not entities:
            print(f"No entities in category '{category}'.")
            return None

        # Convert to list and sort alphabetically by translation
        entity_list = []
        for untranslated, entity_data in entities.items():
            entity_list.append((category, untranslated, entity_data))

        # Sort by translation (English first)
        entity_list.sort(key=lambda x: x[2].get("translation", "").lower())

        # Use pagination if there are many entities
        if len(entity_list) > 20:
            return self._paginate_entity_list(entity_list)
        else:
            # Display all entities in this category
            choices = []
            for category_item, untranslated, entity_data in entity_list:
                translation = entity_data.get("translation", "")
                book_id_display = entity_data.get("book_id", "Global")
                display = f"{translation} ({untranslated}) - [{category_item}] (Book: {book_id_display})"
                choices.append(self.questionary.Choice(
                    title=display,
                    value=(category_item, untranslated, entity_data)
                ))

            choices.append("Back")

            selected = self.questionary.select(
                f"Select an entity in '{category}':",
                choices=choices
            ).ask()

            if selected == "Back":
                return None

            return selected

    def _search_entities(self, all_entities):
        """
        Search entities by untranslated or translated text.
        Supports wildcards (*) and regex patterns.

        Args:
            all_entities: Dict of all entities by category

        Returns:
            List of tuples: (category, untranslated, entity_data)
        """
        search_type = self.questionary.select(
            "Search by:",
            choices=["Untranslated text", "Translation", "Both", "Back"]
        ).ask()

        if search_type == "Back":
            return []

        search_term = self.questionary.text(
            "Enter search term (supports * wildcards or regex, case-insensitive):"
        ).ask()

        if not search_term:
            return []

        # Convert glob pattern to regex if it contains wildcards
        import re
        if '*' in search_term:
            # Convert glob wildcards to regex
            regex_pattern = search_term.replace('*', '.*')
            # Escape other special regex characters except .*
            regex_pattern = re.escape(regex_pattern).replace(r'\.\*', '.*')
        else:
            # Try to use as regex, fall back to literal search
            regex_pattern = search_term

        try:
            pattern = re.compile(regex_pattern, re.IGNORECASE)
            use_regex = True
        except re.error:
            # If regex is invalid, fall back to simple substring search
            use_regex = False
            search_term_lower = search_term.lower()

        results = []

        for category, entities in all_entities.items():
            for untranslated, entity_data in entities.items():
                translation = entity_data.get("translation", "")

                match = False
                if use_regex:
                    if search_type in ["Untranslated text", "Both"]:
                        if pattern.search(untranslated):
                            match = True
                    if search_type in ["Translation", "Both"]:
                        if pattern.search(translation):
                            match = True
                else:
                    if search_type in ["Untranslated text", "Both"]:
                        if search_term_lower in untranslated.lower():
                            match = True
                    if search_type in ["Translation", "Both"]:
                        if search_term_lower in translation.lower():
                            match = True

                if match:
                    results.append((category, untranslated, entity_data))

        return results

    def _display_search_results(self, results):
        """
        Display search results and let user select one.

        Args:
            results: List of (category, untranslated, entity_data) tuples

        Returns:
            Tuple of (category, untranslated, entity_data) or None
        """
        if not results:
            print("No matching entities found.")
            return None

        print(f"\nFound {len(results)} matching entities:\n")

        # Sort results alphabetically by translation
        results_sorted = sorted(results, key=lambda x: x[2].get("translation", "").lower())

        # Use pagination if there are many results
        if len(results_sorted) > 20:
            return self._paginate_entity_list(results_sorted)
        else:
            # Create choices for questionary
            choices = []
            for category, untranslated, entity_data in results_sorted:
                translation = entity_data.get("translation", "")
                book_id_display = entity_data.get("book_id", "Global")
                display = f"{translation} ({untranslated}) - [{category}] (Book: {book_id_display})"
                choices.append(self.questionary.Choice(
                    title=display,
                    value=(category, untranslated, entity_data)
                ))

            choices.append("Back")

            selected = self.questionary.select(
                "Select an entity to review:",
                choices=choices
            ).ask()

            if selected == "Back":
                return None

            return selected

    def _perform_entity_action(self, category, untranslated, entity_data, all_entities):
        """
        Show action menu for a selected entity and perform the chosen action.

        Args:
            category: Entity category
            untranslated: Untranslated text
            entity_data: Entity data dict
            all_entities: All entities dict (for updates)
        """
        while True:
            translation = entity_data.get("translation", "")
            book_id_display = entity_data.get("book_id", "Global")

            print(f"\n=== Entity Details ===")
            print(f"Category: {category}")
            print(f"Untranslated: {untranslated}")
            print(f"Translation: {translation}")
            print(f"Book: {book_id_display}")
            print(f"Last Chapter: {entity_data.get('last_chapter', 'N/A')}")
            if entity_data.get("gender"):
                print(f"Gender: {entity_data.get('gender')}")
            print()

            # Build action choices dynamically based on entity's book status
            action_choices = [
                "Edit Translation",
                "Delete Entity",
                "Change Category",
            ]

            # Add book scope option
            current_book_id = entity_data.get("book_id")
            if current_book_id:
                action_choices.append("Make Global (available for all books)")
            else:
                action_choices.append("Assign to Specific Book")

            action_choices.extend([
                "View Usage Context",
                "Go Back"
            ])

            action = self.questionary.select(
                f"What do you want to do with '{untranslated}'?",
                choices=action_choices
            ).ask()

            if action == "Go Back":
                break

            if action == "Delete Entity":
                confirm = self.questionary.confirm(
                    f"Are you sure you want to delete '{untranslated}'?"
                ).ask()

                if confirm:
                    self.entity_manager.delete_entity(category, untranslated)
                    # Remove from in-memory dict
                    if untranslated in all_entities[category]:
                        del all_entities[category][untranslated]
                    print(f"Entity '{untranslated}' deleted.")
                    break

            elif action == "Change Category":
                categories = ['characters', 'places', 'organizations', 'abilities', 'titles', 'equipment', 'creatures']
                new_category_choices = [cat for cat in categories if cat != category]

                new_category = self.questionary.select(
                    "Select the new category:",
                    choices=new_category_choices + ["Cancel"]
                ).ask()

                if new_category != "Cancel":
                    # Update in database
                    self.entity_manager.change_entity_category(category, untranslated, new_category)

                    # Update in-memory dict
                    if untranslated in all_entities[category]:
                        del all_entities[category][untranslated]
                    all_entities.setdefault(new_category, {})
                    entity_data['category'] = new_category
                    all_entities[new_category][untranslated] = entity_data

                    print(f"Moved '{untranslated}' from '{category}' to '{new_category}'.")
                    category = new_category  # Update for next iteration

            elif action == "Edit Translation":
                # Ask if user wants LLM translation
                wants_llm = self.questionary.confirm(
                    f"Do you want to ask the LLM for translation options for '{untranslated}'?"
                ).ask()

                if wants_llm:
                    node = entity_data.copy()
                    node['category'] = category
                    node['untranslated'] = untranslated
                    advice = self.translator.get_translation_options(node, [])

                    print("\nLLM says:")
                    print(f"  \"{advice['message']}\"\n")

                    if 'options' in advice:
                        options_list = advice['options'] + ["Manual entry"]
                        chosen = self.questionary.select(
                            "Which translation do you want?",
                            choices=options_list
                        ).ask()

                        if chosen == "Manual entry":
                            new_translation = self.questionary.text(
                                f"Enter new translation for '{untranslated}':"
                            ).ask()
                        else:
                            new_translation = chosen
                    else:
                        new_translation = self.questionary.text(
                            f"Enter new translation for '{untranslated}':"
                        ).ask()
                else:
                    new_translation = self.questionary.text(
                        f"Enter new translation for '{untranslated}' (current: {translation}):"
                    ).ask()

                if new_translation and new_translation != translation:
                    # Store old translation as incorrect_translation
                    old_translation = translation
                    entity_data['translation'] = new_translation
                    entity_data['incorrect_translation'] = old_translation

                    # Update in database with both new translation and old translation
                    self.entity_manager.update_entity(
                        category,
                        untranslated,
                        translation=new_translation,
                        incorrect_translation=old_translation
                    )

                    # Update in-memory dict
                    all_entities[category][untranslated] = entity_data

                    print(f"Translation updated: '{untranslated}' → '{new_translation}'")

                    # Post-edit action workflow
                    self._handle_post_translation_edit(
                        category,
                        untranslated,
                        entity_data
                    )

            elif action == "Make Global (available for all books)":
                confirm = self.questionary.confirm(
                    f"Make '{untranslated}' global? It will be available for all books."
                ).ask()

                if confirm:
                    # Update in database - set book_id to NULL
                    self.entity_manager.update_entity(
                        category,
                        untranslated,
                        book_id=None
                    )

                    # Update in-memory dict
                    if "book_id" in entity_data:
                        del entity_data["book_id"]
                    all_entities[category][untranslated] = entity_data

                    print(f"Entity '{untranslated}' is now global (available for all books).")

            elif action == "Assign to Specific Book":
                # Get list of available books
                books = self.entity_manager.list_books()

                if not books:
                    print("No books available in the database.")
                else:
                    # Create book choices
                    book_choices = []
                    for book in books:
                        book_id = book.get("id")
                        book_title = book.get("title", "Untitled")
                        book_choices.append(self.questionary.Choice(
                            title=f"{book_title} (ID: {book_id})",
                            value=book_id
                        ))

                    book_choices.append("Cancel")

                    selected_book_id = self.questionary.select(
                        "Select a book to assign this entity to:",
                        choices=book_choices
                    ).ask()

                    if selected_book_id != "Cancel":
                        # Update in database
                        self.entity_manager.update_entity(
                            category,
                            untranslated,
                            book_id=selected_book_id
                        )

                        # Update in-memory dict
                        entity_data["book_id"] = selected_book_id
                        all_entities[category][untranslated] = entity_data

                        book_title = next(
                            (b.get("title", "Unknown") for b in books if b.get("id") == selected_book_id),
                            "Unknown"
                        )
                        print(f"Entity '{untranslated}' assigned to book: {book_title} (ID: {selected_book_id})")

            elif action == "View Usage Context":
                self._view_entity_usage(category, untranslated, entity_data)

    def _add_new_entity(self, all_entities, book_id=None):
        """
        Add a new entity manually through interactive prompts.

        Args:
            all_entities: Dict of all entities by category
            book_id: Optional book ID filter from parent context
        """
        categories = ['characters', 'places', 'organizations', 'abilities', 'titles', 'equipment', 'creatures']

        # Step 1: Select category
        category = self.questionary.select(
            "Select the category for the new entity:",
            choices=categories + ["Cancel"]
        ).ask()

        if category == "Cancel":
            return

        # Step 2: Enter untranslated text
        untranslated = self.questionary.text(
            "Enter the untranslated text (Chinese):",
        ).ask()

        if not untranslated or not untranslated.strip():
            print("Untranslated text cannot be empty. Operation cancelled.")
            return

        untranslated = untranslated.strip()

        # Step 3: Check if entity already exists
        existing_entity = all_entities.get(category, {}).get(untranslated)
        if existing_entity:
            print(f"Warning: Entity '{untranslated}' already exists in category '{category}'.")
            print(f"Existing translation: {existing_entity.get('translation', 'N/A')}")
            overwrite = self.questionary.confirm(
                "Do you want to update the existing entity instead?"
            ).ask()

            if not overwrite:
                return

        # Step 4: Enter translation
        translation = self.questionary.text(
            "Enter the translation (English):",
        ).ask()

        if not translation or not translation.strip():
            print("Translation cannot be empty. Operation cancelled.")
            return

        translation = translation.strip()

        # Check if this translation is already used
        existing = self.entity_manager.get_entity_by_translation(translation)
        if existing and existing[1] != untranslated:
            existing_category, existing_key, _ = existing
            print(f"Warning: This translation is already used for '{existing_key}' in '{existing_category}'")
            proceed = self.questionary.confirm(
                "Do you want to proceed with this translation anyway?"
            ).ask()

            if not proceed:
                return

        # Step 5: Optional gender field (for characters only)
        gender = None
        if category == "characters":
            has_gender = self.questionary.confirm(
                "Do you want to specify a gender for this character?"
            ).ask()

            if has_gender:
                gender = self.questionary.select(
                    "Select gender:",
                    choices=["male", "female", "other"]
                ).ask()

        # Step 6: Book assignment
        assign_book_id = None
        if book_id:
            # If we're already filtering by a book, offer to assign to that book
            use_filter_book = self.questionary.confirm(
                f"Assign this entity to the current book filter (ID: {book_id})?"
            ).ask()

            if use_filter_book:
                assign_book_id = book_id

        if assign_book_id is None:
            # Ask if the entity should be book-specific
            is_book_specific = self.questionary.confirm(
                "Should this entity be specific to a book? (No = Global for all books)"
            ).ask()

            if is_book_specific:
                books = self.entity_manager.list_books()

                if not books:
                    print("No books available in the database. Entity will be created as global.")
                else:
                    # Create book choices
                    book_choices = []
                    for book in books:
                        book_id_choice = book.get("id")
                        book_title = book.get("title", "Untitled")
                        book_choices.append(self.questionary.Choice(
                            title=f"{book_title} (ID: {book_id_choice})",
                            value=book_id_choice
                        ))

                    book_choices.append("Cancel (Make Global)")

                    selected_book_id = self.questionary.select(
                        "Select a book to assign this entity to:",
                        choices=book_choices
                    ).ask()

                    if selected_book_id != "Cancel (Make Global)":
                        assign_book_id = selected_book_id

        # Step 7: Create the entity in the database
        try:
            self.entity_manager.add_entity(
                category=category,
                untranslated=untranslated,
                translation=translation,
                book_id=assign_book_id,
                gender=gender
            )

            # Step 8: Update in-memory dict
            entity_data = {
                "translation": translation,
                "last_chapter": "",
            }

            if assign_book_id:
                entity_data["book_id"] = assign_book_id

            if gender:
                entity_data["gender"] = gender

            all_entities.setdefault(category, {})
            all_entities[category][untranslated] = entity_data

            print(f"\nSuccessfully added entity:")
            print(f"  Category: {category}")
            print(f"  Untranslated: {untranslated}")
            print(f"  Translation: {translation}")
            if gender:
                print(f"  Gender: {gender}")
            if assign_book_id:
                book_title = next(
                    (b.get("title", "Unknown") for b in self.entity_manager.list_books() if b.get("id") == assign_book_id),
                    "Unknown"
                )
                print(f"  Book: {book_title} (ID: {assign_book_id})")
            else:
                print(f"  Scope: Global (all books)")

        except Exception as e:
            print(f"Error adding entity: {e}")
            self.logger.error(f"Error adding entity: {e}")

    def _view_entity_usage(self, category, untranslated, entity_data):
        """
        Display which chapters use this entity.

        Args:
            category: Entity category
            untranslated: Untranslated text
            entity_data: Entity data dict
        """
        print(f"\nSearching for usage of '{untranslated}' ({entity_data.get('translation', '')})...\n")

        # Get book_id from entity if available
        entity_book_id = entity_data.get("book_id")

        chapters = self.entity_manager.find_chapters_using_entity(
            untranslated,
            book_id=entity_book_id
        )

        if not chapters:
            print(f"No chapters found using this entity.")
            # Fallback to last_chapter field
            last_chapter = entity_data.get("last_chapter")
            if last_chapter:
                print(f"(Note: This entity was last seen in chapter {last_chapter})")
        else:
            print(f"Found in {len(chapters)} chapter(s):\n")
            for ch in chapters:
                print(f"  - Book: {ch['book_title']}, Chapter {ch['chapter_number']}: {ch['chapter_title']}")

        input("\nPress Enter to continue...")

    def _handle_post_translation_edit(self, category, untranslated, entity_data):
        """
        Handle post-translation-edit actions: find affected chapters and offer actions.

        Args:
            category: Entity category (characters, places, etc.)
            untranslated: The untranslated entity text
            entity_data: Entity data dictionary with 'translation' and 'incorrect_translation'
        """
        # Find affected chapters
        entity_book_id = entity_data.get("book_id")
        chapters = self.entity_manager.find_chapters_using_entity(
            untranslated,
            book_id=entity_book_id
        )

        # If no chapters found, skip the prompt
        if not chapters:
            self.logger.debug(f"No chapters found using entity '{untranslated}', skipping post-edit actions")
            return

        # Display affected chapters
        print(f"\nThis entity is used in {len(chapters)} chapter(s):")
        for ch in chapters[:5]:  # Show first 5
            print(f"  - {ch['book_title']}, Ch.{ch['chapter_number']}: {ch['chapter_title']}")
        if len(chapters) > 5:
            print(f"  ... and {len(chapters) - 5} more")

        # Prompt for action
        action = self.questionary.select(
            "\nWhat would you like to do with the affected chapters?",
            choices=[
                "Find and substitute (replace old translation in existing translations)",
                "Re-queue for re-translation (add to queue.json for full re-translation)",
                "Do nothing (keep current translations as-is)"
            ]
        ).ask()

        if action.startswith("Find and substitute"):
            self._substitute_translation_in_chapters(category, untranslated, entity_data, chapters)
        elif action.startswith("Re-queue"):
            self._requeue_chapters_with_entity(category, untranslated, entity_data, chapters)
        else:
            print("No changes made to existing chapters.")

    def _substitute_translation_in_chapters(self, category, untranslated, entity_data, chapters):
        """
        Find and substitute old translation with new translation in all affected chapters.

        Args:
            category: Entity category
            untranslated: The untranslated entity text
            entity_data: Entity data with 'translation' and 'incorrect_translation'
            chapters: List of chapter metadata dicts from find_chapters_using_entity()
        """
        print(f"\nSubstituting translations in {len(chapters)} chapter(s)...")

        updated_count = 0
        failed_count = 0

        for ch in chapters:
            try:
                # Load chapter data
                chapter = self.entity_manager.get_chapter(chapter_id=ch['chapter_id'])
                if not chapter:
                    self.logger.warning(f"Could not load chapter {ch['chapter_number']} from book {ch['book_title']}")
                    failed_count += 1
                    continue

                # Get translated content (list of lines)
                translated_lines = chapter.get('content', [])
                if not translated_lines or not isinstance(translated_lines, list):
                    self.logger.warning(f"No translated content in chapter {ch['chapter_number']}")
                    failed_count += 1
                    continue

                # Perform substitution using existing method
                updated_lines = self.entity_manager.update_translated_text(
                    translated_lines.copy(),  # Make copy to avoid mutation
                    entity_data
                )

                # Save updated chapter
                # Note: We intentionally do NOT update translation_date and translation_model
                # because this is a correction, not a new translation
                chapter_id = self.entity_manager.save_chapter(
                    book_id=chapter['book_id'],
                    chapter_number=chapter['chapter'],
                    title=chapter['title'],
                    untranslated_content=chapter['untranslated'],
                    translated_content=updated_lines,
                    summary=chapter.get('summary'),
                    translation_model=chapter.get('model')  # Keep original model
                )

                if chapter_id:
                    updated_count += 1
                    print(f"  ✓ Updated: {ch['book_title']}, Ch.{ch['chapter_number']}")
                else:
                    failed_count += 1
                    print(f"  ✗ Failed: {ch['book_title']}, Ch.{ch['chapter_number']}")

            except Exception as e:
                self.logger.error(f"Error updating chapter {ch['chapter_number']}: {e}")
                failed_count += 1
                print(f"  ✗ Error: {ch['book_title']}, Ch.{ch['chapter_number']} - {e}")

        # Summary
        print(f"\nSubstitution complete:")
        print(f"  - Successfully updated: {updated_count} chapter(s)")
        if failed_count > 0:
            print(f"  - Failed: {failed_count} chapter(s)")

        input("\nPress Enter to continue...")

    def _requeue_chapters_with_entity(self, category, untranslated, entity_data, chapters):
        """
        Add affected chapters to queue.json for full re-translation.

        Args:
            category: Entity category
            untranslated: The untranslated entity text
            entity_data: Entity data
            chapters: List of chapter metadata dicts from find_chapters_using_entity()
        """
        import os

        print(f"\nAdding {len(chapters)} chapter(s) to queue for re-translation...")

        added_count = 0
        skipped_count = 0

        for ch in chapters:
            # Check for duplicate using database method
            if self.entity_manager.check_duplicate_in_queue(ch['book_id'], ch['chapter_number']):
                self.logger.info(f"Skipping duplicate: {ch['book_title']}, Ch.{ch['chapter_number']}")
                skipped_count += 1
                continue

            try:
                # Load chapter data
                chapter = self.entity_manager.get_chapter(chapter_id=ch['chapter_id'])
                if not chapter:
                    self.logger.warning(f"Could not load chapter {ch['chapter_number']} from book {ch['book_title']}")
                    continue

                # Add to queue using database
                queue_item_id = self.entity_manager.add_to_queue(
                    book_id=chapter['book_id'],
                    content=chapter.get('untranslated', []),
                    title=chapter['title'],
                    chapter_number=chapter['chapter'],
                    source="(re-queued for entity update)"
                )

                if queue_item_id:
                    added_count += 1
                    print(f"  ✓ Queued: {ch['book_title']}, Ch.{ch['chapter_number']}")
                else:
                    print(f"  ✗ Error: Failed to queue {ch['book_title']}, Ch.{ch['chapter_number']}")

            except Exception as e:
                self.logger.error(f"Error queuing chapter {ch['chapter_number']}: {e}")
                print(f"  ✗ Error: {ch['book_title']}, Ch.{ch['chapter_number']} - {e}")

        # Print summary
        if added_count > 0:
            total_count = self.entity_manager.get_queue_count()
            print(f"\nRe-queuing complete:")
            print(f"  - Added to queue: {added_count} chapter(s)")
            if skipped_count > 0:
                print(f"  - Skipped (already queued): {skipped_count} chapter(s)")
            print(f"  - Total queue size: {total_count}")
        else:
            print(f"\nNo chapters added to queue.")
            if skipped_count > 0:
                print(f"  - All {skipped_count} chapter(s) already in queue")

        input("\nPress Enter to continue...")

    def _paginate_entity_list(self, entity_list, page_size=20):
        """
        Display entities in pages to avoid overwhelming the user.

        Args:
            entity_list: List of (category, untranslated, entity_data) tuples
            page_size: Number of entities per page

        Returns:
            Tuple of (category, untranslated, entity_data) or None
        """
        total = len(entity_list)
        current_page = 0
        max_page = (total - 1) // page_size

        while True:
            start = current_page * page_size
            end = min(start + page_size, total)

            page_entities = entity_list[start:end]

            choices = []
            for category, untranslated, entity_data in page_entities:
                translation = entity_data.get("translation", "")
                book_id_display = entity_data.get("book_id", "Global")
                display = f"{translation} ({untranslated}) - [{category}] (Book: {book_id_display})"
                choices.append(self.questionary.Choice(
                    title=display,
                    value=(category, untranslated, entity_data)
                ))

            # Add navigation
            if current_page > 0:
                choices.append(self.questionary.Choice("← Previous Page", value="prev"))
            if current_page < max_page:
                choices.append(self.questionary.Choice("Next Page →", value="next"))
            choices.append("Back")

            print(f"\nPage {current_page + 1} of {max_page + 1} ({total} total entities)")

            selection = self.questionary.select(
                "Select an entity:",
                choices=choices
            ).ask()

            if selection == "prev":
                current_page -= 1
            elif selection == "next":
                current_page += 1
            elif selection == "Back":
                return None
            else:
                return selection

    def display_results(self, end_object, book_info=None):
        """Display translation results to the user and save in the specified format"""
        # Get title with a default value if missing
        chapter_title = end_object.get('title', 'Untitled Chapter')
        
        # Initialize OutputFormatter if needed
        if not hasattr(self, 'output_formatter'):
            self.output_formatter = OutputFormatter(self.entity_manager.config, self.logger)
        
        # Save in the specified format
        output_path = self.output_formatter.save_output(
            end_object, 
            format=getattr(self, 'output_format', 'text'),
            book_info=book_info
        )
        
        # Calculate statistics
        translated_total_words = 0
        translated_total_chars = 0
        content = end_object.get('content', [])
        
        for line in content:
            translated_total_words += len(line.split())
            translated_total_chars += len(line)
        
        # Try to copy to clipboard if available
        try:
            import pyperclip
            pyperclip.copy("\n".join(content))
            self.logger.info("Translated text copied to clipboard for pasting.")
        except ImportError:
            self.logger.info("pyperclip not available - clipboard functions disabled")
        
        # Display summary
        self.logger.info(f"TITLE: {chapter_title}")
        self.logger.info(
            "Translated. Input text is "
            + str(sum(len(line) for line in end_object.get('untranslated', [])))
            + " characters compared to "
            + str(translated_total_words)
            + " translated words ("
            + str(translated_total_chars)
            + " characters.)"
        )
        
        format_name = getattr(self, 'output_format', 'text').upper()
        print(f"Translation saved in {format_name} format to {output_path}")    

    def resolve_duplicate_entities(self, duplicates, untranslated_text):
        """
        Interactive method to resolve duplicate entities across categories.
        
        Args:
            duplicates: List of potential duplicate entities to resolve
            untranslated_text: Original text for context
            
        Returns:
            List of resolved entities with their decisions
        """
        if not self.has_rich_ui or not duplicates:
            return []
        
        resolved = []
        
        print("\n=== Entity Category Conflict Resolution ===")
        print(f"Found {len(duplicates)} potential duplicate entities across categories that need resolution.")
        
        for duplicate in duplicates:
            untranslated = duplicate['untranslated']
            translation = duplicate['translation']
            new_category = duplicate['new_category']
            existing_category = duplicate['existing_category']
            existing_translation = duplicate['existing_translation']
            
            # Find context if possible
            context = None
            if untranslated_text:
                context = self.translator.find_substring_with_context(untranslated_text, untranslated, 50)
                duplicate['context'] = context
            
            print(f"\nEntity: '{untranslated}'")
            print(f"Currently in: '{existing_category}' as '{existing_translation}'")
            print(f"Model suggests: '{new_category}' as '{translation}'")
            
            if context:
                print(f"Context: \"...{context}...\"")
            
            # Ask user what to do
            action = self.questionary.select(
                "How would you like to handle this entity?",
                choices=[
                    "Keep in existing category (reject new suggestion)",
                    "Move to new category (replace existing)",
                    "Keep in both categories (allow duplication)",
                    "Edit manually"
                ]
            ).ask()
            
            if action == "Keep in existing category (reject new suggestion)":
                # Do nothing, just record the decision
                duplicate['decision'] = 'keep_existing'
                resolved.append(duplicate)
                print(f"Decision: Keeping '{untranslated}' in '{existing_category}'")
            
            elif action == "Move to new category (replace existing)":
                # Move the entity to the new category
                result = self.entity_manager.change_entity_category(existing_category, untranslated, new_category)
                
                if result:
                    duplicate['decision'] = 'move_to_new'
                    resolved.append(duplicate)
                    print(f"Decision: Moved '{untranslated}' from '{existing_category}' to '{new_category}'")
                else:
                    print(f"Failed to move entity. See logs for details.")
            
            elif action == "Keep in both categories (allow duplication)":
                # Allow the duplication - need to add it manually as database constraints prevent this
                duplicate['decision'] = 'allow_duplicate'
                resolved.append(duplicate)
                print(f"Decision: Allowing '{untranslated}' in both '{existing_category}' and '{new_category}'")
                
                # Need to override database constraints to add this duplicate
                try:
                    conn = sqlite3.connect(self.entity_manager.db_path)
                    cursor = conn.cursor()
                    cursor.execute('''
                    INSERT INTO entities (category, untranslated, translation, last_chapter)
                    VALUES (?, ?, ?, ?)
                    ''', (new_category, untranslated, translation, duplicate.get('last_chapter', 'THIS CHAPTER')))
                    conn.commit()
                    conn.close()
                    
                    # Update memory cache
                    self.entity_manager._load_entities()
                    
                except sqlite3.Error as e:
                    self.logger.error(f"Error allowing duplicate: {e}")
                    print(f"Database error: {e}")
            
            elif action == "Edit manually":
                # Allow detailed editing
                print("\nManual Editing:")

                # Choose category
                target_category = self.questionary.select(
                    "Which category should this entity be in?",
                    choices=['characters', 'places', 'organizations', 'abilities', 'titles', 'equipment', 'creatures']
                ).ask()
                
                # Choose translation
                custom_translation = self.questionary.text(
                    f"Enter translation for '{untranslated}' (default: '{translation}'):",
                    default=translation
                ).ask()
                
                # Apply changes
                if existing_category == target_category:
                    # Update existing entity
                    self.entity_manager.update_entity(
                        target_category, 
                        untranslated, 
                        translation=custom_translation
                    )
                    print(f"Updated '{untranslated}' in '{target_category}' with translation '{custom_translation}'")
                else:
                    # Delete from old category and add to new
                    self.entity_manager.delete_entity(existing_category, untranslated)
                    self.entity_manager.add_entity(
                        target_category,
                        untranslated,
                        custom_translation
                    )
                    print(f"Moved '{untranslated}' from '{existing_category}' to '{target_category}' with translation '{custom_translation}'")
                
                duplicate['decision'] = 'manual_edit'
                duplicate['final_category'] = target_category
                duplicate['final_translation'] = custom_translation
                resolved.append(duplicate)
        
        print("\nAll duplicate entities have been resolved.")
        return resolved
    
    def _get_book_info(self):
        """Get or create book information for EPUB output"""
        if not hasattr(self, 'output_formatter'):
            self.output_formatter = OutputFormatter(self.entity_manager.config, self.logger)
        
        return self.output_formatter.get_book_info()

    def _edit_book_info(self):
        """Edit book information for EPUB output"""
        if not hasattr(self, 'output_formatter'):
            self.output_formatter = OutputFormatter(self.entity_manager.config, self.logger)
        
        book_info = self._get_book_info()
        
        if self.has_rich_ui:
            # Use questionary for interactive editing
            print("Editing book information for EPUB output:")
            
            book_info["title"] = self.questionary.text(
                "Book title:",
                default=book_info.get("title", "Translated Book")
            ).ask()
            
            book_info["author"] = self.questionary.text(
                "Book author:",
                default=book_info.get("author", "Translator")
            ).ask()
            
            book_info["language"] = self.questionary.text(
                "Book language code (e.g., en, zh, ja):",
                default=book_info.get("language", "en")
            ).ask()
            
            book_info["description"] = self.questionary.text(
                "Book description:",
                default=book_info.get("description", "")
            ).ask()
        else:
            # Fallback to regular input
            print("Editing book information for EPUB output:")
            
            title = input(f"Book title [{book_info.get('title', 'Translated Book')}]: ")
            if title:
                book_info["title"] = title
            
            author = input(f"Book author [{book_info.get('author', 'Translator')}]: ")
            if author:
                book_info["author"] = author
            
            language = input(f"Book language code (e.g., en, zh, ja) [{book_info.get('language', 'en')}]: ")
            if language:
                book_info["language"] = language
            
            description = input(f"Book description [{book_info.get('description', '')}]: ")
            if description:
                book_info["description"] = description
        
        # Save the updated book info
        output_dir = os.path.join(self.entity_manager.config.script_dir, "output")
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
        
        book_info_path = os.path.join(output_dir, "book_info.json")
        
        try:
            with open(book_info_path, 'w', encoding='utf-8') as f:
                json.dump(book_info, f, indent=4, ensure_ascii=False)
            print(f"Book information saved to {book_info_path}")
        except Exception as e:
            self.logger.error(f"Error saving book info: {e}")
            print(f"Error saving book info: {e}")
        
        return book_info

    def _list_queue_contents(self, summary_only=False, book_id=None):
        """List all items in the translation queue."""
        queue_items = self.entity_manager.list_queue(book_id=book_id)

        if not queue_items:
            if book_id:
                print(f"Queue is empty for book ID {book_id}.")
            else:
                print("Queue is empty.")
            return

        if book_id:
            print(f"Queue contains {len(queue_items)} items for book ID {book_id}:")
        else:
            print(f"Queue contains {len(queue_items)} items:")

        if summary_only:
            return

        for i, item in enumerate(queue_items, 1):
            chapter_info = f"Chapter {item['chapter_number']}" if item['chapter_number'] else "No chapter number"
            content_lines = len(item['content']) if isinstance(item['content'], list) else 0

            print(f"{i}. [{item['book_title']}] {item['title']} ({chapter_info}) - {content_lines} lines")
            if item['source']:
                print(f"   Source: {item['source']}")

    def _process_directory(self, directory_path, book_id=None, sort_strategy="auto", file_pattern="*.txt"):
        """Process all text files in a directory and add them to the queue."""
        try:
            # Initialize directory processor
            processor = DirectoryProcessor(self.entity_manager.config, self.logger, self.entity_manager)

            print(f"Processing directory: {directory_path}")
            print(f"Sort strategy: {sort_strategy}")
            print(f"File pattern: {file_pattern}")

            success, num_files, message = processor.process_directory(
                directory_path, book_id, sort_strategy, file_pattern)
            
            if success:
                print(f"Success! {message}")
                
                # Show queue summary
                self._list_queue_contents(summary_only=True)
            else:
                print(f"Failed! {message}")
                
        except Exception as e:
            self.logger.error(f"Error processing directory: {e}")
            print(f"Error processing directory: {e}")
            import traceback
            traceback.print_exc()
            
    def _clear_queue(self, book_id=None):
        """Clear the translation queue."""
        # Get count first
        count = self.entity_manager.get_queue_count(book_id=book_id)

        if count == 0:
            if book_id:
                print(f"Queue is already empty for book ID {book_id}.")
            else:
                print("Queue is already empty.")
            return

        # Ask for confirmation
        if book_id:
            message = f"Are you sure you want to clear {count} items from the queue for book ID {book_id}?"
        else:
            message = f"Are you sure you want to clear the entire queue ({count} items)?"

        if self.has_rich_ui:
            confirm = self.questionary.confirm(message).ask()
            if not confirm:
                print("Operation cancelled.")
                return
        else:
            response = input(f"{message} (y/n): ")
            if response.lower() != 'y':
                print("Operation cancelled.")
                return

        # Clear the queue
        removed = self.entity_manager.clear_queue(book_id=book_id)

        if book_id:
            print(f"Cleared {removed} items from queue for book ID {book_id}.")
        else:
            print(f"Cleared {removed} items from queue.")
