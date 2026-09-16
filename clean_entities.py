#!/usr/bin/env python3
"""
Entity Database Cleaner - Filters out non-proper nouns from the entities database.

This script reads all entities from the database, sends them to an AI model to classify
which ones are proper nouns (names, places, titles like "Excalibur" or "Jacob's sword"),
and removes generic terms (like "sword", "person", etc.) from the database.

Usage:
    python clean_entities.py [--model MODEL] [--dry-run] [--book-id BOOK_ID]
"""

import argparse
import json
import sys
from typing import Dict, List, Set
from database import DatabaseManager
from providers import create_provider
from config import TranslationConfig
from logger import Logger


def get_all_entities(db_manager: DatabaseManager, book_id: int = None) -> tuple[Dict[str, str], Dict[str, str]]:
    """
    Retrieve all entities from the database.

    Args:
        db_manager: Database manager instance
        book_id: Optional book ID to filter entities

    Returns:
        Tuple of (entity_dict, category_map) where:
        - entity_dict maps untranslated to translated text
        - category_map maps untranslated to category for deletion
    """
    entities_by_category = db_manager.get_all_entities_for_review(book_id)
    entity_dict = {}
    category_map = {}

    for category, entities in entities_by_category.items():
        for untranslated, entity_data in entities.items():
            translated = entity_data['translation']
            entity_dict[untranslated] = translated
            category_map[untranslated] = category

    return entity_dict, category_map


def classify_proper_nouns(entities: Dict[str, str], provider, model: str) -> Set[str]:
    """
    Send entities to AI model to classify which are proper nouns.

    Args:
        entities: Dictionary of untranslated:translated entities
        provider: AI provider instance
        model: Model name to use

    Returns:
        Set of untranslated entity keys that are proper nouns
    """
    # Prepare the prompt
    system_prompt = """You are a linguistic classifier. Your task is to identify which entries in a Chinese-English translation dictionary are ENTITIES.
    Entities are defined as PROPER NOUNS as well as any term that requires consistent translation throughout chapters, with examples below.

Entities include:
- Personal names (人物名, 角色名)
- Personal names that include titles like 骑士拉尔·施耐德, 邓布利多教授, 米兰达女士
- Personal titles that reference a unique person like 霍格沃茨副校长
- Place names (地名)
- Organization names (组织名)
- Unique item names (独特物品名称) like "Excalibur", "Jacob's Sword", "Dragon Slayer Blade"
- Non-unique item names if they are possessed by a unique individual and are referenced possessively like 洛丽丝夫人饼干
- Titles when they function as names (称号作为专有名词时)
- Unique names of subjects, schools or places like 黑魔法防御课,  脱凡成衣店
- Technique/skill names that are treated as proper nouns (功法名, 技能名)
- Unique organisations or groups, like 课后辅导兴趣小组

DO NOT classify as entities:
- Generic terms (普通名词) like "sword", "person", "mountain"
- Common descriptive phrases like "the old man", "a powerful technique"
- General categories like "cultivator", "warrior", "city"

Return ONLY a JSON array of the Chinese (untranslated) keys that are proper nouns.

Example input:
{
  "剑": "sword",
  "诛仙剑": "Immortal Slaying Sword",
  "张三": "Zhang San",
  "人": "person"
}

Example output:
["诛仙剑", "张三"]
"""

    user_prompt = f"""Classify which of these entities are proper nouns. Return only a JSON array of the Chinese keys (untranslated text) for entries that are proper nouns:

{json.dumps(entities, ensure_ascii=False, indent=2)}

Return format: ["key1", "key2", ...]
"""

    print(f"\n🤖 Sending {len(entities)} entities to {model} for classification...")

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
    try:
        # Try to extract JSON array from the response
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

    except (json.JSONDecodeError, ValueError) as e:
        print(f"\n❌ Error parsing AI response: {e}")
        print(f"Response content:\n{content}")
        sys.exit(1)


def preview_deletions(entities: Dict[str, str], to_keep: Set[str]) -> Dict[str, str]:
    """
    Show which entities will be deleted.

    Args:
        entities: All entities
        to_keep: Set of untranslated keys to keep

    Returns:
        Dictionary of entities that will be deleted
    """
    to_delete = {}
    for untranslated, translated in entities.items():
        if untranslated not in to_keep:
            to_delete[untranslated] = translated

    return to_delete


def delete_entities(db_manager: DatabaseManager, to_delete: Dict[str, str], category_map: Dict[str, str]):
    """
    Delete non-proper noun entities from the database.

    Args:
        db_manager: Database manager instance
        to_delete: Dictionary of entities to delete (untranslated:translated)
        category_map: Mapping of untranslated to category
    """
    deleted_count = 0

    for untranslated in to_delete.keys():
        try:
            category = category_map.get(untranslated)
            if category:
                success = db_manager.delete_entity(category, untranslated)
                if success:
                    deleted_count += 1
            else:
                print(f"⚠️  Warning: No category found for '{untranslated}'")
        except Exception as e:
            print(f"⚠️  Error deleting '{untranslated}': {e}")

    print(f"\n✅ Deleted {deleted_count} non-proper noun entities from the database.")


def main():
    parser = argparse.ArgumentParser(
        description="Filter out non-proper nouns from the entities database using AI classification."
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="AI model to use for classification (format: provider:model, e.g., 'oai:gpt-4', 'claude:claude-3-5-sonnet'). Uses TRANSLATION_MODEL from .env if not specified."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview which entities would be deleted without actually deleting them."
    )
    parser.add_argument(
        "--book-id",
        type=int,
        default=None,
        help="Filter entities for a specific book ID only."
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip confirmation prompt and delete automatically."
    )

    args = parser.parse_args()

    print("🧹 Entity Database Cleaner")
    print("=" * 50)

    # Initialize configuration and logger
    config = TranslationConfig()
    logger = Logger(config)

    # Initialize database
    db_manager = DatabaseManager(config, logger)

    # Get all entities
    print(f"\n📚 Loading entities from database{f' (book_id={args.book_id})' if args.book_id else ''}...")
    entities, category_map = get_all_entities(db_manager, args.book_id)

    if not entities:
        print("⚠️  No entities found in the database.")
        return

    print(f"✅ Loaded {len(entities)} entities.")

    # Initialize provider using the same config
    model_spec = args.model or config.translation_model

    try:
        # Parse the model spec to get provider and model name
        provider_name, model = config.parse_model_spec(model_spec)

        # Create provider instance
        provider = create_provider(provider_name)
    except Exception as e:
        print(f"❌ Error initializing AI provider: {e}")
        sys.exit(1)

    # Classify proper nouns
    proper_nouns = classify_proper_nouns(entities, provider, model)

    print(f"\n✅ Classified {len(proper_nouns)} entities as proper nouns.")
    print(f"⚠️  {len(entities) - len(proper_nouns)} entities identified as non-proper nouns.")

    # Preview deletions
    to_delete = preview_deletions(entities, proper_nouns)

    if not to_delete:
        print("\n✨ No non-proper nouns found! Database is clean.")
        return

    # Show what will be deleted
    print("\n📋 Entities to be DELETED (non-proper nouns):")
    print("-" * 50)
    for untranslated, translated in sorted(to_delete.items()):
        print(f"  {untranslated} → {translated}")

    print("\n📋 Entities to be KEPT (proper nouns):")
    print("-" * 50)
    kept_entities = {k: v for k, v in entities.items() if k in proper_nouns}
    for untranslated, translated in sorted(kept_entities.items())[:20]:  # Show first 20
        print(f"  {untranslated} → {translated}")
    if len(kept_entities) > 20:
        print(f"  ... and {len(kept_entities) - 20} more")

    # Dry run or confirm deletion
    if args.dry_run:
        print("\n🔍 DRY RUN MODE - No changes made to database.")
        return

    if not args.yes:
        print(f"\n⚠️  This will DELETE {len(to_delete)} entities from the database.")
        confirm = input("Continue? [y/N]: ").strip().lower()
        if confirm not in ['y', 'yes']:
            print("❌ Aborted.")
            return

    # Delete entities
    delete_entities(db_manager, to_delete, category_map)

    print("\n✨ Entity cleanup complete!")


if __name__ == "__main__":
    main()
