from typing import Dict, List, Optional, Any, Union, Tuple
import json
import sqlite3
import math
import os
import time
import re
import uuid
from database import DEFAULT_CATEGORIES
from modules import apply_system_prompt, apply_source_module
from providers.base import (
    OverloadedError,
    looks_overloaded,
    SessionLimitError,
    looks_session_limited,
    parse_session_reset_seconds,
)


class TranslationCancelled(Exception):
    """Raised when the user cancels an in-flight translation.

    Distinct from connection/parse errors so the per-chunk retry loops do NOT
    swallow it and "transparently retry" — a cancel must propagate straight out
    of translate_chapter rather than being treated as a transient failure.
    """
    pass


class TranslationEngine:
    """Core class for handling text translation logic"""

    def __init__(self, config: 'TranslationConfig', logger: 'Logger', entity_manager: 'DatabaseManager'):
        self.config = config
        self.logger = logger
        self.entity_manager = entity_manager

    @staticmethod
    def _check_cancel(should_cancel):
        """Raise TranslationCancelled if the caller-supplied predicate fires.

        `should_cancel` is an optional zero-arg callable (None disables it).
        Called at every cooperative cancellation point in translate_chapter."""
        if should_cancel is not None:
            try:
                cancelled = should_cancel()
            except Exception:
                cancelled = False
            if cancelled:
                raise TranslationCancelled()

    def _interruptible_sleep(self, seconds, should_cancel=None):
        """Sleep up to `seconds`, but wake early (raising TranslationCancelled)
        if a cancel is requested. Polls once per second so a long overload /
        session-limit wait doesn't keep a cancelled job parked for minutes."""
        if should_cancel is None:
            time.sleep(seconds)
            return
        remaining = max(0, int(seconds))
        for _ in range(remaining):
            self._check_cancel(should_cancel)
            time.sleep(1)
        # Sleep any sub-second remainder.
        frac = seconds - int(seconds)
        if frac > 0:
            time.sleep(frac)
        self._check_cancel(should_cancel)

    def _overload_retry_wait_seconds(self) -> int:
        """Seconds to wait before retrying after a 529 "Overloaded" response.

        Configurable via the OVERLOAD_RETRY_WAIT_SECONDS setting/env var
        (default 300). settings_store mirrors the setting into os.environ.
        """
        raw = os.getenv("OVERLOAD_RETRY_WAIT_SECONDS", "300")
        try:
            return max(1, int(raw))
        except (TypeError, ValueError):
            return 300

    def _sleep_for_overload(self, reason, progress_callback=None, chunk_index=None, should_cancel=None):
        """Wait the configured interval after a 529, then return so the caller
        can retry. Overload waits are intentionally not bounded by the normal
        per-chunk retry budget — the service is saturated, not broken."""
        wait = self._overload_retry_wait_seconds()
        where = f" on chunk {chunk_index}" if chunk_index else ""
        print(f"\n⏳ API overloaded (529){where}. Waiting {wait}s before retrying...")
        self.logger.warning(
            f"API overloaded (529){where}: {str(reason)[:200]}. Waiting {wait}s before retry."
        )
        if progress_callback:
            try:
                progress_callback({
                    "phase": "overloaded",
                    "wait_seconds": wait,
                    "chunk": chunk_index,
                })
            except Exception:
                pass
        self._interruptible_sleep(wait, should_cancel)

    def _sleep_for_session_limit(self, reason, progress_callback=None, chunk_index=None, should_cancel=None):
        """Pause the queue until just past the Claude Code session-limit reset
        time named in `reason`, then return so the caller can retry the chunk.

        Like the overload wait, this is intentionally not bounded by the
        per-chunk retry budget — the session is throttled, not broken, and the
        chapter resumes from the same chunk once usage resets. If the reset
        time can't be parsed we fall back to the overload retry interval and
        loop again, re-reading the (still-current) limit notice next time."""
        wait = parse_session_reset_seconds(str(reason))
        if wait is None:
            wait = self._overload_retry_wait_seconds()
            detail = "reset time unparseable, using overload interval"
        else:
            detail = "until 1 min past reset"
        where = f" on chunk {chunk_index}" if chunk_index else ""
        mins = max(1, round(wait / 60))
        print(
            f"\n⏸️  Claude Code session limit hit{where}. "
            f"Pausing ~{mins} min ({detail}) before resuming..."
        )
        self.logger.warning(
            f"Session limit hit{where}: {str(reason)[:200]}. "
            f"Pausing {wait}s before retry."
        )
        if progress_callback:
            try:
                progress_callback({
                    "phase": "session_limit",
                    "wait_seconds": wait,
                    "resume_at": time.time() + wait,
                    "chunk": chunk_index,
                    "reset_text": str(reason)[:200],
                })
            except Exception:
                pass
        self._interruptible_sleep(wait, should_cancel)

    def _chat_completion_overload_aware(self, provider, progress_callback=None, should_cancel=None, **kwargs):
        """Non-streaming provider.chat_completion that transparently waits and
        retries on a 529 "Overloaded" (whether raised as OverloadedError or
        returned as plain-text content). Genuine errors propagate to the
        caller's normal retry handling. Returns the provider response dict."""
        while True:
            self._check_cancel(should_cancel)
            try:
                response = provider.chat_completion(**kwargs)
            except TranslationCancelled:
                raise
            except SessionLimitError as e:
                self._sleep_for_session_limit(e.reset_text, progress_callback, should_cancel=should_cancel)
                continue
            except OverloadedError as e:
                self._sleep_for_overload(e, progress_callback, should_cancel=should_cancel)
                continue
            except Exception as e:
                if looks_session_limited(str(e)):
                    self._sleep_for_session_limit(e, progress_callback, should_cancel=should_cancel)
                    continue
                if looks_overloaded(str(e), strict=False):
                    self._sleep_for_overload(e, progress_callback, should_cancel=should_cancel)
                    continue
                raise
            try:
                content = provider.get_response_content(response)
            except Exception:
                content = ""
            if looks_session_limited(content):
                self._sleep_for_session_limit(content, progress_callback, should_cancel=should_cancel)
                continue
            if looks_overloaded(content):
                self._sleep_for_overload("529 Overloaded", progress_callback, should_cancel=should_cancel)
                continue
            return response
    
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
    
    def _parse_template_from_prompt(self, prompt_text):
        """
        Extract and parse the JSON response template from between the ++++ markers
        in a system prompt.

        Returns:
            dict or None: The parsed template JSON, or None if not found/invalid
        """
        pattern = re.compile(
            r'\+\+\+\+ Response Template Example\n(.*?)\+\+\+\+ Response Template End',
            re.DOTALL,
        )
        match = pattern.search(prompt_text)
        if not match:
            return None
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError as e:
            self.logger.warning(f"Failed to parse response template JSON from prompt: {e}")
            return None

    def _build_response_template(self, categories, entities, chapter_number=3, base_template=None, source_language='zh', mode='full', gendered_categories=None):
        """
        Build the response template JSON dynamically from the book's active
        categories, using real entities as examples where available.

        If base_template is provided (parsed from the system prompt), its non-entity
        fields (title, content, summary) are preserved so genre-specific prompts
        keep their flavour.

        Args:
            categories: list of category names for this book
            entities: dict {category: {chinese_key: {translation, ...}, ...}}
            chapter_number: chapter number to use in the example (default 3)
            base_template: dict parsed from the prompt's response template (optional)
            source_language: source language code for placeholder entity keys
            mode: 'full' (default — title/content/entities), 'entity_only' (entities only),
                  'translate_only' (title/content but no entities)
        Returns:
            str: A pretty-printed JSON string suitable for the response template
        """
        ch = chapter_number if isinstance(chapter_number, int) and chapter_number > 0 else 3
        gendered = set(gendered_categories) if gendered_categories else set()

        # Determine which fields the base template's entity entries carry (e.g. gender on characters)
        base_entity_fields = {}
        if base_template and "entities" in base_template:
            for cat, cat_dict in base_template["entities"].items():
                if cat_dict:
                    first_entry = next(iter(cat_dict.values()))
                    base_entity_fields[cat] = set(first_entry.keys()) - {"translation", "last_chapter"}

        # Build the entities example section
        entities_example = {}
        for cat in categories:
            cat_entities = entities.get(cat, {})
            # Pick up to 2 real entities as examples
            sample_keys = list(cat_entities.keys())[:2]
            cat_example = {}
            for key in sample_keys:
                entry = cat_entities[key]
                example_entry = {
                    "translation": entry.get("translation", "Example Translation"),
                    "last_chapter": ch,
                }
                # Carry over extra fields from the base template (e.g. gender for characters).
                # A category is gender-tracked if the book says so (gendered set), if the
                # base template already carries gender, or — when no book context was passed —
                # the legacy "characters" default.
                extra_fields = base_entity_fields.get(cat, set())
                if "gender" in extra_fields or cat in gendered or (gendered_categories is None and cat == "characters"):
                    example_entry["gender"] = entry.get("gender", "male")
                for field in extra_fields - {"gender"}:
                    if field in entry:
                        example_entry[field] = entry[field]
                cat_example[key] = example_entry

            # If no real entities, fall back to the base template's examples or a placeholder
            if not cat_example:
                if base_template and "entities" in base_template and cat in base_template["entities"]:
                    # Re-use the original prompt's example entities for this category
                    for orig_key, orig_val in base_template["entities"][cat].items():
                        patched = dict(orig_val)
                        patched["last_chapter"] = ch
                        cat_example[orig_key] = patched
                else:
                    # Generate a placeholder keyed in the source language
                    placeholder_key = self._placeholder_entity_key(cat, source_language)
                    singular = cat[:-1] if cat.endswith('s') and not cat.endswith('ss') else cat
                    if singular.endswith('ie'):
                        singular = singular[:-2] + 'y'
                    placeholder = {"translation": f"Example {singular.title()}", "last_chapter": ch}
                    if "gender" in base_entity_fields.get(cat, set()) or cat in gendered or (gendered_categories is None and cat == "characters"):
                        placeholder["gender"] = "male"
                    cat_example[placeholder_key] = placeholder

            entities_example[cat] = cat_example

        # Build the final template, preserving non-entity fields from the base if available
        if mode == 'entity_only':
            template = {"entities": entities_example}
        elif base_template:
            template = {
                "title": base_template.get("title", f"Chapter {ch} - The Great Apocalyptic Battle"),
                "chapter": ch,
                "summary": base_template.get("summary", "A concise summary of no more than 75 words."),
                "content": base_template.get("content", []),
            }
            if mode != 'translate_only':
                template["entities"] = entities_example
        else:
            template = {
                "title": f"Chapter {ch} - The Great Apocalyptic Battle",
                "chapter": ch,
                "summary": "A concise summary of no more than 75 words.",
                "content": [
                    "The warriors gathered at the base of the mountain, their weapons gleaming under the pale moonlight.",
                    "",
                    "\"We have no choice,\" Lin Feng said, gripping the hilt of his sword. \"If we don't act now, the Scarlet Flame Sect will destroy everything.\"",
                    "",
                    "A cold wind swept across the battlefield as the first clash of steel echoed through the valley."
                ],
            }
            if mode != 'translate_only':
                template["entities"] = entities_example

        return json.dumps(template, ensure_ascii=False, indent=4)

    @staticmethod
    def _entity_response_format(mode=None, categories=None, gendered_categories=None):
        """Build the OpenAI-style response_format dict, carrying the book's entity
        categories and which of them are gender-tracked so structured-output
        providers (Gemini) can build a matching schema. Non-Gemini providers
        ignore the extra keys."""
        rf = {"type": "json_object"}
        if mode:
            rf["mode"] = mode
        if categories is not None:
            rf["categories"] = categories
        if gendered_categories is not None:
            rf["gendered_categories"] = gendered_categories
        return rf

    @staticmethod
    def _placeholder_entity_key(category, source_language='zh'):
        """Return a placeholder entity key in the appropriate source language."""
        placeholders = {
            'zh': f"示例{category}",
            'ja': f"例{category}",
            'ko': f"예시{category}",
        }
        return placeholders.get(source_language, f"示例{category}")

    def generate_system_prompt(self, pretext, entities, do_count=True, book_prompt_template=None, provider=None, chapter_number=None, source_language='zh', retranslation_reason=None, mode='full', chapter_title=None, gendered_categories=None, book=None, chunk_index=None, total_chunks=None, previous_summary=None):
        """
        Generate the system (instruction) prompt for translation, incorporating any discovered entities.

        Args:
            provider: The model provider instance (used to detect Gemini and remove schema)
            chapter_number: Known chapter number to inject into the prompt template
            source_language: Source language code for the book (default: zh)
            retranslation_reason: Optional free-text reason for retranslating this chapter,
                appended as an extra section at the end of the prompt.
            mode: 'full' (default — translate + extract entities),
                  'entity_only' (pass-1 of two-pass — identify entities, no prose),
                  'translate_only' (pass-2 of two-pass — translate prose, no entity output).
            chunk_index: 1-based index of the chunk currently being translated (when the
                chapter was split across multiple chunks). None/1 with total_chunks<=1 means
                the chapter fits in a single pass and no chunk framing is added.
            total_chunks: Total number of chunks the chapter was split into.
            previous_summary: Running summary of all previously-translated chunks of this
                same chapter, injected for continuity when chunk_index > 1.
        """
        # Debug info
        self.logger.debug(f"generate_system_prompt: type of pretext = {type(pretext)}")
        if isinstance(pretext, list) and len(pretext) > 0:
            self.logger.debug(f"First line: {pretext[0][:50]}")

        # Ensure all entity categories exist (entities dict already has the right keys)
        end_entities = {}
        for category in entities:
            chapter_label = str(chapter_number) if chapter_number and isinstance(chapter_number, int) and chapter_number > 0 else "THIS CHAPTER"
            end_entities[category] = self.entity_manager.entities_inside_text(pretext, entities[category], chapter_label, do_count)

        entities_json = json.dumps(end_entities, ensure_ascii=False, indent=4)

        # Load the appropriate template
        if book_prompt_template:
            # Use the custom template for this book
            prompt = book_prompt_template
        else:
            # Try to load prompt from file (check prompts/ directory first, then legacy location)
            prompt_file_path = os.path.join(self.config.script_dir, "prompts", "chinese_xianxia.txt")
            if not os.path.exists(prompt_file_path):
                # Legacy fallback
                prompt_file_path = os.path.join(self.config.script_dir, "system_prompt.txt")

            try:
                if os.path.exists(prompt_file_path):
                    with open(prompt_file_path, 'r', encoding='utf-8') as file:
                        prompt = file.read()
                    self.logger.info(f"Loaded system prompt from {prompt_file_path}")
                else:
                    self.logger.error(f"No system prompt found at {prompt_file_path}. Place a prompt file in prompts/ or create a book with a genre preset.")
                    raise FileNotFoundError(f"System prompt not found: {prompt_file_path}")
            except FileNotFoundError:
                raise
            except Exception as e:
                self.logger.error(f"Error loading system prompt from file: {e}")
                raise

        # Strip out comment lines (lines whose first non-whitespace char is #).
        # Applied uniformly so book-stored templates (saved raw from genre prompt
        # files) are cleaned at translation time without a DB migration.
        prompt = ''.join(
            line for line in prompt.splitlines(keepends=True)
            if not line.lstrip().startswith('#')
        )

        # Insert the entity categories list into the template
        categories_str = ", ".join(entities.keys())
        self.logger.debug(f"ENTITY_CATEGORIES replacement: keys={list(entities.keys())}, placeholder_present={'{{ENTITY_CATEGORIES}}' in prompt}")
        if "{{ENTITY_CATEGORIES}}" in prompt:
            prompt = prompt.replace("{{ENTITY_CATEGORIES}}", categories_str)
        else:
            # Fallback: template may already have literal default categories baked in
            default_categories_str = ", ".join(DEFAULT_CATEGORIES)
            prompt = prompt.replace(
                f"Entity categories: {default_categories_str}.",
                f"Entity categories: {categories_str}.",
            )

        # Insert the entities JSON into the template (both default and custom)
        prompt = prompt.replace("{{ENTITIES_JSON}}", entities_json)

        # Insert the chapter number if known, otherwise remove the placeholder line
        if chapter_number and isinstance(chapter_number, int) and chapter_number > 0:
            prompt = prompt.replace("{{CHAPTER_NUMBER}}", str(chapter_number))
        else:
            prompt = prompt.replace("\nYou are translating chapter {{CHAPTER_NUMBER}}.\n", "\n")

        # Insert the source-language chapter title if known, otherwise remove the
        # whole line carrying the placeholder. This lets the model translate the
        # chapter title into the "title" field even when the importer stripped the
        # heading out of the chapter content (it lives in queue.title instead).
        if chapter_title and str(chapter_title).strip():
            prompt = prompt.replace("{{CHAPTER_TITLE}}", str(chapter_title).strip())
        else:
            prompt = re.sub(r'[^\n]*\{\{CHAPTER_TITLE\}\}[^\n]*\n?', '', prompt)

        # Parse the base template from the prompt before rebuilding it
        base_template = self._parse_template_from_prompt(prompt)

        # Rebuild the response template with the book's actual categories and real entity examples
        template_pattern = re.compile(
            r'(\+\+\+\+ Response Template Example\n).*?(\+\+\+\+ Response Template End)',
            re.DOTALL,
        )
        match = template_pattern.search(prompt)
        if match:
            dynamic_template = self._build_response_template(
                list(entities.keys()), entities, chapter_number or 3,
                base_template=base_template, source_language=source_language,
                mode=mode, gendered_categories=gendered_categories,
            )
            prompt = prompt[:match.start()] + match.group(1) + "\n" + dynamic_template + "\n" + match.group(2) + prompt[match.end():]
            self.logger.debug(f"Rebuilt response template with categories: {list(entities.keys())} (mode={mode})")

        # For Gemini providers, remove the JSON schema example to avoid conflicts with responseSchema
        if provider and hasattr(provider, 'provider_name') and 'Gemini' in provider.provider_name:
            prompt = template_pattern.sub('', prompt)
            self.logger.debug("Removed JSON schema template for Gemini provider")

        # Mode-specific overrides appended at the end so they override anything earlier in the prompt
        if mode == 'entity_only':
            prompt = prompt.rstrip() + (
                "\n\n---\n\n"
                "ENTITY-EXTRACTION MODE (OVERRIDES ALL OTHER INSTRUCTIONS):\n"
                "Your sole task is to identify proper nouns and consistency-required terms in the chapter "
                "below and return ONLY their translations. Do NOT translate the chapter prose. "
                "Your JSON response must contain ONLY the 'entities' field (no 'title', 'chapter', "
                "'summary', or 'content' fields). Use the existing entity rules above for what counts as "
                "an entity and what does not.\n"
            )
        elif mode == 'translate_only':
            prompt = prompt.rstrip() + (
                "\n\n---\n\n"
                "TRANSLATION-ONLY MODE (OVERRIDES ALL OTHER INSTRUCTIONS):\n"
                "All entities have already been identified and pre-translated in the PRE-TRANSLATED "
                "ENTITIES block above. Use those translations exactly when the entity appears in the "
                "source. You must NOT emit an 'entities' field in your response — return only 'title', "
                "'chapter', 'summary', and 'content'.\n"
            )

        # If this chapter carries illustration sentinels, instruct the model to
        # preserve them verbatim. Injected dynamically (only when a marker is
        # actually present) so image-free chapters pay no token/noise cost.
        try:
            from illustrations import markers_in
            if markers_in(pretext):
                prompt = prompt.rstrip() + (
                    "\n\n---\n\n"
                    "IMAGE PLACEHOLDERS:\n"
                    "Some content lines are opaque image placeholders of the exact form "
                    "⟦IMG:xxxxxx⟧ (where xxxxxx is a short code). Copy every such line into "
                    "your output 'content' array verbatim and unchanged, on its own line, in "
                    "the same relative position. Never translate, transliterate, reword, "
                    "merge, reorder, duplicate, or omit them, and do not alter the ⟦ ⟧ "
                    "brackets or the code inside.\n"
                )
        except Exception:
            pass

        # If this chapter was split into multiple chunks (output-token limits),
        # tell the model which chunk it is translating and feed it the running
        # summary of everything translated so far. This keeps tone, pronouns,
        # ongoing events, and entity rendering consistent across chunk boundaries.
        if total_chunks and isinstance(total_chunks, int) and total_chunks > 1 and chunk_index:
            prompt = prompt.rstrip() + (
                "\n\n---\n\n"
                "CHUNKED CHAPTER:\n"
                "This chapter was too long to translate in one pass, so it has been split "
                f"into {total_chunks} sequential chunks. You are now translating chunk "
                f"{chunk_index} of {total_chunks}. The user message contains ONLY this "
                "chunk's source text — translate exactly what is given, do not summarize, "
                "skip ahead, or re-translate earlier chunks. Your 'summary' field should "
                "describe the events of THIS chunk only.\n"
            )
            prior = (previous_summary or "").strip()
            if chunk_index > 1 and prior:
                prompt = prompt.rstrip() + (
                    "\n\nSUMMARY OF PREVIOUS CHUNKS (context for continuity only — do not "
                    "re-translate this text or include it in your output):\n"
                    f"{prior}\n"
                )

        # Append retranslation reason (if any) as a final, high-priority section
        reason = (retranslation_reason or "").strip()
        if reason:
            prompt = prompt.rstrip() + (
                "\n\n---\n\n"
                "RETRANSLATION NOTE:\n"
                "This chapter is being retranslated because a previous attempt was unsatisfactory. "
                "The user's reason for retranslation is below. Pay particular attention to it and "
                "avoid repeating the same mistake.\n\n"
                f"{reason}\n"
            )
            self.logger.info(f"Appended retranslation reason to system prompt ({len(reason)} chars)")

        # Per-book module transforms of the assembled system prompt.
        prompt = apply_system_prompt(book, prompt, self.config, self.logger,
                                     chapter_number=chapter_number, mode=mode)

        return prompt
    
    def _detect_repetition(self, text: str) -> bool:
        """Detect pathological token repetition loops in streamed output."""
        tail = text[-200:]
        # Non-whitespace single character repeated 10+ times: 框框框框框框框框框框
        if re.search(r'([^\s])\1{9,}', tail):
            return True
        # CJK phrase of 2-10 chars repeated 4+ times: 改革开放改革开放改革开放改革开放
        if re.search(r'([\u4e00-\u9fff\u3400-\u4dbf]{2,10})\1{3,}', tail):
            return True
        return False

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
            conn = self.entity_manager.get_connection()
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
            parsed_response = advice_provider.validate_json_response(response_content)
            
            # If we found duplicates earlier, append a warning to the message
            if existing_duplicates:
                duplicate_warning = "\n\nWARNING: The current translation conflicts with existing entities:"
                for dup in existing_duplicates:
                    duplicate_warning += f"\n- '{dup['untranslated']}' in '{dup['category']}' (also translated as '{dup['translation']}')"
                duplicate_warning += "\nConsider choosing a more distinctive translation to avoid confusion."
                
                parsed_response['message'] = parsed_response['message'] + duplicate_warning
        except json.JSONDecodeError as e:
            print("Failed to parse JSON. Writing response to json_fail_debug.txt")
            with open('json_fail_debug.txt', 'w', encoding='utf-8') as f:
                f.write(str(response_content))
            print(f"Error: {e}")
            return {'message': f'The translation failed: {e}', 'options': []}
        
        return parsed_response
    
    def extract_entities(self, chapter_text, book_id=None, chapter_number=None,
                         progress_callback=None, retranslation_reason=None, should_cancel=None):
        """
        Pass-1 of two-pass mode: identify new entities in the chapter without
        translating any prose. Makes a single non-streaming API call over the
        full chapter (no chunking — output is small enough that input limits,
        not output limits, drive sizing). Returns the same shape as the
        per-chunk `find_new_entities` output: { category: { untranslated: {...} } }.

        Args:
            chapter_text (list[str]): Chapter source lines.
            book_id (int|None): Book ID for prompt template + entity scope.
            chapter_number (int|None): Known chapter number (injected into the prompt).
            progress_callback (callable|None): Receives {"phase": "entity_extract", ...} updates.
            retranslation_reason (str|None): Forwarded to generate_system_prompt.

        Returns:
            dict: {category: {untranslated: entity_data}} containing only NEW entities
                  (filtered against the existing DB).
        """
        session_id = str(uuid.uuid4())

        if not chapter_text:
            self.logger.warning("extract_entities: empty chapter_text")
            return {}

        # Strip common scraping artifacts (matches translate_chapter's behavior)
        if chapter_text[-1].strip() == '(本章完)':
            chapter_text = chapter_text[:-1]

        book_prompt_template = None
        source_language = 'zh'
        book_info = None
        if book_id:
            book_prompt_template = self.entity_manager.get_book_prompt_template(book_id)
            book_info = self.entity_manager.get_book(book_id)
            if book_info:
                source_language = book_info.get('source_language', 'zh') or 'zh'

        # Optional trad→simp preprocessing (mirrors translate_chapter). Applies the
        # trad_to_simp module only, so the AI sees canonical simplified text even on
        # direct (non-ingest) paths. Idempotent if it already ran at ingest.
        chapter_text = apply_source_module(book_info, chapter_text, "trad_to_simp",
                                           self.config, self.logger)

        provider, model_name = self.config.get_client(self.config.translation_model)
        self.logger.debug(f"extract_entities: using {provider.provider_name}/{model_name}")

        # Load entities scoped to this book so categories from other books
        # don't leak into the system prompt's {{ENTITY_CATEGORIES}}.
        if book_id:
            self.entity_manager._load_entities(book_id=book_id)
        old_entities = self.entity_manager.entities.copy()
        if book_id:
            for cat in self.entity_manager.get_book_categories(book_id):
                old_entities.setdefault(cat, {})
        else:
            for cat in DEFAULT_CATEGORIES:
                old_entities.setdefault(cat, {})

        book_categories = self.entity_manager.get_book_categories(book_id) if book_id else None
        gendered_categories = self.entity_manager.get_book_gendered_categories(book_id) if book_id else None
        system_prompt = self.generate_system_prompt(
            chapter_text, old_entities,
            book_prompt_template=book_prompt_template, provider=provider,
            chapter_number=chapter_number, source_language=source_language,
            retranslation_reason=retranslation_reason, mode='entity_only',
            gendered_categories=gendered_categories, book=book_info,
        )

        # Save the pass-1 prompt for debugging (mirrors translate_chapter's behavior)
        self.entity_manager.save_json_file(f"{self.config.script_dir}/prompt.tmp", system_prompt)

        chunk_str = "\n".join(chapter_text)
        user_text = "Identify the entities in the following text. Do NOT translate the prose.\n" + chunk_str

        if progress_callback:
            progress_callback({"phase": "entity_extract", "chunk": 1, "total": 1})

        # Pass mode hint to providers (Gemini uses it to pick the right schema)
        response_format = self._entity_response_format("entity_only", book_categories, gendered_categories)

        MAX_RETRIES = 2
        parsed = None
        for attempt in range(MAX_RETRIES + 1):
            self._check_cancel(should_cancel)
            if attempt > 0:
                self.logger.info(f"extract_entities: retrying pass-1 (attempt {attempt + 1}/{MAX_RETRIES + 1})")
            call_start_time = time.time()
            response_content = ""
            try:
                response = self._chat_completion_overload_aware(
                    provider,
                    progress_callback=progress_callback,
                    should_cancel=should_cancel,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_text},
                    ],
                    model=model_name,
                    temperature=1,
                    top_p=1,
                    response_format=response_format,
                )
                response_content = provider.get_response_content(response)
                usage = response.get("usage", {}) if isinstance(response, dict) else {}
                self.entity_manager.log_api_call(
                    session_id=session_id, book_id=book_id, chapter_number=chapter_number,
                    chunk_index=0, total_chunks=1,
                    system_prompt=system_prompt, user_prompt=user_text,
                    response_text=response_content,
                    model_name=model_name, provider=provider.provider_name,
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    completion_tokens=usage.get("completion_tokens", 0),
                    total_tokens=usage.get("total_tokens", 0),
                    duration_ms=int((time.time() - call_start_time) * 1000),
                    success=1, attempt=attempt,
                )
                parsed = provider.validate_json_response(response_content)
                break
            except json.JSONDecodeError as e:
                self.logger.warning(f"extract_entities: JSON parse failed (attempt {attempt + 1}): {e}")
                if attempt < MAX_RETRIES:
                    continue
                self.logger.error("extract_entities: giving up after JSON parse failures")
                with open('json_fail_debug.txt', 'w', encoding='utf-8') as f:
                    f.write(str(response_content))
                raise
            except TranslationCancelled:
                raise
            except Exception as e:
                self.logger.error(f"extract_entities: provider error (attempt {attempt + 1}): {e}")
                self.entity_manager.log_api_call(
                    session_id=session_id, book_id=book_id, chapter_number=chapter_number,
                    chunk_index=0, total_chunks=1,
                    system_prompt=system_prompt, user_prompt=user_text, response_text="",
                    model_name=model_name, provider=provider.provider_name,
                    duration_ms=int((time.time() - call_start_time) * 1000),
                    success=0, attempt=attempt,
                )
                if attempt < MAX_RETRIES:
                    continue
                raise

        raw_entities = (parsed or {}).get("entities", {}) or {}
        # Filter to only newly-seen entities (same logic translate_chapter uses on each chunk)
        new_entities = self.entity_manager.find_new_entities(old_entities, raw_entities)

        # Apply the chapter_number to last_chapter so downstream code can use it directly
        ch = chapter_number if isinstance(chapter_number, int) and chapter_number > 0 else 0
        for cat in new_entities:
            for key, val in new_entities[cat].items():
                if isinstance(val, dict):
                    val.setdefault("last_chapter", ch)

        return new_entities

    def translate_chapter(self, chapter_text, book_id=None, stream=True, progress_callback=None, chapter_number=None, json_fix_callback=None, retranslation_reason=None, pass2_only=False, chapter_title=None, should_cancel=None):
        """
        Translate a chapter of text using the configured LLM.

        Args:
            chapter_text (list of str): The chapter's text content split into lines.
            book_id (int, optional): Book ID for loading book-specific prompt templates.
            stream (bool): Whether to use streaming output.
            progress_callback (callable, optional): Callback for chunk progress updates.
            chapter_number (int, optional): Known chapter number, injected into the system prompt.
            retranslation_reason (str, optional): Free-text reason for retranslating an
                existing chapter. Appended to the system prompt so the model knows what
                the previous attempt got wrong.

        Returns:
            dict: A dictionary containing the translated chapter data.
        """
        # Unique session ID for this translation run (groups all chunks together)
        session_id = str(uuid.uuid4())

        # Strip common scraping artifacts from the last line
        if chapter_text and chapter_text[-1].strip() == '(本章完)':
            chapter_text = chapter_text[:-1]

        # Initialize current_chapter to a default value
        current_chapter = 0
        total_input_chars = 0
        total_output_tokens = 0
        average_ratio = 1.0
        book_prompt_template = None
        source_language = 'zh'
        book_info = None
        if book_id:
            book_prompt_template = self.entity_manager.get_book_prompt_template(book_id)
            book_info = self.entity_manager.get_book(book_id)
            if book_info:
                source_language = book_info.get('source_language', 'zh') or 'zh'

        # Optional trad→simp preprocessing — runs before the AI sees the source so
        # entity matching and prompt generation work against canonical (simplified) text.
        # Applies the trad_to_simp module only. Idempotent: if the source was already
        # converted in add_to_queue, this is a no-op.
        chapter_text = apply_source_module(book_info, chapter_text, "trad_to_simp",
                                           self.config, self.logger)

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

        # Load entities scoped to this book so categories from other books
        # don't leak into the system prompt's {{ENTITY_CATEGORIES}}.
        if book_id:
            self.entity_manager._load_entities(book_id=book_id)
        old_entities = self.entity_manager.entities.copy()
        # Ensure all categories for this book exist in the dict
        if book_id:
            for cat in self.entity_manager.get_book_categories(book_id):
                old_entities.setdefault(cat, {})
        else:
            for cat in DEFAULT_CATEGORIES:
                old_entities.setdefault(cat, {})

        real_old_entities = old_entities
        self.logger.debug(f"translate_chapter: old_entities keys={list(old_entities.keys())}, book_id={book_id}")
        self.logger.debug(f"translate_chapter: entity_manager.entities keys={list(self.entity_manager.entities.keys())}")

        # Calculate chunks count, ensuring at least 1 chunk
        max_chars = self.config.get_max_chars(self.config.translation_model)
        chunks_count = max(1, math.ceil(total_char_count / max_chars))

        # Split the text into chunks for the LLM if necessary due to output token limits
        split_text = list(self.split_by_n(chapter_text, chunks_count))

        self.logger.debug(f"Text split into {len(split_text)} chunks")

        if len(split_text) == 0:
            self.logger.error("Error: Text was split into 0 chunks. This should never happen.")
            # Create a single chunk with the entire text as a fallback
            split_text = [chapter_text]
            self.logger.debug("Created fallback chunk with entire text")

        # Generate the initial system prompt (for chunk 1). total_chunks lets the
        # prompt tell the model which chunk it is translating when split > 1.
        _mode = 'translate_only' if pass2_only else 'full'
        book_categories = self.entity_manager.get_book_categories(book_id) if book_id else None
        gendered_categories = self.entity_manager.get_book_gendered_categories(book_id) if book_id else None
        system_prompt = self.generate_system_prompt(chapter_text, old_entities,
                                               book_prompt_template=book_prompt_template, provider=provider,
                                               chapter_number=chapter_number, source_language=source_language,
                                               retranslation_reason=retranslation_reason, mode=_mode,
                                               chapter_title=chapter_title, gendered_categories=gendered_categories,
                                               book=book_info, chunk_index=1, total_chunks=len(split_text))

        if len(split_text) > 1:
            self.logger.info(f"Input text is {total_char_count} characters. Splitting text into {len(split_text)} chunks.")

        # Load token ratio for progress estimation (once, before the chunk loop)
        average_ratio = self.entity_manager.get_token_ratio(book_id)

        end_object = {}

        self.logger.debug("Initializing totally_new_entities")
        totally_new_entities = {}
        self.entity_manager.save_json_file(f"{self.config.script_dir}/prompt.tmp", system_prompt)
        
        self.logger.debug(f"About to process {len(split_text)} chunks")
        for chunk_index, chunk in enumerate(split_text, 1):
            # Cooperative cancellation point — between chunks the job stops cleanly
            # (raising TranslationCancelled) rather than billing the next chunk.
            self._check_cancel(should_cancel)
            self.logger.debug(f"Processing chunk {chunk_index} of {len(split_text)}")
            if progress_callback:
                progress_callback({"chunk": chunk_index, "total": len(split_text), "phase": "start"})
            chunk_str = "\n".join(chunk)
            user_text = "Translate the following into English: \n" + chunk_str
            total_input_chars += len(chunk_str)
            self.logger.debug(f"TransEng> Stream mode is {stream}")
            if stream:
                print(f"\nTranslating chunk {chunk_index} of {len(split_text)}")

                expected_tokens = len(chunk_str) * average_ratio
                print(f"Based on {len(chunk_str)} input characters * {average_ratio:.2f} (our historic average ratio) we expect {expected_tokens:.0f} tokens.")

                # Get progress bar width based on terminal size
                terminal_width = 80
                try:
                    import shutil
                    terminal_width = shutil.get_terminal_size().columns
                except Exception:
                    pass
                progress_width = min(50, terminal_width - 30)

                MAX_STREAM_RETRIES = 2
                parsed_chunk = None

                for attempt in range(MAX_STREAM_RETRIES + 1):
                    self._check_cancel(should_cancel)
                    if attempt > 0:
                        print(f"🔄 Retrying chunk {chunk_index} (attempt {attempt + 1}/{MAX_STREAM_RETRIES + 1})...")

                    # A genuine connection error consumes a retry from the
                    # MAX_STREAM_RETRIES budget. A 529 "Overloaded" does not:
                    # the service is saturated, so the inner loop waits a
                    # configurable interval and re-streams without burning a
                    # retry, looping until the service recovers.
                    connection_failed = False
                    while True:
                        self._check_cancel(should_cancel)
                        response_text = ""
                        chunk_count = 0
                        start_time = time.time()
                        call_start_time = time.time()
                        repetition_detected = False
                        overloaded = False
                        session_limited = None

                        try:
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
                                response_format=self._entity_response_format(None, book_categories, gendered_categories),
                                stream=True
                            )

                            # Process streaming response
                            for stream_chunk in response_stream:
                                content = provider.get_streaming_content(stream_chunk)
                                if content:
                                    response_text += content
                                    chunk_count += 1

                                    # Mid-stream cancellation: abort the in-flight
                                    # stream as soon as the user cancels instead of
                                    # waiting for the whole chunk to finish.
                                    if chunk_count % 10 == 0:
                                        self._check_cancel(should_cancel)

                                    # Estimate tokens from response length (~4 chars per token for English + JSON)
                                    token_count = len(response_text) // 4

                                    # Check for repetition loop every 20 chunks
                                    if chunk_count % 20 == 0 and self._detect_repetition(response_text):
                                        print(f"\n⚠️  Repetition loop detected at ~{token_count} tokens. Aborting stream...")
                                        repetition_detected = True
                                        break

                                    # Update progress display every 10 chunks
                                    if chunk_count % 10 == 0:
                                        elapsed = time.time() - start_time
                                        tokens_per_second = token_count / elapsed if elapsed > 0 else 0
                                        completion_percentage = min(100, (token_count / expected_tokens) * 100) if expected_tokens > 0 else 0
                                        filled = int(completion_percentage / 100 * progress_width)
                                        progress_bar = "█" * filled + "░" * (progress_width - filled)
                                        print(f"\r[{progress_bar}] {token_count}/{int(expected_tokens)} tokens ({completion_percentage:.1f}%) - {elapsed:.1f}s elapsed", end="")
                                        if progress_callback:
                                            progress_callback({
                                                "chunk": chunk_index,
                                                "total": len(split_text),
                                                "phase": "translating",
                                                "token_count": token_count,
                                                "expected_tokens": int(expected_tokens),
                                                "percent": round(completion_percentage, 1),
                                                "tokens_per_second": round(tokens_per_second, 1),
                                                "elapsed": round(elapsed, 1),
                                            })

                                # Check if stream is complete
                                if provider.is_stream_complete(stream_chunk):
                                    break

                            # Some providers surface a 529 — or a session-limit
                            # notice — as plain assistant text rather than
                            # raising. Detect both before treating it as output.
                            if looks_session_limited(response_text):
                                session_limited = response_text
                            elif looks_overloaded(response_text):
                                overloaded = True

                        except TranslationCancelled:
                            raise
                        except SessionLimitError as e:
                            self._sleep_for_session_limit(e.reset_text, progress_callback, chunk_index, should_cancel=should_cancel)
                            continue
                        except OverloadedError as e:
                            self._sleep_for_overload(e, progress_callback, chunk_index, should_cancel=should_cancel)
                            continue
                        except Exception as e:
                            if looks_session_limited(str(e)):
                                self._sleep_for_session_limit(e, progress_callback, chunk_index, should_cancel=should_cancel)
                                continue
                            if looks_overloaded(str(e), strict=False):
                                self._sleep_for_overload(e, progress_callback, chunk_index, should_cancel=should_cancel)
                                continue
                            print(f"\n⚠️  Connection error on chunk {chunk_index}: {e}")
                            self.logger.error(f"Connection error during chunk {chunk_index} (attempt {attempt + 1}): {e}")
                            self.entity_manager.log_api_call(
                                session_id=session_id, book_id=book_id, chapter_number=chapter_number,
                                chunk_index=chunk_index, total_chunks=len(split_text),
                                system_prompt=system_prompt, user_prompt=user_text, response_text="",
                                model_name=model_name, provider=provider.provider_name,
                                duration_ms=int((time.time() - call_start_time) * 1000),
                                success=0, attempt=attempt,
                            )
                            if attempt < MAX_STREAM_RETRIES:
                                connection_failed = True
                                break
                            else:
                                raise

                        if session_limited:
                            self._sleep_for_session_limit(session_limited, progress_callback, chunk_index, should_cancel=should_cancel)
                            continue

                        if overloaded:
                            self._sleep_for_overload("529 Overloaded", progress_callback, chunk_index, should_cancel=should_cancel)
                            continue

                        break  # streamed without an overload / session limit

                    if connection_failed:
                        continue  # consume one retry from the outer budget

                    print("")
                    token_count = len(response_text) // 4
                    total_output_tokens += token_count
                    self.logger.info(f"Chunk {chunk_index}/{len(split_text)} attempt {attempt + 1} - Input chars: {len(chunk_str)}, Output tokens (est): {token_count}, Ratio: {token_count / len(chunk_str):.2f}")

                    # Log the API call to the database
                    self.entity_manager.log_api_call(
                        session_id=session_id, book_id=book_id, chapter_number=chapter_number,
                        chunk_index=chunk_index, total_chunks=len(split_text),
                        system_prompt=system_prompt, user_prompt=user_text,
                        response_text=response_text,
                        model_name=model_name, provider=provider.provider_name,
                        completion_tokens=token_count,
                        duration_ms=int((time.time() - call_start_time) * 1000),
                        success=0 if (repetition_detected or not response_text.strip()) else 1,
                        attempt=attempt,
                    )

                    if repetition_detected and attempt < MAX_STREAM_RETRIES:
                        continue  # retry the chunk

                    # Empty response — treat as a transient failure and retry
                    if not response_text.strip():
                        self.logger.warning(f"Empty response on chunk {chunk_index} (attempt {attempt + 1})")
                        if attempt < MAX_STREAM_RETRIES:
                            print(f"\n⚠️  Empty response on chunk {chunk_index}. Retrying...")
                            continue
                        # Last attempt — fall through to JSON parse with empty string
                        # (don't substitute a fake string that masks the real problem)

                    print("\rTranslation complete. Parsing response...                 ")

                    # Parse the completed response
                    try:
                        parsed_chunk = provider.validate_json_response(response_text)
                    except json.JSONDecodeError as e:
                        if json_fix_callback:
                            fix_action = None
                            while True:
                                fix_result = json_fix_callback(
                                    raw_response=response_text,
                                    chunk_index=chunk_index,
                                    total_chunks=len(split_text),
                                    chunk_text=chunk_str,
                                )
                                fix_action = fix_result.get("action")
                                if fix_action == "abort":
                                    raise Exception("Translation aborted by user")
                                elif fix_action == "retry":
                                    break
                                elif fix_action == "fix":
                                    try:
                                        parsed_chunk = json.loads(fix_result["json"])
                                        break
                                    except json.JSONDecodeError:
                                        response_text = fix_result["json"]
                                        continue
                            if fix_action == "retry":
                                continue  # re-enter attempt loop
                            # "fix" falls through with parsed_chunk set
                        else:
                            print("Failed to parse JSON. Writing response to json_fail_debug.txt")
                            with open('json_fail_debug.txt', 'w', encoding='utf-8') as f:
                                f.write(str(response_text))
                            print(f"Error: {e}")
                            raise
                    break  # parsed successfully — exit attempt loop
            else:
                self.logger.debug(f"Processing chunk {chunk_index} of {len(split_text)}")
                chunk_str = "\n".join(chunk)
                user_text = "Translate the following into English: \n" + chunk_str
                self.logger.debug(f"About to call {self.config.translation_model} with chunk {chunk_index} of {len(split_text)}")

                MAX_RETRIES = 2
                parsed_chunk = None
                for attempt in range(MAX_RETRIES + 1):
                    self._check_cancel(should_cancel)
                    if attempt > 0:
                        print(f"🔄 Retrying chunk {chunk_index} (attempt {attempt + 1}/{MAX_RETRIES + 1})...")
                    call_start_time = time.time()
                    try:
                        response = self._chat_completion_overload_aware(
                            provider,
                            progress_callback=progress_callback,
                            should_cancel=should_cancel,
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
                            response_format=self._entity_response_format(None, book_categories, gendered_categories)
                        )
                        response_content = provider.get_response_content(response)
                        usage = response.get("usage", {}) if isinstance(response, dict) else {}
                        self.entity_manager.log_api_call(
                            session_id=session_id, book_id=book_id, chapter_number=chapter_number,
                            chunk_index=chunk_index, total_chunks=len(split_text),
                            system_prompt=system_prompt, user_prompt=user_text,
                            response_text=response_content,
                            model_name=model_name, provider=provider.provider_name,
                            prompt_tokens=usage.get("prompt_tokens", 0),
                            completion_tokens=usage.get("completion_tokens", 0),
                            total_tokens=usage.get("total_tokens", 0),
                            duration_ms=int((time.time() - call_start_time) * 1000),
                            success=1, attempt=attempt,
                        )
                        parsed_chunk = provider.validate_json_response(response_content)
                        break
                    except json.JSONDecodeError as e:
                        if json_fix_callback:
                            fix_action = None
                            while True:
                                fix_result = json_fix_callback(
                                    raw_response=response_content,
                                    chunk_index=chunk_index,
                                    total_chunks=len(split_text),
                                    chunk_text=chunk_str,
                                )
                                fix_action = fix_result.get("action")
                                if fix_action == "abort":
                                    raise Exception("Translation aborted by user")
                                elif fix_action == "retry":
                                    break
                                elif fix_action == "fix":
                                    try:
                                        parsed_chunk = json.loads(fix_result["json"])
                                        break
                                    except json.JSONDecodeError:
                                        response_content = fix_result["json"]
                                        continue
                            if fix_action == "retry":
                                continue  # re-enter attempt loop
                            # "fix" falls through with parsed_chunk set
                            break
                        else:
                            print("Failed to parse JSON. Writing response to json_fail_debug.txt")
                            with open('json_fail_debug.txt', 'w', encoding='utf-8') as f:
                                f.write(str(response_content))
                            print(f"Error: {e}")
                            raise
                    except TranslationCancelled:
                        raise
                    except Exception as e:
                        self.logger.error(f"Connection error during chunk {chunk_index} (attempt {attempt + 1}): {e}")
                        self.entity_manager.log_api_call(
                            session_id=session_id, book_id=book_id, chapter_number=chapter_number,
                            chunk_index=chunk_index, total_chunks=len(split_text),
                            system_prompt=system_prompt, user_prompt=user_text, response_text="",
                            model_name=model_name, provider=provider.provider_name,
                            duration_ms=int((time.time() - call_start_time) * 1000),
                            success=0, attempt=attempt,
                        )
                        if attempt < MAX_RETRIES:
                            print(f"⚠️  Connection error: {e}. Retrying...")
                        else:
                            raise
            
            if parsed_chunk is None:
                # Attempt loop exhausted without a parsable response (e.g. the
                # user chose "retry" on the final attempt). Fail cleanly instead
                # of letting the None reach combine_json_chunks as a TypeError.
                raise Exception(
                    f"Chunk {chunk_index}/{len(split_text)}: retries exhausted "
                    f"without a valid translation response.")

            self.logger.info(f"Translation of chunk {chunk_index} complete.")
            self.logger.debug(f"API call completed for chunk {chunk_index}")

            # Only trust the model's chapter number from the first chunk;
            # tolerate a model that omits the field entirely.
            if chunk_index == 1:
                current_chapter = parsed_chunk.get('chapter', current_chapter)

            end_object = self.combine_json_chunks(end_object, parsed_chunk, current_chapter)

            if pass2_only:
                # Pass-2 of two-pass: entities were finalized in pass-1, so don't
                # accumulate anything new. The model is also instructed not to
                # emit an 'entities' field, but if it does, drop it on the floor.
                end_object['entities'] = {}
            else:
                # Find new entities in this chunk and record them in totally_new_entities as a running total
                new_entities_this_chunk = self.entity_manager.find_new_entities(real_old_entities, end_object['entities'])
                totally_new_entities = self.entity_manager.combine_json_entities(totally_new_entities, new_entities_this_chunk)

                # Update old_entities with the newly processed chunk's combined entities
                old_entities = self.entity_manager.combine_json_entities(old_entities, end_object['entities'])

                # Regenerate the system prompt for the next chunk to maintain consistency.
                # Pass the running summary accumulated so far (end_object['summary']
                # holds every prior chunk's summary) so the next chunk has continuity.
                if chunk_index < len(split_text):
                    system_prompt = self.generate_system_prompt(chapter_text, old_entities, do_count=False,
                                                               book_prompt_template=book_prompt_template, provider=provider,
                                                               chapter_number=chapter_number, source_language=source_language,
                                                               mode=_mode, chapter_title=chapter_title,
                                                               gendered_categories=gendered_categories, book=book_info,
                                                               chunk_index=chunk_index + 1, total_chunks=len(split_text),
                                                               previous_summary=end_object.get('summary', ''))
        
        self.logger.debug("Finished processing all chunks")

        if total_input_chars > 0:
            ratio = total_output_tokens / total_input_chars
            self.logger.info(f"Chapter completion - Total input chars: {total_input_chars}, Total output tokens: {total_output_tokens}, Overall ratio: {ratio:.2f}")
            self.entity_manager.update_token_ratio(book_id, total_input_chars, total_output_tokens)
        
        # Check for duplicate entities based on translation value
        self._check_for_translation_duplicates(end_object['entities'])
        
        # Build new_entities from the categories relevant to this book
        categories = self.entity_manager.get_book_categories(book_id) if book_id else DEFAULT_CATEGORIES
        ent_data = end_object.get('entities', {})
        new_entities = {cat: ent_data.get(cat, {}) for cat in categories}
        # Also include any extra categories the LLM may have returned
        for cat in ent_data:
            if cat not in new_entities:
                new_entities[cat] = ent_data[cat]

        # Reconcile illustration markers: guarantee every ⟦IMG:…⟧ from the source
        # survives into the translated content (the model may have dropped or
        # mangled them despite the prompt instruction).
        end_object['content'] = self.reconcile_illustration_markers(
            chapter_text, end_object.get('content', [])
        )

        return {
            "end_object": end_object,
            "new_entities": new_entities,
            "totally_new_entities": totally_new_entities,
            "old_entities": old_entities,
            "real_old_entities": real_old_entities,
            "current_chapter": current_chapter,
            "total_char_count": total_char_count
        }

    def reconcile_illustration_markers(self, source_lines, translated_lines):
        """Ensure the translated content carries exactly the source's image markers.

        The source array is the ground truth: it holds every ⟦IMG:id⟧ marker in
        known order. The model may drop, mangle, duplicate, or reorder them. This:
          1. Repairs lightly-mangled markers (e.g. 【IMG:..】, [IMG:..], inline) for
             ids that exist in the source, isolating each onto its own line.
          2. If the resulting marker sequence already matches the source, keeps the
             model's positions (the common happy path).
          3. Otherwise strips all markers and re-inserts the source set in order,
             positioning each proportionally to where it sat in the source — so
             presence and ordering are guaranteed even when the model diverged.

        Returns the (possibly rewritten) translated line list. No-op for chapters
        with no illustrations.
        """
        from illustrations import (
            markers_in, parse_marker, parse_marker_lenient, make_marker,
            MARKER_RE_LENIENT,
        )

        ground_truth = markers_in(source_lines)
        if not ground_truth:
            return translated_lines

        known = set(ground_truth)

        # 1. Normalize: split any clean/known-mangled markers onto their own lines.
        norm = []
        for line in (translated_lines or []):
            if not isinstance(line, str):
                norm.append(line)
                continue
            if parse_marker(line):
                norm.append(line.strip())
                continue
            # Look for embedded / mangled markers whose id is a real source id.
            segments = []
            last = 0
            for m in MARKER_RE_LENIENT.finditer(line):
                mid = m.group(1).lower()
                if mid not in known:
                    continue
                before = line[last:m.start()]
                if before.strip():
                    segments.append(before.strip())
                segments.append(make_marker(mid))
                last = m.end()
            if not segments:
                norm.append(line)
                continue
            tail = line[last:]
            if tail.strip():
                segments.append(tail.strip())
            norm.extend(segments)

        present = markers_in(norm)
        if present == ground_truth:
            return norm  # happy path — model preserved everything

        self.logger.warning(
            f"Illustration marker mismatch: source has {ground_truth}, "
            f"translation had {present}. Reconciling."
        )

        # 2. Strip all markers, then re-insert the ground-truth set by position.
        stripped = [l for l in norm if not (isinstance(l, str) and parse_marker(l))]
        src_marker_idx = [i for i, l in enumerate(source_lines)
                          if isinstance(l, str) and parse_marker(l)]
        S = max(1, len(source_lines))
        T = len(stripped)

        targets = []
        for k, mid in enumerate(ground_truth):
            s_idx = src_marker_idx[k] if k < len(src_marker_idx) else S
            t = int(round((s_idx / S) * T))
            t = max(0, min(t, T))
            targets.append((t, k, mid))
        targets.sort(key=lambda x: (x[0], x[1]))

        result = list(stripped)
        for offset, (t, _k, mid) in enumerate(targets):
            result.insert(t + offset, make_marker(mid))
        return result

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

