import json
import os  # Import os module
from openai import OpenAI
import math
import pyperclip
import copy
import argparse
from dotenv import load_dotenv

load_dotenv()

api_token = os.getenv("OPENAI_KEY")
debug_mode = os.getenv("DEBUG") == "True"

MODEL_NAME = "gpt-4o"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__)) + "/"
MAX_CHARS = 2400  # We'll split text into chunks if it's above this character count.

client = OpenAI(api_key=api_token)


def combine_json_entities(old_entities, new_entities):
    """
    Merges two JSON-like dictionaries, updating 'old_entities' with entries
    from 'new_entities'. The keys are entity categories, and values are dictionaries
    of untranslated-translated pairs. Entries from 'new_entities' will replace
    existing ones from 'old_entities' if they have the same keys.
    """
    for category in ['characters', 'places', 'organizations', 'abilities', 'equipment']:
        old_category_dict = old_entities.get(category, {})
        new_category_dict = new_entities.get(category, {})

        old_category_dict.update(new_category_dict)
        old_entities[category] = old_category_dict

    return old_entities


def entities_inside_text(text_lines, all_entities, current_chapter, do_count=True):
    """
    Extracts entities mentioned in the given text and updates their running count and last chapter.

    Args:
        text_lines (list of str): The chapter's text content split into lines.
        all_entities (dict): The complete entities dictionary with global counts.
        current_chapter (int or str): The current chapter number.
        do_count (bool): Defaults to True. Set to False if regenerating system prompt to avoid double counting.

    Returns:
        dict: A filtered dictionary of entities mentioned in the text with updated global counts and last chapter.
    """
    found_entities = {}

    combined_text = " ".join(text_lines)

    for key, value in all_entities.items():
        if key in combined_text:
            occurrence_count = combined_text.count(key)

            # If already found in this pass, just increment
            if key in found_entities:
                if do_count:
                    found_entities[key]["count"] += occurrence_count
            else:
                # Initialize new entity record
                if do_count:
                    existing_count = value.get("count", 0) + occurrence_count
                else:
                    existing_count = value.get("count", 0)

                found_entities[key] = {
                    "translation": value["translation"],
                    "count": existing_count,
                    "last_chapter": current_chapter
                }

            # Update global tracking in original entity dictionary
            all_entities[key]["count"] = found_entities[key]["count"]
            all_entities[key]["last_chapter"] = current_chapter

    return found_entities


def find_new_entities(old_data, new_data):
    """
    Return a dictionary of all entities that are present in new_data
    but do NOT exist in old_data at all.
    """
    newly_added = {}

    for category, new_items in new_data.items():
        if category not in old_data:
            newly_added[category] = new_items
            continue

        for entity_name, entity_info in new_items.items():
            if entity_name not in old_data[category]:
                if category not in newly_added:
                    newly_added[category] = {}
                newly_added[category][entity_name] = entity_info

    return newly_added


def split_by_n(sequence, n):
    """
    Generator that splits a list (sequence) into n (approximately) equal chunks.
    e.g., [1,2,3,4,5,6,7,8,9],3 => [[1,2,3], [4,5,6], [7,8,9]]
    """
    n = min(n, len(sequence))
    chunk_size, remainder = divmod(len(sequence), n)
    return (
        sequence[i * chunk_size + min(i, remainder):(i + 1) * chunk_size + min(i + 1, remainder)]
        for i in range(n)
    )


def generate_system_prompt(pretext, entities, do_count=True):
    """
    Generate the system (instruction) prompt for translation, incorporating any discovered entities.
    """
    end_entities = {}
    end_entities['characters'] = entities_inside_text(pretext, entities['characters'], "THIS CHAPTER", do_count)
    end_entities['places'] = entities_inside_text(pretext, entities['places'], "THIS CHAPTER", do_count)
    end_entities['organizations'] = entities_inside_text(pretext, entities['organizations'], "THIS CHAPTER", do_count)
    end_entities['abilities'] = entities_inside_text(pretext, entities['abilities'], "THIS CHAPTER", do_count)
    end_entities['equipment'] = entities_inside_text(pretext, entities['equipment'], "THIS CHAPTER", do_count)

    entities_json = json.dumps(end_entities, ensure_ascii=False, indent=4).encode('utf8')
    prompt = """Your task is to translate the provided material into English, preserving the original content, title, and entities. Focus on semantic accuracy, cultural relevance, and stylistic fidelity.
Key Guidelines:

    Content:
        Translate all content without summarizing. Double-space lines for clarity.
        Ensure the translation reflects the meaning, tone, and flow of the original text, including slang, idioms, and subtle nuances.
        Use double quotes for speech and maintain correct English grammar, syntax, and tenses.
        Retain formatting symbols (e.g., 【】) unless specified otherwise.
        NEVER Summarize "content"! Always translate!
        Prioritize meaningful translation over literal transliteration (e.g., 天海国 → "Heavenly Sea Kingdom").
        This is a xianxia/xuanhuan novel, so prioritise translating words and phrases in a way that emphasises the Eastern Martial Arts. For example, translate 打拳 as "practicing martial arts" or "practicing fist techniques" instead of "practicing boxing."

    Entities:
        Always translate proper nouns (characters, places, organizations, etc.).
        Translate most place names meaningfully (e.g., 黑风镇 → "Black Wind Town").
        Use provided pre-translated entities for consistency; translate new ones as required.
        Categories: CHARACTERS, PLACES, ORGANIZATIONS, ABILITIES, EQUIPMENT.
        If there are no entities to put in the category then just leave it blank but include the full JSON empty dictionary format:
{}

    Entities Format:
        Use this JSON format for entities:

Translate entities accurately, ensuring their relevance and significance in the context of the text.

Here is a list of pre-translated entities, in JSON. If and when you see these nouns in this text, please translate them as provided for a consistent translation experience. If an entity (as described above) is not in this list, translate it yourself:

ENTITIES: """ + entities_json.decode() + '\n' + """

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
            \"钟岳\": {\"translation\": \"Zhong Yue\", \"gender\":\"male\", \"count\": 10, \"last_chapter\": 3},
            \"夏儿\": {\"translation\": \"Xia'er\", \"gender\":\"female\", \"count\": 5, \"last_chapter\": 3},
            \"方剑\": {\"translation\": \"Fang Jian\", \"gender\":\"male\", \"count\": 3, \"last_chapter\": 2}
        },
        \"places\": {
            \"剑门山\": {\"translation\": \"Jianmen Mountain\", \"count\": 4, \"last_chapter\": 3},
            \"大荒\": {\"translation\": \"Great Wilderness\", \"count\": 6, \"last_chapter\": 3},
            \"染霜城\": {\"translation\": \"Frostveil City\", \"count\": 2, \"last_chapter\": 75
        }
        },
        \"organizations\": {
            \"风氏\": {\"translation\": \"Feng Clan\", \"count\": 7, \"last_chapter\": 3}
        },
        \"abilities\": {
            \"太极拳\": {\"translation\": \"Supreme Ultimate Fist\", \"count\": 12, \"last_chapter\": 3},
            \"天级上品武技·星陨斩\": {\"translation\": \"High-level Heaven Rank Martial Skill: Starfall Slash\", \"count\": 9, \"last_chapter\": 2}
        },
        \"equipment\": {
            \"蓝龙药鼎\": {\"translation\": \"Azure Dragon Medicinal Cauldron\", \"count\": 4, \"last_chapter\": 3},
            \"血魔九影剑\": {\"translation\": \"Blood Demon Nine Shadows Sword\", \"count\": 2, \"last_chapter\": 1}
        }
    }
}
---

### Key Notes:
1. **Content**: The `content` array must include the full textual content of the chapter, formatted exactly as given, with line breaks preserved. DO NOT summarize or alter the content.
2. **Chapter**: The chapter number, as an integer. Provide a good guess based on the initial translation.
3. **Summary**: Provide a concise summary of no more than 75 words for the chapter.
4. **Entities**: The `entities` section should include all relevant `characters`, `places`, `organizations`, `abilities`, and `equipment`. Each entry must:
    - Equipment can include things like weapons, tools, potions, and special resources. It's not limited to things that have to be carried.
    - Use the untranslated name as the key.
    - Include:
        - \"translation\": The accurate and consistent translated name or term.
        - \"gender\": CHARACTER exclusive attribute. female, male, or neither. Used to keep pronouns consistent since Chinese doesn't have gendered pronouns
        - \"count\": The total occurrences of the entity in the story up to and including this chapter. The higher, the more important the character, item, place, etc
        - \"last_chapter\": You only see entities if they are in this chapter, so this will always be THIS CHAPTER for you.
5. **Ensure Consistency**: Check for existing entities in the pre-translated entities list above. Only add new entities or update existing ones if necessary.
6. **Formatting**: The output must strictly adhere to JSON formatting standards to ensure proper parsing.
"""
    return prompt


def combine_json_chunks(chunk1_data, chunk2_data, current_chapter):
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

    for category, entities in chunk2_data.get("entities", {}).items():
        chunk1_data["entities"].setdefault(category, {})
        for key, data in entities.items():
            if key not in chunk1_data["entities"][category]:
                # Add new entity
                chunk1_data["entities"][category][key] = {
                    "translation": data["translation"],
                    "count": data.get("count", 0),
                    "last_chapter": current_chapter,
                }
            else:
                # Update existing entity
                chunk1_data["entities"][category][key]["count"] += data.get("count", 0)
                chunk1_data["entities"][category][key]["last_chapter"] = current_chapter

    return chunk1_data

def review_and_edit_entries(data):
    """
    Displays items from the JSON schema to the user, allows review, and facilitates changes.

    Parameters:
    data (dict): JSON data containing categories like characters, places, etc.

    Returns:
    dict: A dictionary containing only the edited entries.
    """
    edited_data = {}

    while True:
        print("\nTotally New Entities In This Chapter:")
        print(json.dumps(data, indent=4, ensure_ascii=False))

        make_changes = input("\nDo you want to make any changes? (yes to proceed, no to exit): ").strip().lower()
        if make_changes != 'yes':
            break

        print("\nCategories:")
        categories = list(data.keys())
        for i, category in enumerate(categories):
            print(f"  {i + 1}. {category}")

        category_choice = input("\nSelect a category to edit (enter number, or press Enter to stop): ").strip()
        if not category_choice.isdigit() or int(category_choice) < 1 or int(category_choice) > len(categories):
            print("Exiting changes.")
            break

        selected_category = categories[int(category_choice) - 1]
        items = data[selected_category]

        if not items:
            print(f"No items in category '{selected_category}'.")
            continue

        print(f"\nItems in '{selected_category}':")
        item_keys = list(items.keys())
        for i, key in enumerate(item_keys):
            print(f"  {i + 1}. {key} ({items[key]['translation']})")

        item_choice = input("\nSelect an item to edit (enter number, or press Enter to stop): ").strip()
        if not item_choice.isdigit() or int(item_choice) < 1 or int(item_choice) > len(item_keys):
            print("Exiting changes.")
            continue

        selected_item_key = item_keys[int(item_choice) - 1]
        selected_item = items[selected_item_key]

        print(f"\nEditing item: {selected_item_key}")
        item_edited = False
        fields = list(selected_item.keys())  # Create a list of keys
        i = 0
        while i < len(fields):
            field = fields[i]
            value = selected_item[field]
            if field == "translation":
                yesno = input("\nDo you want to ask the LLM for translation options? (yes to proceed, no to exit): ").strip().lower()
                if yesno == 'yes' or yesno == 'y':
                    node = selected_item.copy()
                    node['category'] = selected_category
                    node['untranslated'] = selected_item_key
                    advice = get_translation_options(node)
                    print(MODEL_NAME + " says, \"" + advice['message'] + "\"") # this is a message directly from the model
                    options = advice['options'] # an array of 3 alternative options generated by the model
                    options.append("Custom Translation [Your Input]")
                    options_keys = list(range(len(options)))
                    for i, key in enumerate(options):
                        print(f"  {i + 1}. {key}")
                    option_choice = input("\nSelect an item to edit (enter number, or press Enter to keep original translation): ").strip()
                    if not option_choice.isdigit() or int(option_choice) < 1 or int(option_choice) > len(options):
                        print("Keeping original translation:")
                        new_value = False
                    else:
                        selected_option_key = options_keys[int(option_choice) - 1]
                        selected_option_item = options[selected_option_key]
                        if selected_option_item == "Custom Translation [Your Input]":
                            selected_option_item = input("Enter your translation:")
                        new_value = selected_option_item
                else:
                    print(f"  {field}: {value}")
                    new_value = input(f"    Update {field} (press Enter to keep current value): ").strip()
            else:
                print(f"  {field}: {value}")
                new_value = input(f"    Update {field} (press Enter to keep current value): ").strip()

            if new_value:
                item_edited = True
                if isinstance(value, int):
                    try:
                        selected_item[field] = int(new_value)
                    except ValueError:
                        print(f"    Invalid input for {field}. Keeping original value.")
                else:
                    selected_item[field] = new_value
            i += 1

        delete_entry = input(f"\nDo you want to delete '{selected_item_key}'? (yes or y to delete, no or enter to keep): ").strip().lower()
        if delete_entry == 'yes' or delete_entry == 'y':
            del items[selected_item_key]
            item_edited = True
            print(f"Item '{selected_item_key}' deleted.")

        if item_edited:
            if selected_category not in edited_data:
                edited_data[selected_category] = {}
            edited_data[selected_category][selected_item_key] = selected_item

    return edited_data

def get_translation_options(node):
    """
    Asks the LLM for translation options for an entity node.

    Parameters:
    node(dict): JSON data corresponding to one entity

    Returns:
    dict: A dictionary in this format:
    {"message":"A message from the LLM about the characters being translated.", "options":["translation option1", "translation option 2", "translation option 3"]}
    """
    prompt = """Your task is to offer translation options. Below in the user text is a JSON node consisting of a translation you have performed previously. The user did not like the translation and wants to change it, so please offer three alternatives, as well as a short message (less than 150 words) about the untranslated Chinese characters and why you chose to translate it this way. You should include a very literal translation of each character in your message, but not necessarily in your alternatives, unless the translation is phonetic (foreign words). Order the alternatives by your preference.
    
    Your output should be in this schema:
    {
    "message": "Your message to the user",
    "options": ["translation option 1", "translation option 2", "translation option 3"]
    }

    Do not include your original translation option among the three options.
    """

    response = client.chat.completions.create(
        model=MODEL_NAME,
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
                        "text": json.dumps(node, indent=4, ensure_ascii=False)
                    }
                ]
            }
        ],
        temperature=1,
        max_tokens=500,
        top_p=1,
        frequency_penalty=0,
        presence_penalty=0,
        response_format={"type": "json_object"}
    )
    try:
        parsed_response = json.loads(response.choices[0].message.content)
    except json.JSONDecodeError as e:
        print("Failed to parse JSON. Payload:")
        print(response.choices[0].message.content)
        print(f"Error: {e}")
        return {'message':f'The translation failed: {e}', 'options':[]}
    return parsed_response

def update_translated_text(translated_text, entity):
    """
    Does a substitution on translated_text, replacing entity['old_translation'] for entity['translation'].

    Parameters:
    translated_text (list of str): An array of strings, the translated text.
    entity (dict): A dictionary in our standard entity format, except this has an additional attribute 'old_translation'.
    """
    old_translation = entity.get('old_translation', '')
    new_translation = entity['translation']

    print(f"We will update {old_translation} for {new_translation}...")
    
    # Iterate through the translated text and replace old translations with the new ones
    for i in range(len(translated_text)):
        translated_text[i] = translated_text[i].replace(old_translation, new_translation)

    return translated_text

def file_to_array(filename):
    with open(filename, 'r', encoding='utf-8') as file:
        lines = file.readlines()
    # Strip newline characters
    return [line.strip() for line in lines]

# Create the parser
parser = argparse.ArgumentParser(description="Process input from clipboard, file, or no input.")

# Create a mutually exclusive group
group = parser.add_mutually_exclusive_group()

# Add arguments to the group
group.add_argument("--clipboard", action="store_true", help="Process input from the clipboard")
group.add_argument("--file", type=str, help="Process input from a specified file")

# Parse arguments
args = parser.parse_args()

# Handle the cases
if args.clipboard:
    print("Processing input from the clipboard.")
    pretext = pyperclip.paste()
elif args.file:
    print(f"Processing input from the file: {args.file}")
    file_to_array(args.file)
else:
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
total_char_count = sum(len(line) for line in pretext)

try:
    file_pointer = open(SCRIPT_DIR + 'entities.json', 'rb')
except OSError:
    old_entities = {"characters": {}, "places": {}, "organizations": {}, "abilities": {}, "equipment": {}}
    with open(SCRIPT_DIR + 'entities.json', 'w') as file_pointer_write:
        json.dump(old_entities, file_pointer_write, indent=4)
    file_pointer = None

if file_pointer:
    with file_pointer:
        old_entities = json.load(file_pointer)
else:
    old_entities = {"characters": {}, "places": {}, "organizations": {}, "abilities": {}, "equipment": {}}

real_old_entities = old_entities


chunks_count = math.ceil(total_char_count / MAX_CHARS)

# Generate the initial system prompt
system_prompt = generate_system_prompt(pretext, old_entities)

# Split the text into chunks for the LLM if necessary due to output token limits
# split_by_n will split into roughly equal chunks
split_text = list(split_by_n(pretext, chunks_count))

if len(split_text) > 1:
    print(f"Input text is {total_char_count} characters. Splitting text into {len(split_text)} chunks.")

end_object = {}
chunk_index = 1

totally_new_entities = {}

for chunk in split_text:
    chunk_str = "\n".join(chunk)
    user_text = "Translate the following into English: \n" + chunk_str

    response = client.chat.completions.create(
        model=MODEL_NAME,
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
        max_tokens=4096,
        top_p=1,
        frequency_penalty=0,
        presence_penalty=0,
        response_format={"type": "json_object"}
    )

    print(f"Translation of chunk {chunk_index} complete.")
    chunk_index += 1

    try:
        parsed_chunk = json.loads(response.choices[0].message.content)
    except json.JSONDecodeError as e:
        print("Failed to parse JSON. Payload:")
        print(response.choices[0].message.content)
        print(f"Error: {e}")
        exit(1)
    current_chapter = parsed_chunk['chapter']

    end_object = combine_json_chunks(end_object, parsed_chunk, current_chapter)

    # find new entities in this chunk and record them in the uninspiredly named "totally_new_entities" as a running total
    new_entities_this_chunk = find_new_entities(real_old_entities, end_object['entities'])
    totally_new_entities = combine_json_entities(totally_new_entities,new_entities_this_chunk)

    # Update old_entities with the newly processed chunk's combined entities
    old_entities = combine_json_entities(old_entities, end_object['entities'])

    # Regenerate the system prompt to keep it updated. this way the second chunk will receive the entities translated in the first chunk
    # to prevent a situation where two chunks translate words differently because they havent been told they're entities yet
    system_prompt = generate_system_prompt(pretext, old_entities, do_count=False)


# Occasionally the LLM will skip returning a part of the dictionary if it is empty.
# This isn't ideal, but we will structure this following line so that it fails gracefully,
# instead substituting empty dictionaries if this happens
new_entities = {
    "characters": end_object.get('entities', {}).get('characters', {}),
    "places": end_object.get('entities', {}).get('places', {}),
    "organizations": end_object.get('entities', {}).get('organizations', {}),
    "abilities": end_object.get('entities', {}).get('abilities', {}),
    "equipment": end_object.get('entities', {}).get('equipment', {})
}

# If there are items in "totally_new_entities" we will give the user an opportunity to review them
# Often times the LLM will put an entity into the wrong category and will need to be deleted
# Other times, the LLM will duplicate an already existing entity, just slightly differently worded
# The LLM also has issues sometimes identifying a gender, which effects pronoun use. Can edit this if it is obviously wrong
# Lastly, the LLM can sometimes provide poor translation, especially in Chinese representations of phonetic English names
# The user will have an opportunity to ask the LLM to provide 3 alternative translations, or just enter their own

# we'll make a temporary copy of the old_entities in order to have something to reference in case we change them in review_and_edit...
oldest_entities = copy.deepcopy(old_entities)

if totally_new_entities != {'characters':{},'places':{},'organizations': {},'abilities':{},'equipment':{}}:
    edited_entities = review_and_edit_entries(totally_new_entities)
else:
    edited_entities = {}

print(json.dumps(edited_entities,indent=4,ensure_ascii=False))

if edited_entities:
    # loop through totally_new_entities 
    for category in edited_entities:
        for key, value in edited_entities[category].items():
            node = value.copy()
            node['old_translation'] = oldest_entities[category][key]['translation']
            end_object['content'] = update_translated_text(end_object['content'], node)

    print("The entities have been edited. If you edited a translated name, we've conducted a substitution to change it in the translated text. Simple changes like that should be fine.")
    print("If they're more complex, like pronouns for a character, then you need to retranslate this chapter. The edited entities will be preserved and used for the retranslation.")

# Convert any "THIS CHAPTER" placeholder in last_chapter to the actual chapter number
for category in new_entities:
    for entity_key, entity_value in end_object['entities'][category].items():
        if entity_value["last_chapter"] == "THIS CHAPTER":
            end_object['entities'][category][entity_key]["last_chapter"] = current_chapter


# Combine final new entities with old_entities
combined_entities = combine_json_entities(old_entities, new_entities)
for entity_key, entity_value in combined_entities[category].items():
    if entity_value["last_chapter"] == "THIS CHAPTER":
        combined_entities[category][entity_key]["last_chapter"] = current_chapter

# Write our output
chapter_title = end_object['title']
filename = chapter_title + ".txt"

translated_total_words = 0
translated_total_chars = 0

end_object['untranslated'] = pretext

with open(SCRIPT_DIR + chapter_title + '.json', 'w') as fp_json:
    json.dump(end_object, fp_json, indent=4)

with open(SCRIPT_DIR + filename, "w") as txt_file:
    for line in end_object['content']:
        translated_total_words += len(line.split())
        translated_total_chars += len(line)
        txt_file.write(line + '\n')

pyperclip.copy("\n".join(end_object['content']))
print(f"TITLE: {end_object['title']}")
print(end_object['summary'])
print("Translated text copied to clipboard for pasting.")

with open(SCRIPT_DIR + 'entities.json', 'w') as fp_entities:
    json.dump(combined_entities, fp_entities, indent=4)

print(
    "Translated. Input text is "
    + str(total_char_count)
    + " characters compared to "
    + str(translated_total_words)
    + " translated words ("
    + str(translated_total_chars)
    + " characters.)"
)
