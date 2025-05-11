"""
Directory Processor Module for Translator Application.
Processes a directory of text files and adds them to the translation queue.
"""
import os
import re
import json
import logging
from datetime import datetime
from typing import List, Dict, Tuple


class DirectoryProcessor:
    """
    A class to process directories containing text files and add them to the translation queue.
    """
    
    def __init__(self, config, logger):
        """
        Initialize the directory processor.
        
        Args:
            config: TranslationConfig object with script_dir and other settings
            logger: Logger object for logging messages
        """
        self.config = config
        self.logger = logger
    
    def process_directory(self, directory_path, sort_strategy="auto", file_pattern="*.*"):
        """
        Process text files in a directory and add them to the translation queue.
        
        Args:
            directory_path: Path to the directory containing text files
            sort_strategy: Strategy for ordering files ("auto", "name", "modified", "none")
            file_pattern: Pattern to filter files (e.g., "*.txt")
            
        Returns:
            tuple: (success, num_files_added, message)
        """
        if not os.path.exists(directory_path) or not os.path.isdir(directory_path):
            return False, 0, f"Directory not found: {directory_path}"
        
        # Get list of files matching pattern
        import glob
        file_paths = glob.glob(os.path.join(directory_path, file_pattern))
        
        # Filter out directories
        file_paths = [path for path in file_paths if os.path.isfile(path)]
        
        if not file_paths:
            return False, 0, f"No files found matching pattern '{file_pattern}' in {directory_path}"
        
        # Sort files based on strategy
        sorted_files = self._sort_files(file_paths, sort_strategy)
        
        # Process each file and add to queue
        chapters = []
        for i, (file_path, metadata) in enumerate(sorted_files, 1):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                # Extract filename for title
                filename = os.path.basename(file_path)
                title = os.path.splitext(filename)[0]
                
                # Determine chapter number
                chapter_number = metadata.get("chapter_number", i)
                
                # Create chapter info
                chapter = {
                    'title': title,
                    'content': content,
                    'number': chapter_number,
                    'file_path': file_path
                }
                
                chapters.append(chapter)
                self.logger.info(f"Processed file: {file_path}")
                
            except Exception as e:
                self.logger.error(f"Error processing file {file_path}: {e}")
        
        # Add to queue
        if chapters:
            num_added = self._add_chapters_to_queue(chapters)
            return True, num_added, f"Successfully added {num_added} files to queue"
        else:
            return False, 0, "No files were successfully processed"
    
    def _sort_files(self, file_paths, sort_strategy):
        """
        Sort files based on the specified strategy.
        
        Args:
            file_paths: List of file paths
            sort_strategy: Strategy for ordering ("auto", "name", "modified", "none")
            
        Returns:
            list: List of tuples (file_path, metadata) sorted according to strategy
        """
        files_with_metadata = []
        
        # Gather metadata for each file
        for file_path in file_paths:
            metadata = {
                "modified_time": os.path.getmtime(file_path),
                "name": os.path.basename(file_path)
            }
            
            # Try to extract chapter number from filename
            chapter_match = re.search(r'(?:chapter|ch|第)[\s_-]*(\d+)|^(\d+)', os.path.basename(file_path), re.IGNORECASE)
            if chapter_match:
                # Use the first matching group that captured something
                chapter_number = next(g for g in chapter_match.groups() if g is not None)
                metadata["chapter_number"] = int(chapter_number)
            
            files_with_metadata.append((file_path, metadata))
        
        # Sort based on strategy
        if sort_strategy == "auto":
            # First try to sort by chapter number if available
            chapter_numbers_found = any("chapter_number" in metadata for _, metadata in files_with_metadata)
            
            if chapter_numbers_found:
                # Sort by chapter number
                def chapter_number_key(item):
                    _, metadata = item
                    return metadata.get("chapter_number", float('inf'))
                
                files_with_metadata.sort(key=chapter_number_key)
            else:
                # Fall back to name sorting
                files_with_metadata.sort(key=lambda x: x[1]["name"])
                
        elif sort_strategy == "name":
            # Sort alphabetically by name
            files_with_metadata.sort(key=lambda x: x[1]["name"])
            
        elif sort_strategy == "modified":
            # Sort by modification time (oldest first)
            files_with_metadata.sort(key=lambda x: x[1]["modified_time"])
            
        # For "none", keep original order
        
        return files_with_metadata
    
    def _add_chapters_to_queue(self, chapters):
        """
        Add chapters to the translation queue.
        
        Args:
            chapters: List of chapter dicts
            
        Returns:
            int: Number of chapters added to queue
        """
        # Load existing queue
        queue_path = os.path.join(self.config.script_dir, "queue.json")
        if os.path.exists(queue_path):
            try:
                with open(queue_path, 'r', encoding='utf-8') as f:
                    queue = json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                self.logger.error(f"Error loading queue: {e}")
                queue = []
        else:
            queue = []
        
        # Add each chapter to the queue
        for chapter in chapters:
            content = chapter['content']
            content_lines = content.split('\n')
            
            # Add metadata as comments at the top
            metadata = [
                f"# Title: {chapter['title']}",
                f"# Chapter: {chapter['number']}",
                f"# Source: {chapter['file_path']}",
                "# ---"
            ]
            
            chapter_with_metadata = metadata + content_lines
            queue.append(chapter_with_metadata)
        
        # Save updated queue
        try:
            with open(queue_path, 'w', encoding='utf-8') as f:
                json.dump(queue, f, ensure_ascii=False, indent=2)
            self.logger.info(f"Added {len(chapters)} chapters to queue")
            return len(chapters)
        except IOError as e:
            self.logger.error(f"Error saving queue: {e}")
            return 0
