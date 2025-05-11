from typing import Dict, List, Optional, Any, Union, Tuple
from abc import ABC, abstractmethod
from entities import EntityManager
from logger import Logger
from translation_engine import TranslationEngine
from ui import UserInterface
import json
import sqlite3

class CommandLineInterface(UserInterface):
    """Command-line interface implementation"""
    
    def __init__(self, translator: TranslationEngine, entity_manager: EntityManager, logger: Logger):
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
    
    def get_input(self) -> List[str]:
        """Get input text from CLI - clipboard, file, or manual entry"""
        import argparse
        
        # Create the parser
        parser = argparse.ArgumentParser(description="Process input from clipboard, file, or manual entry.")
        
        # Create a mutually exclusive group, this is for the input option
        group = parser.add_mutually_exclusive_group()
        
        # Add arguments to the group
        group.add_argument("--clipboard", action="store_true", help="Process input from the clipboard")
        group.add_argument("--resume", action="store_true", help="Take input from the queue, and translate sequentially")
        group.add_argument("--file", type=str, help="Process input from a specified file")
        group.add_argument("--epub", type=str, help="Process an EPUB file and add chapters to the queue")

        # directory input and options
        group.add_argument("--dir", type=str, help="Process all text files in a directory and add to queue")
        parser.add_argument("--sort", type=str, choices=["auto", "name", "modified", "none"], default="auto",
                    help="Sorting strategy for directory files (default: auto)")
        parser.add_argument("--pattern", type=str, default="*.txt",
                    help="File pattern for directory processing (default: *.txt)")

        # output options
        parser.add_argument("--format", type=str, choices=["text", "html", "markdown", "epub"], default="text",
                   help="Output format for translation results (default: text)")
        parser.add_argument("--book-title", type=str, help="Book title for EPUB output")
        parser.add_argument("--book-author", type=str, help="Book author for EPUB output")
        parser.add_argument("--book-language", type=str, default="en", help="Book language code for EPUB output (default: en)")
        parser.add_argument("--edit-book-info", action="store_true", help="Edit book information for EPUB output")
        
        # Queue argument and manipulation
        parser.add_argument("--queue", action="store_true", help="Add a chapter to the queue, for later sequential translation")
        parser.add_argument("--list-queue", action="store_true", 
                        help="List all items in the translation queue")
        parser.add_argument("--clear-queue", action="store_true", 
                        help="Clear the translation queue")
        
        # SQLite arguments
        parser.add_argument("--export-json", type=str, help="Export SQLite database to JSON file")
        parser.add_argument("--import-json", type=str, help="Import entities from JSON file to SQLite database")
        parser.add_argument("--check-duplicates", action="store_true", help="Check for duplicate entities in the database")
        
        # Model arguments
        parser.add_argument("--model", type=str, 
                        help="Specify model for translation (format: [provider:]model, e.g., oai:gpt-4 or deepseek:deepseek-chat)")
        parser.add_argument("--advice-model", type=str, 
                    help="Specify model for entity translation advice (format: [provider:]model,e.g. oai:gpt-4 or deepseek:deepseek-chat)")
        parser.add_argument("--key", type=str, 
                    help="Specify API key (for the provider specified in --model)")
        
        args = parser.parse_args()

        # CLI provided API keys
        if args.key:
            # Determine which key to set based on the model provider
            if args.model:
                provider, _ = self.translator.config.parse_model_spec(args.model)
                if provider in ["deepseek", "ds"]:
                    self.translator.config.deepseek_key = args.key
                else:  # Default to OpenAI
                    self.translator.config.openai_key = args.key
            else:
                # If no model specified, use the provider from translation_model
                provider, _ = self.translator.config.parse_model_spec(self.translator.config.translation_model)
                if provider in ["deepseek", "ds"]:
                    self.translator.config.deepseek_key = args.key
                else:  # Default to OpenAI
                    self.translator.config.openai_key = args.key
            
            # Reinitialize client with new key
            self.translator.client, self.translator.model_name = self.translator.config.get_client()

        # CLI provided model
        if args.model:
            self.translator.config.translation_model = args.model
            self.translator.client, self.translator.model_name = self.translator.config.get_client(args.model)

        # Handle advice model override
        if args.advice_model:
            self.translator.config.advice_model = args.advice_model

        # Process directory
        if args.dir:
            self._process_directory(args.dir, args.sort, args.pattern)
            exit(0)  # Exit after processing directory

        # edit epub info from --edit-book-info
        if args.edit_book_info:
            self._edit_book_info()
            exit(0)

        # List queue contents
        if args.list_queue:
            self._list_queue_contents()
            exit(0)

        # Clear queue
        if args.clear_queue:
            self._clear_queue()
            exit(0)
        
        # Store the format in a class variable
        self.output_format = args.format

        # Create book_info if EPUB format is selected
        if self.output_format == "epub":
            self.book_info = self._get_book_info()
            
            # Override with command line arguments if provided
            if args.book_title:
                self.book_info["title"] = args.book_title
            if args.book_author:
                self.book_info["author"] = args.book_author
            if args.book_language:
                self.book_info["language"] = args.book_language
        else:
            self.book_info = None

        # Handle API key override
        if args.key:
            self.translator.config.openai_key = args.key
            self.translator.client = self.translator.config.get_client()
        
        # Handle model override
        if args.model:
            self.translator.config.translation_model = args.model
        
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
        
        # Get the input based on the arguments
        if args.clipboard:
            import pyperclip
            self.logger.info("Processing input from the clipboard.")
            pretext = pyperclip.paste().splitlines()
        elif args.file:
            self.logger.info(f"Processing input from the file: {args.file}")
            pretext = self.file_to_array(args.file)
        elif args.resume:
            queue_json = self.entity_manager._load_json_file("queue.json")
            if not queue_json or not isinstance(queue_json, list) or len(queue_json) == 0:
                print("No items in queue to resume.")
                exit(1)
            
            # Debug info
            self.logger.info(f"Queue contains {len(queue_json)} items.")
            self.logger.info(f"First item type: {type(queue_json[0])}")
            
            # Get the first item from the queue
            first_item = queue_json[0]
            
            # Handle different possible formats in the queue
            if isinstance(first_item, list):
                # Already a list of strings
                pretext = first_item
            elif isinstance(first_item, str):
                # Single string, split into lines
                pretext = first_item.splitlines()
            else:
                # Unknown format
                self.logger.error(f"Unknown queue item format: {type(first_item)}")
                print(f"Queue item has unexpected format: {type(first_item)}")
                exit(1)
            
            # Verify the item is not empty
            if not pretext:
                self.logger.error("Empty item in queue.")
                print("The first item in the queue is empty. Removing it.")
                
                # Remove the empty item and save the updated queue
                updated_queue = queue_json[1:]
                self.entity_manager.save_json_file(f"{self.entity_manager.config.script_dir}/queue.json", updated_queue)
                
                # Exit or recurse to get the next item
                if not updated_queue:
                    print("Queue is now empty.")
                    exit(1)
                else:
                    print("Trying next item in queue...")
                    return self.get_input()  # Recursive call to try the next item
            
            self.logger.info(f"Processing queue item with {len(pretext)} lines.")
            
            # Store the queue for later updating after successful translation
            self._current_queue = queue_json
            
            return pretext
        elif args.epub:
            self._process_epub_file(args.epub)
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
            queue = self.entity_manager._load_json_file("queue.json") or []
            self.logger.info(f"Appending to queue (len: {len(queue)}) ")
            queue.append(pretext)
            self.entity_manager.save_json_file(f"{self.entity_manager.config.script_dir}/queue.json", queue)
            self.logger.info("queue.json written")
            exit(0)  # Exit after queuing - don't continue to translation!
        
        return pretext
    
    def _process_epub_file(self, epub_path):
        """Process an EPUB file and add chapters to the queue."""
        try:
            # Initialize EPUB processor
            processor = EPUBProcessor(self.entity_manager.config, self.logger)
            
            print(f"Processing EPUB file: {epub_path}")
            success, num_chapters, message = processor.process_epub(epub_path)
            
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
    
    def review_entities(self, data, untranslated_text=[]):
        """
        Using questionary to display interactive prompts.
        Returns a dictionary of edited data.
        """
        if not self.has_rich_ui:
            print("Rich UI components not available. Skipping entity review.")
            return {}
            
        edited_data = {'characters': {}, 'places': {}, 'organizations': {}, 'abilities': {}, 'titles': {}, 'equipment': {}}
        categories = ['characters', 'places', 'organizations', 'abilities', 'titles', 'equipment']
        
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
                
                self.entity_manager.update_entity(
                    selected_category, 
                    selected_item_key, 
                    translation=translation, 
                    last_chapter=last_chapter,
                    incorrect_translation=incorrect_translation,
                    gender=gender
                )
        
        return edited_data

    def display_results(self, end_object):
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
            book_info=getattr(self, 'book_info', None)
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
                    choices=['characters', 'places', 'organizations', 'abilities', 'titles', 'equipment']
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

    def _list_queue_contents(self, summary_only=False):
        """List all items in the translation queue."""
        queue_path = os.path.join(self.entity_manager.config.script_dir, "queue.json")
        
        if not os.path.exists(queue_path):
            print("Queue file not found.")
            return
        
        try:
            with open(queue_path, 'r', encoding='utf-8') as f:
                queue = json.load(f)
                
            if not queue:
                print("Queue is empty.")
                return
                
            print(f"Queue contains {len(queue)} items:")
            
            if summary_only:
                return
                
            for i, item in enumerate(queue, 1):
                # Try to extract title from metadata
                title = None
                chapter = None
                
                if isinstance(item, list) and len(item) > 0:
                    for line in item[:5]:  # Check first few lines for metadata
                        if isinstance(line, str):
                            if line.startswith("# Title:"):
                                title = line[8:].strip()
                            elif line.startswith("# Chapter:"):
                                chapter = line[10:].strip()
                
                if title:
                    if chapter:
                        print(f"{i}. {title} (Chapter {chapter}) - {len(item)} lines")
                    else:
                        print(f"{i}. {title} - {len(item)} lines")
                else:
                    print(f"{i}. Item with {len(item)} lines")
                    
        except Exception as e:
            print(f"Error reading queue: {e}")

    def _process_directory(self, directory_path, sort_strategy="auto", file_pattern="*.txt"):
        """Process all text files in a directory and add them to the queue."""
        try:
            # Initialize directory processor
            processor = DirectoryProcessor(self.entity_manager.config, self.logger)
            
            print(f"Processing directory: {directory_path}")
            print(f"Sort strategy: {sort_strategy}")
            print(f"File pattern: {file_pattern}")
            
            success, num_files, message = processor.process_directory(
                directory_path, sort_strategy, file_pattern)
            
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
            
    def _clear_queue(self):
        """Clear the translation queue."""
        queue_path = os.path.join(self.entity_manager.config.script_dir, "queue.json")
        
        if not os.path.exists(queue_path):
            print("Queue file not found.")
            return
        
        try:
            # Ask for confirmation
            if self.has_rich_ui:
                confirm = self.questionary.confirm("Are you sure you want to clear the entire queue?").ask()
                if not confirm:
                    print("Operation cancelled.")
                    return
            else:
                response = input("Are you sure you want to clear the entire queue? (y/n): ")
                if response.lower() != 'y':
                    print("Operation cancelled.")
                    return
            
            # Clear the queue
            with open(queue_path, 'w', encoding='utf-8') as f:
                json.dump([], f)
            
            print("Queue has been cleared.")
                
        except Exception as e:
            print(f"Error clearing queue: {e}")
