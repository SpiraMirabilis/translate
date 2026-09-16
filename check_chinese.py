import argparse
import json
import os
import sqlite3
import re
import sys

# Allow imports from the project root (providers, config, etc.)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Language-specific patterns for detecting untranslated source text
SOURCE_LANG_PATTERNS = {
    'zh': re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]'),
    'ja': re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff\u3040-\u309f\u30a0-\u30ff]'),
    'ko': re.compile(r'[\uac00-\ud7af\u1100-\u11ff\u3130-\u318f]'),
}

# Chunk patterns for finding contiguous runs of source-language characters
SOURCE_LANG_CHUNK_PATTERNS = {
    'zh': re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]+'),
    'ja': re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff\u3040-\u309f\u30a0-\u30ff]+'),
    'ko': re.compile(r'[\uac00-\ud7af\u1100-\u11ff\u3130-\u318f]+'),
}

LANG_NAMES = {
    'zh': 'Chinese',
    'ja': 'Japanese',
    'ko': 'Korean',
}

# Legacy default — used when no language can be determined
DEFAULT_LANG = 'zh'


def contains_source_chars(text, source_language='zh'):
    """Check if text contains source-language characters"""
    pattern = SOURCE_LANG_PATTERNS.get(source_language)
    if pattern is None:
        return False
    return pattern.search(text)


def find_source_snippets(text, source_language='zh', context_chars=30):
    """Find source-language characters and return them with surrounding context"""
    chunk_pattern = SOURCE_LANG_CHUNK_PATTERNS.get(source_language)
    if chunk_pattern is None:
        return []
    matches = []

    for match in chunk_pattern.finditer(text):
        start = max(0, match.start() - context_chars)
        end = min(len(text), match.end() + context_chars)
        snippet = text[start:end]
        matches.append({
            'text': match.group(),
            'context': snippet,
            'position': match.start()
        })

    return matches


def fix_partial_translations(content, model_spec, source_language='zh'):
    """
    Find each source-language fragment in content, ask the model to translate only those
    fragments in context, then splice the translations back in place.
    Returns the repaired content string.
    """
    chunk_pattern = SOURCE_LANG_CHUNK_PATTERNS.get(source_language)
    if chunk_pattern is None:
        print(f"  Repair not supported for source language '{source_language}', skipping.")
        return content

    matches = list(chunk_pattern.finditer(content))

    if not matches:
        return content

    lang_name = LANG_NAMES.get(source_language, source_language)
    print(f"\n  Repairing {len(matches)} {lang_name} fragment(s)...")

    context_chars = 120
    fragments = []
    for m in matches:
        start = max(0, m.start() - context_chars)
        end = min(len(content), m.end() + context_chars)
        fragments.append({
            "source_text": m.group(),
            "context": content[start:end]
        })

    system_prompt = (
        "You are a translation repair assistant. "
        f"You will receive a JSON array of objects. Each object has a 'source_text' field "
        f"containing an untranslated {lang_name} fragment, and a 'context' field showing the "
        "surrounding English text where the fragment appears. "
        "Return a JSON array of strings — one English translation per input object, "
        "in the same order. Translate only the 'source_text' fragment, fitting naturally into "
        "the surrounding English context. "
        "The output array MUST have exactly the same number of elements as the input. "
        "Return no explanation or markdown."
    )
    user_prompt = json.dumps(fragments, ensure_ascii=False, indent=2)

    from providers import create_provider
    from config import TranslationConfig
    config = TranslationConfig()

    if model_spec is None:
        model_spec = config.translation_model

    provider_name, model_name = config.parse_model_spec(model_spec)
    provider = create_provider(provider_name)

    print(f"  Using model: {model_name}")

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
        raw_lines = raw.split("\n")
        raw = "\n".join(raw_lines[1:-1]) if len(raw_lines) > 2 else raw
        if raw.startswith("json"):
            raw = raw[4:].strip()

    if not raw:
        raise ValueError("Model returned an empty response")

    try:
        translations = json.loads(raw)
    except json.JSONDecodeError:
        # Model may have returned a bare word/phrase instead of a JSON array
        if len(matches) == 1:
            translations = [raw]
        else:
            raise ValueError(f"Model returned invalid JSON.\nRaw response: {raw!r}")

    if not isinstance(translations, list) or len(translations) != len(matches):
        raise ValueError(
            f"Expected {len(matches)} translations, "
            f"got {len(translations) if isinstance(translations, list) else type(translations)}"
        )

    # Replace fragments in reverse order so earlier positions stay valid
    result = content
    for m, translation in zip(reversed(matches), reversed(translations)):
        result = result[:m.start()] + str(translation) + result[m.end():]

    return result


def _get_book_source_language(cursor, book_id):
    """Look up a book's source_language from the database."""
    cursor.execute("SELECT source_language FROM books WHERE id = ?", (book_id,))
    row = cursor.fetchone()
    if row and row[0]:
        return row[0]
    return DEFAULT_LANG


def check_translations(db_path, book_id=None, fix=False, model_spec=None):
    """Check all translated chapters for remaining source-language characters"""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Build query
    if book_id:
        query = """
            SELECT c.id, c.book_id, c.chapter_number, c.title,
                   c.translated_content, b.title as book_title
            FROM chapters c
            JOIN books b ON c.book_id = b.id
            WHERE c.book_id = ?
            ORDER BY c.chapter_number
        """
        cursor.execute(query, (book_id,))
    else:
        query = """
            SELECT c.id, c.book_id, c.chapter_number, c.title,
                   c.translated_content, b.title as book_title
            FROM chapters c
            JOIN books b ON c.book_id = b.id
            ORDER BY c.book_id, c.chapter_number
        """
        cursor.execute(query)

    chapters = cursor.fetchall()

    if not chapters:
        print("No chapters found.")
        conn.close()
        return

    # Cache book source languages
    book_langs = {}

    issues_found = 0
    total_chapters = len(chapters)

    print(f"Checking {total_chapters} chapter(s) for untranslated source-language characters...\n")
    print("=" * 80)

    for chapter_id, chap_book_id, chapter_num, title, content, book_title in chapters:
        # Determine source language for this book
        if chap_book_id not in book_langs:
            book_langs[chap_book_id] = _get_book_source_language(cursor, chap_book_id)
        source_lang = book_langs[chap_book_id]

        if not contains_source_chars(content, source_lang):
            continue

        issues_found += 1
        lang_name = LANG_NAMES.get(source_lang, source_lang)
        snippets = find_source_snippets(content, source_lang)

        print(f"\n\U0001f4da Book: {book_title} (source: {lang_name})")
        print(f"\U0001f4d6 Chapter {chapter_num}: {title}")
        print(f"   Chapter ID: {chapter_id}")
        print(f"   Found {len(snippets)} instance(s) of {lang_name} characters:")
        print("-" * 80)

        for i, snippet in enumerate(snippets, 1):
            print(f"\n   [{i}] Source text: {snippet['text']}")
            print(f"       Position: {snippet['position']}")
            print(f"       Context: ...{snippet['context']}...")

        if fix:
            try:
                fixed_content = fix_partial_translations(content, model_spec, source_lang)

                cursor.execute(
                    "UPDATE chapters SET translated_content = ? WHERE id = ?",
                    (fixed_content, chapter_id)
                )
                conn.commit()
                print(f"\n  \u2705 Chapter {chapter_num} repaired and saved.")
            except Exception as e:
                print(f"\n  \u26a0\ufe0f  Could not repair chapter {chapter_num}: {e}")

        print("-" * 80)

    print("\n" + "=" * 80)
    print(f"\n\u2705 Summary:")
    print(f"   Total chapters checked: {total_chapters}")
    print(f"   Chapters with untranslated text: {issues_found}")
    print(f"   Clean chapters: {total_chapters - issues_found}")

    conn.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Check translated chapters for remaining source-language characters."
    )
    parser.add_argument(
        "db_path", nargs="?", default="database.db",
        help="Path to the SQLite database file (default: database.db)"
    )
    parser.add_argument(
        "book_id", nargs="?", type=int, default=None,
        help="Optional book ID to filter chapters"
    )
    parser.add_argument(
        "--fix", action="store_true",
        help="Send lines with source-language characters to the model for repair and update the database"
    )
    parser.add_argument(
        "--model", default=None,
        help="Model spec to use for repairs, e.g. 'claude:claude-3-5-sonnet-20241022' "
             "(defaults to TRANSLATION_MODEL env var)"
    )
    args = parser.parse_args()

    print(f"Using database: {args.db_path}")
    if args.book_id:
        print(f"Filtering by book_id: {args.book_id}")
    if args.fix:
        print(f"Fix mode enabled (model: {args.model or 'from TRANSLATION_MODEL env var'})")
    print()

    try:
        check_translations(args.db_path, args.book_id, fix=args.fix, model_spec=args.model)
    except FileNotFoundError:
        print(f"Error: Database file '{args.db_path}' not found.")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
