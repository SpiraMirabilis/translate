from openai import OpenAI
import json
import sqlite3
import math

class TranslationEngine:
    """Core class for handling text translation logic"""
    
    def __init__(self, config: 'TranslationConfig', logger: 'Logger', entity_manager: 'EntityManager'):
        self.config = config
        self.logger = logger
        self.entity_manager = entity_manager
        self.client = config.get_client()
    
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
    
    def generate_system_prompt(self, pretext, entities, do_count=True):
        """
        Generate the system (instruction) prompt for translation, incorporating any discovered entities.
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
        
        # This large template string remains the same as your original implementation
        prompt = """Your task is to translate the provided material into English, preserving the original content, title, and entities. Focus on semantic accuracy, cultural relevance, and stylistic fidelity.
Key Guidelines:

    Content:
        I have permission to translate this content.
        Translate all content without summarizing. Double-space lines for clarity.
        Ensure the translation reflects the meaning, tone, and flow of the original text, including slang, idioms, and subtle nuances.
        Use double quotes for speech and maintain correct English grammar, syntax, and tenses.
        Retain formatting symbols (e.g., 【】) unless specified otherwise.
        NEVER Summarize "content"! Always translate!
        Prioritize meaningful translation over literal transliteration (e.g., 天海国 → "Heavenly Sea Kingdom").
        This is a Chinese xianxia story.
	
    Entities:
        Always translate proper nouns (characters, places, organizations, etc.).
        Translate most place names meaningfully (e.g., 黑风镇 → "Black Wind Town").
        Places, abilities and characters are especially important and should always be incorporated into the new entities record.
        Abilities could encompass skills, techniques, spells, etc
        Use provided pre-translated entities for consistency; translate new ones as required.
        Categories: CHARACTERS, PLACES, ORGANIZATIONS, ABILITIES, TITLES, and EQUIPMENT.
        If there are no entities to put in the category then just leave it blank but include the full JSON empty dictionary format:
{}

    Entities Format:
        Use this JSON format for entities:

Translate entities accurately, ensuring their relevance and significance in the context of the text.

Here is a list of pre-translated entities, in JSON. If and when you see these nouns in this text, please translate them as provided for a consistent translation experience. If an entity (as described above) is not in this list, translate it yourself:

ENTITIES: """ + entities_json + '\n' + """

---

#### Response Template Example

{
    \"title\": \"Chapter 3 - The Great Apcalyptic Battle\",
    \"chapter\": 3,
    \"summary\": \"A concise 75-word or less summary of this chapter. This is the only place where you can summarize.\",
    \"content\": [
        \"This is an example of the great battle. We must remember to NEVER summarise the content.\",
        \"\",
        \"Now we are on a new line to express the fact we should go to new lines.\",
        \"\",
        \"'I wonder what I am supposed to do now.'\",
        \"\",
        \"Now we are on the last line, which shouldn't include any linebreaks.\"
    ],
    \"entities\": {
        \"characters\": {
            \"钟岳\": {\"translation\": \"Zhong Yue\", \"gender\":\"male\", \"last_chapter\": 3},
            \"夏儿\": {\"translation\": \"Xia'er\", \"gender\":\"female\", \"last_chapter\": 3},
            \"方剑\": {\"translation\": \"Fang Jian\", \"gender\":\"male\", \"last_chapter\": 2}
        },
        \"places\": {
            \"剑门山\": {\"translation\": \"Jianmen Mountain\", \"last_chapter\": 3},
            \"大荒\": {\"translation\": \"Great Wilderness\", \"last_chapter\": 3},
            \"染霜城\": {\"translation\": \"Frostveil City\", \"last_chapter\": 75
        }
        },
        \"organizations\": {
            \"风氏\": {\"translation\": \"Feng Clan\", \"last_chapter\": 3}
        },
        \"abilities\": {
            \"太极拳\": {\"translation\": \"Supreme Ultimate Fist\", \"last_chapter\": 3},
            \"天级上品武技·星陨斩\": {\"translation\": \"High-level Heaven Rank Martial Skill: Starfall Slash\", \"last_chapter\": 2}
        },
        \"titles\": {
            \"鉴宝师\": {\"translation\": \"Treasure Appraiser\", \"last_chapter\": 1},
            \"真君\": {\"translation\": \"True Sovereign\", \"last_chapter\": 5},
            \"筑道\": {\"translation\": \"Foundation Establishment\", \"last_chapter\": 7}
        },
        \"equipment\": {
            \"蓝龙药鼎\": {\"translation\": \"Azure Dragon Medicinal Cauldron\", \"last_chapter\": 3},
            \"血魔九影剑\": {\"translation\": \"Blood Demon Nine Shadows Sword\", \"last_chapter\": 1}
        }
    }
}
---

### Key Notes:
1. **Content**: The `content` array must include the full textual content of the chapter, formatted exactly as given, with line breaks preserved. DO NOT summarize or alter the content.
2. **Chapter**: The chapter number, as an integer. Provide a good guess based on the initial translation.
3. **Summary**: Provide a concise summary of no more than 75 words for the chapter.
4. **Entities**: The `entities` section should include all relevant `characters`, `places`, `organizations`, `abilities`, `titles`, and `equipment`. Each entry must:
    - Each entity key inside each category is untranslated text. IMPORTANT: NEVER PLACE AN ENGLISH ENTITY KEY. KEYS ARE UNTRANSLATED.
    - Equipment can include things like weapons, tools, potions, and special resources. It's not limited to things that have to be carried.
    - Use the untranslated name as the key.
    - Include:
        - \"translation\": The accurate and consistent translated name or term.
        - \"gender\": CHARACTER exclusive attribute. female, male, or neither. Used to keep pronouns consistent since Chinese doesn't have gendered pronouns
        - \"last_chapter\": You only see entities if they are in this chapter, so this will always be THIS CHAPTER for you.
        - \"incorrect_translation\": this field only exists if I have corrected your translation of this entity in the past. this is the incorrect translation you made. pay some attention to how your translation was corrected, if you can.
5. **Translation Formatting** In general, do not split sentences with whitespaces. For example: 'Yet deep down, Chen Shaojun felt that this\nwas really important' is wrong. That should be on one line.
6. **Titles** Titles in the entity list should include both obvious titles as well as cultivation ranks or levels.
7. **Ensure Consistency**: Check for existing entities in the pre-translated entities list above. Only add new entities or update existing ones if necessary.
8. **Formatting**: The output must strictly adhere to JSON formatting standards to ensure proper parsing.
"""
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
            
            # Get current translations that might be similar
            cursor.execute('''
            SELECT translation, category, untranslated 
            FROM entities 
            WHERE untranslated != ? AND category != ?
            ''', (node['untranslated'], node.get('category', '')))
            
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

        advice_client, advice_model_name = self.config.get_client(self.config.advice_model)
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
        
        response = advice_client.chat.completions.create(
            model=advice_model_name,
            messages=[
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "text",
                            "text": prompt
                        }
                    ]
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": dumped_node
                        }
                    ]
                }
            ],
            temperature=1,
            top_p=1,
            frequency_penalty=0,
            presence_penalty=0,
            response_format={"type": "json_object"}
        )
        
        try:
            parsed_response = json.loads(response.choices[0].message.content)
            
            # If we found duplicates earlier, append a warning to the message
            if existing_duplicates:
                duplicate_warning = "\n\nWARNING: The current translation conflicts with existing entities:"
                for dup in existing_duplicates:
                    duplicate_warning += f"\n- '{dup['untranslated']}' in '{dup['category']}' (also translated as '{dup['translation']}')"
                duplicate_warning += "\nConsider choosing a more distinctive translation to avoid confusion."
                
                parsed_response['message'] = parsed_response['message'] + duplicate_warning
        except json.JSONDecodeError as e:
            print("Failed to parse JSON. Payload:")
            print(response.choices[0].message.content)
            print(f"Error: {e}")
            return {'message': f'The translation failed: {e}', 'options': []}
        
        return parsed_response
    
    def translate_chapter(self, chapter_text):
        """
        Translate a chapter of text using the configured LLM.
        
        Args:
            chapter_text (list of str): The chapter's text content split into lines.
            
        Returns:
            dict: A dictionary containing the translated chapter data.
        """
        # Initialize current_chapter to a default value
        current_chapter = 0

        client, model_name = self.config.get_client(self.config.translation_model)
        self.logger.debug(f"Using translation model: {self.config.translation_model}")
        self.logger.debug(f"API client initialized: {self.client is not None}")
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
        chunks_count = max(1, math.ceil(total_char_count / self.config.max_chars))

        # Generate the initial system prompt
        system_prompt = self.generate_system_prompt(chapter_text, old_entities)

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
            self.logger.debug(f"About to call {self.config.translation_model} with chunk {chunk_index} of {len(split_text)}")
            response = self.client.chat.completions.create(
                model=model_name,
                messages=[
                    {
                        "role": "system",
                        "content": [
                            {
                                "type": "text",
                                "text": system_prompt
                            }
                        ]
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": user_text
                            }
                        ]
                    }
                ],
                temperature=1,
                top_p=1,
                frequency_penalty=0,
                presence_penalty=0,
                response_format={"type": "json_object"}
            )
            
            self.logger.info(f"Translation of chunk {chunk_index} complete.")
            self.logger.debug(f"API call completed for chunk {chunk_index}")
            self.entity_manager.save_json_file(f"{self.config.script_dir}/response.tmp", response.choices[0].message.content)
            
            try:
                parsed_chunk = json.loads(response.choices[0].message.content)
            except json.JSONDecodeError as e:
                print("Failed to parse JSON. Payload:")
                print(response.choices[0].message.content)
                print(f"Error: {e}")
                exit(1)
            
            current_chapter = parsed_chunk['chapter']
            
            end_object = self.combine_json_chunks(end_object, parsed_chunk, current_chapter)
            
            # Find new entities in this chunk and record them in totally_new_entities as a running total
            new_entities_this_chunk = self.entity_manager.find_new_entities(real_old_entities, end_object['entities'])
            totally_new_entities = self.entity_manager.combine_json_entities(totally_new_entities, new_entities_this_chunk)
            
            # Update old_entities with the newly processed chunk's combined entities
            old_entities = self.entity_manager.combine_json_entities(old_entities, end_object['entities'])
            
            # Regenerate the system prompt for the next chunk to maintain consistency
            system_prompt = self.generate_system_prompt(chapter_text, old_entities, do_count=False)
        
        self.logger.debug("Finished processing all chunks")
        
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

