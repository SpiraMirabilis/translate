"""
Modular Translator Application with Class-Based Design
"""
import json
import os
import math
import re
import unicodedata
import sqlite3
import logging
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any, Union, Tuple
from itertools import zip_longest
from openai import OpenAI
from dotenv import load_dotenv
from epub_processor import EPUBProcessor
from output_formatter import OutputFormatter
from directory_processor import DirectoryProcessor
from config import TranslationConfig
from logger import Logger
from database import DatabaseManager
from translation_engine import TranslationEngine
from ui import UserInterface
from cli import CommandLineInterface

class TranslationApp:
    """
    Main application class that ties everything together.
    This class creates all the necessary components and manages the workflow.
    """
    
    def __init__(self, ui_type="cli"):
        # Initialize configuration
        self.config = TranslationConfig()
        
        # Set up logger
        self.logger = Logger(self.config)
        
        # Initialize database manager with SQLite database
        self.entity_manager = DatabaseManager(self.config, self.logger)
        
        # Handle migration from JSON to SQLite if needed
        self._migrate_json_to_sqlite_if_needed()
        
        # Create translation engine
        self.translator = TranslationEngine(self.config, self.logger, self.entity_manager)
        
        # Create appropriate UI
        if ui_type.lower() == "cli":
            self.ui = CommandLineInterface(self.translator, self.entity_manager, self.logger)
        elif ui_type.lower() == "web":
            self.ui = WebUserInterface(self.translator, self.entity_manager, self.logger)
        else:
            raise ValueError(f"Unsupported UI type: {ui_type}")
    
    def _migrate_json_to_sqlite_if_needed(self):
        """
        Check if we need to migrate from JSON to SQLite by looking for an entities.json file
        and a possibly empty SQLite database.
        """
        
        json_path = os.path.join(self.config.script_dir, "entities.json")
        
        # Check if the JSON file exists and SQLite DB is empty
        if os.path.exists(json_path):
            try:
                # Check if the database is empty
                conn = sqlite3.connect(self.entity_manager.db_path)
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM entities")
                count = cursor.fetchone()[0]
                conn.close()
                
                if count == 0:
                    # Database is empty, but JSON file exists - perform migration
                    self.logger.info("Found entities.json but empty SQLite database. Migrating data...")
                    
                    # Import the JSON data
                    success = self.entity_manager.import_from_json("entities.json")
                    
                    if success:
                        self.logger.info("Successfully migrated entities from JSON to SQLite")
                        # Optionally, rename the original JSON file to indicate it's been migrated
                        backup_path = os.path.join(self.config.script_dir, "entities.json.bak")
                        try:
                            os.rename(json_path, backup_path)
                            self.logger.info(f"Renamed original JSON file to {backup_path}")
                        except OSError as e:
                            self.logger.warning(f"Could not rename JSON file: {e}")
                    else:
                        self.logger.error("Failed to migrate data from JSON to SQLite")
                else:
                    self.logger.info(f"SQLite database already contains {count} entities, no migration needed")
                    
            except sqlite3.Error as e:
                self.logger.error(f"Error checking database during migration: {e}")
    
    def run(self):
        """Run the full translation process"""
        try:
            return self.ui.run_translation()
        except Exception as e:
            self.logger.error(f"Error during translation: {e}")
            raise


def main():
    """Entry point function"""
    app = TranslationApp()
    app.run()


if __name__ == "__main__":
    main()
