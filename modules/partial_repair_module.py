"""partial_repair module — fix lines the model left partially untranslated.

Detects translated lines that still contain source-language characters (zh/ja/ko)
and re-translates just those lines in a single batched API call, using a repair
prompt template. Moved out of ui.py's hardcoded ``_fix_partial_translations``.

Auto-enables for **DeepSeek** translation models (they most often leave partial
lines); other models default off but it can be turned on per book. The model used
for the repair call is the per-run cleaning model if set, else the translation model.
"""
import json
import os
import re

from .base import TranslationModule

# Regex patterns for detecting untranslated source-language characters.
_SOURCE_LANG_PATTERNS = {
    'zh': re.compile(r'[一-鿿㐀-䶿豈-﫿]'),
    # Japanese: CJK ideographs OR hiragana/katakana.
    'ja': re.compile(r'[一-鿿㐀-䶿豈-﫿぀-ゟ゠-ヿ]'),
    # Korean: Hangul syllables and Jamo.
    'ko': re.compile(r'[가-힯ᄀ-ᇿ㄰-㆏]'),
}
_LANG_NAMES = {'zh': 'Chinese', 'ja': 'Japanese', 'ko': 'Korean'}


def _is_deepseek(model_spec):
    if not model_spec:
        return False
    spec = model_spec.lower()
    provider = spec.split(":", 1)[0] if ":" in spec else ""
    return provider in ("deepseek", "ds") or "deepseek" in spec


class PartialRepairModule(TranslationModule):
    id = "partial_repair"
    name = "Partial-Translation Repair"
    description = ("Re-translate lines the model left with source-language "
                   "characters (zh/ja/ko). Auto-on for DeepSeek translation models.")

    has_auto = True  # custom model-based auto-rule (see auto_enabled below)

    def auto_enabled(self, book, ctx):
        return _is_deepseek((ctx or {}).get("model"))

    @property
    def auto_hint(self):
        return "on for DeepSeek translation models"

    def transform_translated_lines(self, content, ctx):
        if not isinstance(content, list):
            return content
        config = ctx.get("config")
        logger = ctx.get("logger")
        book = ctx.get("book")
        source_language = (book.get("source_language") if (book and hasattr(book, "get")) else None) or "zh"

        pattern = _SOURCE_LANG_PATTERNS.get(source_language)
        if pattern is None:
            if logger:
                logger.debug(f"Partial-translation repair not supported for "
                             f"source_language='{source_language}', skipping")
            return content

        affected = [i for i, line in enumerate(content)
                    if isinstance(line, str) and pattern.search(line)]
        if not affected:
            return content

        lang_name = _LANG_NAMES.get(source_language, source_language)
        if logger:
            logger.info(f"Found {len(affected)} partially translated line(s) containing "
                        f"{lang_name} characters")

        try:
            from providers import create_provider

            repair_prompt_path = os.path.join(config.script_dir, "translation_repair_prompt.txt")
            if not os.path.exists(repair_prompt_path):
                if logger:
                    logger.error(f"translation_repair_prompt.txt not found at {repair_prompt_path}")
                return content
            with open(repair_prompt_path, "r", encoding="utf-8") as f:
                system_prompt = f.read().replace("{{LANGUAGE}}", lang_name)

            model_spec = ctx.get("cleaning_model") or config.translation_model
            provider_name, model_name = config.parse_model_spec(model_spec)
            provider = create_provider(provider_name)

            if logger:
                logger.info(f"Repairing {len(affected)} line(s) with {model_name}...")

            response = provider.chat_completion(
                model=model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": json.dumps([content[i] for i in affected],
                                                            ensure_ascii=False, indent=2)},
                ],
                temperature=0.0,
            )
            raw = provider.get_response_content(response).strip()
            if raw.startswith("```"):
                lines = raw.split("\n")
                raw = "\n".join(lines[1:-1]) if len(lines) > 2 else raw
                if raw.startswith("json"):
                    raw = raw[4:].strip()
            fixed_lines = json.loads(raw)
            if not isinstance(fixed_lines, list) or len(fixed_lines) != len(affected):
                raise ValueError(
                    f"Expected {len(affected)} fixed lines, got "
                    f"{len(fixed_lines) if isinstance(fixed_lines, list) else type(fixed_lines)}")

            result = list(content)
            for idx, fixed in zip(affected, fixed_lines):
                result[idx] = fixed
            if logger:
                logger.info(f"Repaired {len(affected)} partially translated line(s)")
            return result
        except Exception as e:  # noqa: BLE001
            if logger:
                logger.error(f"Could not repair partial translations: {e}")
            return content
