from typing import Dict, List, Optional, Any, Union, Tuple
import json
import sqlite3
import math
import os
import time


class TranslationEngine:
    """Core class for handling text translation logic"""
    
    def __init__(self, config: 'TranslationConfig', logger: 'Logger', entity_manager: 'DatabaseManager'):
        self.config = config
        self.logger = logger
        self.entity_manager = entity_manager
    
    def find_substring_with_context(self, text_array, substring, padding=20):
        """
        Search for a substring in a joined string (converted from a list of strings)
        and return padding[20] characters before and after the match.
        
        Parameters:
            text_array (list of str or str): The array of strings or string representing the text.
            substring (str): The substring to search for.
            padding (int) [optional]: the number of characters before and after to include
        
        Returns:
            str: The context of the match (padding characters before, the match, padding characters after) 
                 or None if no match is found.
        """
        if isinstance(text_array, list):
            # Join the array of strings into a single string with spaces separating lines
            full_text = ' '.join(text_array)
        elif isinstance(text_array, str):
            full_text = text_array
        
        # Find the index of the substring in the full text
        match_index = full_text.find(substring)
        if match_index != -1:
            start_index = max(0, match_index - padding)
            end_index = min(len(full_text), match_index + len(substring) + padding)
            return full_text[start_index:end_index]
        return None
    
    def split_by_n(self, sequence, n):
        """
        Generator that splits a list (sequence) into n (approximately) equal chunks.
        e.g., [1,2,3,4,5,6,7,8,9],3 => [[1,2,3], [4,5,6], [7,8,9]]
        
        Safely handles cases where n is 0 or sequence is empty.
        """
        if not sequence:
            # Return the empty sequence as a single chunk
            yield sequence
            return
        
        # Always return at least one chunk
        n = max(1, n)
        n = min(n, len(sequence))
        
        chunk_size, remainder = divmod(len(sequence), n)
        
        # Debug info
        self.logger.debug(f"Splitting sequence of length {len(sequence)} into {n} chunks")
        self.logger.debug(f"Chunk size: {chunk_size}, remainder: {remainder}")
        
        for i in range(n):
            start_idx = i * chunk_size + min(i, remainder)
            end_idx = (i + 1) * chunk_size + min(i + 1, remainder)
            
            self.logger.debug(f"Chunk {i+1}: indices {start_idx} to {end_idx}")
            yield sequence[start_idx:end_idx]
    
    def generate_system_prompt(self, pretext, entities, do_count=True, book_prompt_template=None, provider=None):
        """
        Generate the system (instruction) prompt for translation, incorporating any discovered entities.
        
        Args:
            provider: The model provider instance (used to detect Gemini and remove schema)
        """
        # Debug info
        self.logger.debug(f"generate_system_prompt: type of pretext = {type(pretext)}")
        if isinstance(pretext, list) and len(pretext) > 0:
            self.logger.debug(f"First line: {pretext[0][:50]}")
    
        # Ensure all entity categories exist
        for category in ['characters', 'places', 'organizations', 'abilities', 'titles', 'equipment']:
            entities.setdefault(category, {})
    
        end_entities = {}
        for category in ['characters', 'places', 'organizations', 'abilities', 'titles', 'equipment']:
            entities.setdefault(category, {})
    
        end_entities = {}
        end_entities['characters'] = self.entity_manager.entities_inside_text(pretext, entities['characters'], "THIS CHAPTER", do_count)
        end_entities['places'] = self.entity_manager.entities_inside_text(pretext, entities['places'], "THIS CHAPTER", do_count)
        end_entities['organizations'] = self.entity_manager.entities_inside_text(pretext, entities['organizations'], "THIS CHAPTER", do_count)
        end_entities['abilities'] = self.entity_manager.entities_inside_text(pretext, entities['abilities'], "THIS CHAPTER", do_count)
        end_entities['titles'] = self.entity_manager.entities_inside_text(pretext, entities['titles'], "THIS CHAPTER", do_count)
        end_entities['equipment'] = self.entity_manager.entities_inside_text(pretext, entities['equipment'], "THIS CHAPTER", do_count)

        entities_json = json.dumps(end_entities, ensure_ascii=False, indent=4)
        
        # Load the appropriate template
        if book_prompt_template:
            # Use the custom template for this book
            prompt = book_prompt_template
        else:
            # Try to load prompt from file
            prompt_file_path = os.path.join(self.config.script_dir, "system_prompt.txt")
            
            try:
                if os.path.exists(prompt_file_path):
                    with open(prompt_file_path, 'r', encoding='utf-8') as file:
                        # Read lines and filter out comments
                        lines = [line for line in file.readlines() if not line.strip().startswith('#')]
                        prompt = ''.join(lines)
                        
                    self.logger.info(f"Loaded system prompt from {prompt_file_path}")
            except Exception as e:
                self.logger.error(f"Error loading system prompt from file: {e}")
                self.logger.error(f"You're going to need to redownload the system_prompt.txt from the github or create your own")
                exit(1)

        # Insert the entities JSON into the template (both default and custom)
        prompt = prompt.replace("{{ENTITIES_JSON}}", entities_json)

        # For Gemini providers, remove the JSON schema example to avoid conflicts with responseSchema
        if provider and hasattr(provider, 'provider_name') and 'Gemini' in provider.provider_name:
            # Remove the section between ++++ Response Template Example and ++++ Response Template End
            import re
            pattern = r'\+\+\+\+ Response Template Example.*?\+\+\+\+ Response Template End'
            prompt = re.sub(pattern, '', prompt, flags=re.DOTALL)
            self.logger.debug("Removed JSON schema template for Gemini provider")

        return prompt
    
    def combine_json_chunks(self, chunk1_data, chunk2_data, current_chapter):
        """
        Combine two JSON-like chapter data chunks into one by merging their
        content, summary, and entities. 'current_chapter' is used to update
        the 'last_chapter' field.
        """
        if not chunk1_data:
            return chunk2_data
        if not chunk2_data:
            return chunk1_data
        
        chunk1_data.setdefault("entities", {})
        chunk2_data.setdefault("entities", {})
        
        chunk1_data.setdefault("content", [])
        chunk2_data.setdefault("content", [])
        chunk1_data["content"].extend(chunk2_data["content"])
        
        chunk1_data["summary"] = f"{chunk1_data.get('summary', '')} {chunk2_data.get('summary', '')}".strip()
        
        # Process each entity category
        for category, entities in chunk2_data.get("entities", {}).items():
            chunk1_data["entities"].setdefault(category, {})
            for key, data in entities.items():
                # Check if this entity already exists in another category
                entity_exists_elsewhere = False
                
                for other_category in chunk1_data["entities"]:
                    if other_category != category and key in chunk1_data["entities"][other_category]:
                        # This entity key already exists in a different category
                        entity_exists_elsewhere = True
                        self.logger.warning(f"Duplicate entity '{key}' found in both '{category}' and '{other_category}'")
                        
                        # Check if the translations match
                        existing_translation = chunk1_data["entities"][other_category][key].get("translation")
                        new_translation = data.get("translation")
                        
                        if existing_translation != new_translation:
                            self.logger.warning(f"Entity translations don't match: '{existing_translation}' vs '{new_translation}'")
                        break
                
                if entity_exists_elsewhere:
                    # Skip adding this entity to avoid duplication
                    continue
                
                # Check if the translation already exists in any category
                translation = data.get("translation", "")
                translation_exists = False
                if translation:
                    for check_category, check_entities in chunk1_data["entities"].items():
                        for check_key, check_data in check_entities.items():
                            if check_data.get("translation") == translation and check_key != key:
                                translation_exists = True
                                self.logger.warning(f"Entity translation '{translation}' already exists for key '{check_key}' in '{check_category}'")
                                break
                        if translation_exists:
                            break
                
                if translation_exists:
                    # Skip adding this entity to avoid translation duplication
                    # or optionally, we could add with a modified translation
                    # data["translation"] = f"{translation} (alt)"
                    continue
                
                # Add the entity if it doesn't exist elsewhere
                if key not in chunk1_data["entities"][category]:
                    # Add new entity
                    chunk1_data["entities"][category][key] = {
                        "translation": data["translation"],
                        "last_chapter": current_chapter,
                    }
                    # Add optional fields
                    if "gender" in data:
                        chunk1_data["entities"][category][key]["gender"] = data["gender"]
                    if "incorrect_translation" in data:
                        chunk1_data["entities"][category][key]["incorrect_translation"] = data["incorrect_translation"]
                else:
                    # Update existing entity's last_chapter field
                    chunk1_data["entities"][category][key]["last_chapter"] = current_chapter
        
        return chunk1_data
    
    def get_translation_options(self, node, untranslated_text):
        """
        Asks the LLM for translation options for an entity node.
        Also checks for potential duplicates of suggested translations.
        
        Parameters:
        node(dict): JSON data corresponding to one entity
        untranslated_text(array): lines of untranslated text, optional. will provide additional context to LLM
        
        Returns:
        dict: A dictionary with message and options for translation
        """
        context = self.find_substring_with_context(untranslated_text, node['untranslated'], 35)
        node['context'] = context
        
        # Check if there are existing translations that might conflict
        existing_duplicates = []
        try:
            # We'll look for similar translations to warn the user
            conn = sqlite3.connect(self.entity_manager.db_path)
            cursor = conn.cursor()
            
            # Get current translations that might be similar (same starting character)
            current_untranslated = node['untranslated']
            first_char = current_untranslated[0] if current_untranslated else ''
            
            cursor.execute('''
            SELECT translation, category, untranslated 
            FROM entities 
            WHERE untranslated != ? AND category != ? AND untranslated LIKE ?
            ''', (node['untranslated'], node.get('category', ''), first_char + '%'))
            
            results = cursor.fetchall()
            conn.close()
            
            # If we have results, include them in the node so the LLM can avoid them
            if results:
                node['existing_translations'] = [
                    {'translation': trans, 'category': cat, 'untranslated': unt}
                    for trans, cat, unt in results
                ]
                
                # Find exact duplicates for later warning
                current_translation = node.get('translation', '')
                if current_translation:
                    existing_duplicates = [
                        {'translation': trans, 'category': cat, 'untranslated': unt}
                        for trans, cat, unt in results
                        if trans.lower() == current_translation.lower()
                    ]
        except Exception as e:
            self.logger.error(f"Error checking for duplicate translations: {e}")
        
        # Use the advice model for this
        advice_provider, advice_model_name = self.config.get_client(self.config.advice_model)
        # Modify the prompt to include awareness of duplicates
        prompt = """Your task is to offer translation options. Below in the user text is a JSON node consisting of a translation you have performed previously, which may include "context" which is 20-50 characters before and after the untranslated text. The user did not like the translation and wants to change it, so please offer three alternatives, as well as a short message (less than 200 words) about the untranslated Chinese characters and why you chose to translate it this way. 

    You should include a very literal translation of each character in your message, but not necessarily in your alternatives, unless the translation is phonetic (foreign words). Order the alternatives by your preference, use the context to more finely tune your advice if it is offered.

    One of the most common rejections of translations is simply transliterating, so if if you transliterated last time, do not do so this time.

    IMPORTANT: If "existing_translations" is provided in the node, AVOID suggesting translations that are identical or very similar to these existing translations, as this would cause confusion. If you see similar translations, try to make your suggestions clearly distinct.

    Your output should be in this schema:
    {
    "message": "Your message to the user",
    "options": ["translation option 1", "translation option 2", "translation option 3"]
    }

    Do not include your original translation option among the three options.
    """
        
        dumped_node = json.dumps(node, indent=4, ensure_ascii=False)
        print(dumped_node)
        
        response = advice_provider.chat_completion(
            messages=[
                {
                    "role": "system",
                    "content": prompt
                },
                {
                    "role": "user",
                    "content": dumped_node
                }
            ],
            model=advice_model_name,
            temperature=1,
            top_p=1,
            response_format={"type": "json_object"}
        )
        
        try:
            response_content = advice_provider.get_response_content(response)
            parsed_response = json.loads(response_content)
            
            # If we found duplicates earlier, append a warning to the message
            if existing_duplicates:
                duplicate_warning = "\n\nWARNING: The current translation conflicts with existing entities:"
                for dup in existing_duplicates:
                    duplicate_warning += f"\n- '{dup['untranslated']}' in '{dup['category']}' (also translated as '{dup['translation']}')"
                duplicate_warning += "\nConsider choosing a more distinctive translation to avoid confusion."
                
                parsed_response['message'] = parsed_response['message'] + duplicate_warning
        except json.JSONDecodeError as e:
            print("Failed to parse JSON. Payload:")
            print(response_content)
            print(f"Error: {e}")
            return {'message': f'The translation failed: {e}', 'options': []}
        
        return parsed_response
    
    def translate_chapter(self, chapter_text, book_id=None, stream=True):
        """
        Translate a chapter of text using the configured LLM.
        
        Args:
            chapter_text (list of str): The chapter's text content split into lines.
            
        Returns:
            dict: A dictionary containing the translated chapter data.
        """
        # Initialize current_chapter to a default value
        current_chapter = 0
        total_input_chars = 0
        total_output_tokens = 0
        average_ratio = 1.0
        book_prompt_template = None
        if book_id:
            book_prompt_template = self.entity_manager.get_book_prompt_template(book_id)

        provider, model_name = self.config.get_client(self.config.translation_model)
        self.logger.debug(f"Using translation model: {self.config.translation_model}")
        self.logger.debug(f"Provider initialized: {provider.provider_name}")
        self.logger.debug(f"translate_chapter called with text of {len(chapter_text)} lines")

        # Handle empty input
        if not chapter_text:
            self.logger.warning("Empty text provided for translation. Nothing to translate.")
            return {
                "end_object": {"title": "Empty Chapter", "chapter": 0, "content": [], "entities": {}},
                "new_entities": {},
                "totally_new_entities": {},
                "old_entities": self.entity_manager.entities.copy(),
                "real_old_entities": self.entity_manager.entities.copy(),
                "current_chapter": 0,
                "total_char_count": 0
            }

        total_char_count = sum(len(line) for line in chapter_text)

        # Use entities from SQLite database
        old_entities = self.entity_manager.entities.copy()
        for category in ['characters', 'places', 'organizations', 'abilities', 'titles', 'equipment']:
            old_entities.setdefault(category, {})

        real_old_entities = old_entities

        # Calculate chunks count, ensuring at least 1 chunk
        max_chars = self.config.get_max_chars(self.config.translation_model)
        chunks_count = max(1, math.ceil(total_char_count / max_chars))

        # Generate the initial system prompt
        system_prompt = self.generate_system_prompt(chapter_text, old_entities, 
                                               book_prompt_template=book_prompt_template, provider=provider)

        # Split the text into chunks for the LLM if necessary due to output token limits
        split_text = list(self.split_by_n(chapter_text, chunks_count))

        self.logger.debug(f"Text split into {len(split_text)} chunks")

        if len(split_text) == 0:
            self.logger.error("Error: Text was split into 0 chunks. This should never happen.")
            # Create a single chunk with the entire text as a fallback
            split_text = [chapter_text]
            self.logger.debug("Created fallback chunk with entire text")

        if len(split_text) > 1:
            self.logger.info(f"Input text is {total_char_count} characters. Splitting text into {len(split_text)} chunks.")
        
        end_object = {}

        self.logger.debug("Initializing totally_new_entities")
        totally_new_entities = {}
        self.entity_manager.save_json_file(f"{self.config.script_dir}/prompt.tmp", system_prompt)
        
        self.logger.debug(f"About to process {len(split_text)} chunks")
        for chunk_index, chunk in enumerate(split_text, 1):
            self.logger.debug(f"Processing chunk {chunk_index} of {len(split_text)}")
            chunk_str = "\n".join(chunk)
            user_text = "Translate the following into English: \n" + chunk_str
            total_input_chars += len(chunk_str)
            self.logger.debug(f"TransEng> Stream mode is {stream}")
            if stream:
                # Streaming API call
                print(f"\nTranslating chunk {chunk_index} of {len(split_text)}")
                
                response_text = ""
                token_count = 0
                start_time = time.time()
                try:
                    ratio_file = os.path.join(self.config.script_dir, "token_ratios.json")
                    if os.path.exists(ratio_file):
                        with open(ratio_file, 'r') as f:
                            ratios = json.load(f)
                            average_ratio = ratios.get("average", 1.0)
                except Exception as e:
                    self.logger.warning(f"Could not load token ratios: {e}")
                    average_ratio = 1.0
                expected_tokens = len(chunk_str) * average_ratio 
                print(f"Based on {total_input_chars} input characters * {average_ratio:.2f} (our historic average ratio) we expect {expected_tokens:.0f} tokens.")
                response_stream = provider.chat_completion(
                    messages=[
                        {
                            "role": "system",
                            "content": system_prompt
                        },
                        {
                            "role": "user",
                            "content": user_text
                        }
                    ],
                    model=model_name,
                    temperature=1,
                    top_p=1,
                    response_format={"type": "json_object"},
                    stream=True
                )
                
                # Get progress bar width based on terminal size
                terminal_width = 80
                try:
                    import shutil
                    terminal_width = shutil.get_terminal_size().columns
                except:
                    pass
                progress_width = min(50, terminal_width - 30)
                
                # Process streaming response
                for chunk in response_stream:
                    content = provider.get_streaming_content(chunk)
                    if content:
                        response_text += content
                        token_count += 1
                        
                        # Update progress display
                        if token_count % 10 == 0:  # Update every 10 tokens
                            elapsed = time.time() - start_time
                            tokens_per_second = token_count / elapsed if elapsed > 0 else 0
                            completion_percentage = min(100, (token_count / expected_tokens) * 100) if expected_tokens > 0 else 0
                            progress_bar = "█" * int(completion_percentage / 2) + "░" * (50 - int(completion_percentage / 2))
                            print(f"\r[{progress_bar}] {token_count}/{int(expected_tokens)} tokens ({completion_percentage:.1f}%) - {elapsed:.1f}s elapsed", end="")
                    
                    # Check if stream is complete
                    if provider.is_stream_complete(chunk):
                        break
                print("")
                total_output_tokens += token_count
                self.logger.info(f"Chunk {chunk_index}/{len(split_text)} - Input chars: {len(chunk_str)}, Output tokens: {token_count}, Ratio: {token_count / len(chunk_str):.2f}")
                print("\rTranslation complete. Parsing response...                 ")
                
                # Parse the completed response
                try:
                    parsed_chunk = json.loads(response_text)
                except json.JSONDecodeError as e:
                    print("Failed to parse JSON. Payload:")
                    print(response_text[:500] + "..." if len(response_text) > 500 else response_text)
                    print(f"Error: {e}")
                    exit(1)
            else:
                self.logger.debug(f"Processing chunk {chunk_index} of {len(split_text)}")
                chunk_str = "\n".join(chunk)
                user_text = "Translate the following into English: \n" + chunk_str
                self.logger.debug(f"About to call {self.config.translation_model} with chunk {chunk_index} of {len(split_text)}")
                response = provider.chat_completion(
                    messages=[
                        {
                            "role": "system",
                            "content": system_prompt
                        },
                        {
                            "role": "user", 
                            "content": user_text
                        }
                    ],
                    model=model_name,
                    temperature=1,
                    top_p=1,
                    response_format={"type": "json_object"}
                )
                try:
                    response_content = provider.get_response_content(response)
                    parsed_chunk = json.loads(response_content)
                except json.JSONDecodeError as e:
                    print("Failed to parse JSON. Payload:")
                    print(response_content)
                    print(f"Error: {e}")
                    exit(1)
            
            self.logger.info(f"Translation of chunk {chunk_index} complete.")
            self.logger.debug(f"API call completed for chunk {chunk_index}")
            
            current_chapter = parsed_chunk['chapter']
            
            end_object = self.combine_json_chunks(end_object, parsed_chunk, current_chapter)
            
            # Find new entities in this chunk and record them in totally_new_entities as a running total
            new_entities_this_chunk = self.entity_manager.find_new_entities(real_old_entities, end_object['entities'])
            totally_new_entities = self.entity_manager.combine_json_entities(totally_new_entities, new_entities_this_chunk)
            
            # Update old_entities with the newly processed chunk's combined entities
            old_entities = self.entity_manager.combine_json_entities(old_entities, end_object['entities'])
            
            # Regenerate the system prompt for the next chunk to maintain consistency
            system_prompt = self.generate_system_prompt(chapter_text, old_entities, do_count=False, provider=provider)
        
        self.logger.debug("Finished processing all chunks")

        if total_input_chars > 0:
            ratio = total_output_tokens / total_input_chars
            self.logger.info(f"Chapter completion - Total input chars: {total_input_chars}, Total output tokens: {total_output_tokens}, Overall ratio: {ratio:.2f}")
            # Save this ratio for future reference
            try:
                # Create or update a JSON file with historical ratios
                ratio_file = os.path.join(self.config.script_dir, "token_ratios.json")
                if os.path.exists(ratio_file):
                    with open(ratio_file, 'r') as f:
                        ratios = json.load(f)
                else:
                    ratios = {"ratios": [], "average": 0.9}
                    
                # Add new ratio
                ratios["ratios"].append(ratio)
                ratios["average"] = sum(ratios["ratios"]) / len(ratios["ratios"])
                ratios["samples"] = len(ratios["ratios"])
                
                # Save updated ratios
                with open(ratio_file, 'w') as f:
                    json.dump(ratios, f)
                    
                self.logger.info(f"Updated token ratio statistics - Current average: {ratios['average']:.2f} based on {ratios['samples']} samples")
            except Exception as e:
                self.logger.error(f"Failed to save token ratio statistics: {e}")
        
        # Check for duplicate entities based on translation value
        self._check_for_translation_duplicates(end_object['entities'])
        
        # Ensure all entity categories exist
        new_entities = {
            "characters": end_object.get('entities', {}).get('characters', {}),
            "places": end_object.get('entities', {}).get('places', {}),
            "organizations": end_object.get('entities', {}).get('organizations', {}),
            "abilities": end_object.get('entities', {}).get('abilities', {}),
            "titles": end_object.get('entities', {}).get('titles', {}),
            "equipment": end_object.get('entities', {}).get('equipment', {})
        }

        return {
            "end_object": end_object,
            "new_entities": new_entities,
            "totally_new_entities": totally_new_entities,
            "old_entities": old_entities,
            "real_old_entities": real_old_entities,
            "current_chapter": current_chapter,
            "total_char_count": total_char_count
        }
    def _check_for_translation_duplicates(self, entities_dict):
        """
        Check for duplicate translations across different categories or within the same category
        and log warnings for manual review.
        
        Args:
            entities_dict (dict): Dictionary of entities organized by category
        """
        # Create a mapping of translations to their sources
        translation_map = {}
        
        for category, entities in entities_dict.items():
            for key, data in entities.items():
                translation = data.get('translation', '')
                if not translation:
                    continue
                
                if translation in translation_map:
                    # Found a duplicate translation
                    prev_category, prev_key = translation_map[translation]
                    self.logger.warning(f"Duplicate translation '{translation}' found:")
                    self.logger.warning(f"  - {prev_category}: {prev_key}")
                    self.logger.warning(f"  - {category}: {key}")
                else:
                    # Add this translation to the map
                    translation_map[translation] = (category, key)

