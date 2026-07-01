"""
Google Gemini provider implementation using the google-genai SDK.

This provider supports Gemini models (2.5 Pro/Flash, 1.5 Pro/Flash, etc.)
through the official google-genai SDK (the replacement for the deprecated
google-generativeai package).
"""
from typing import Dict, List, Optional, Any, Union
from .base import ModelProvider, StreamingResponse

try:
    from google import genai
    from google.genai import types as genai_types
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False


class GeminiProvider(ModelProvider):
    """
    Provider for Google Gemini models via the google-genai SDK.

    Note: Requires 'google-genai' package to be installed.
    """

    def __init__(self, api_key: str, base_url: Optional[str] = None, **kwargs):
        """
        Initialize Gemini provider.

        Args:
            api_key: Google AI API key
            base_url: Not used for Gemini, kept for interface compatibility
            **kwargs: Additional configuration
                     Supports max_output_tokens for configuring output token limit
        """
        if not GEMINI_AVAILABLE:
            raise ImportError(
                "google-genai package is required for Gemini provider. "
                "Install with: pip install google-genai"
            )

        super().__init__(api_key, base_url, **kwargs)

        self.max_output_tokens = kwargs.get('max_output_tokens', None)
        self.client = genai.Client(api_key=api_key)

    def _convert_messages_to_gemini_format(
        self, messages: List[Dict[str, Any]]
    ) -> tuple[Optional[str], List[Any]]:
        """
        Convert OpenAI-style messages to google-genai Content objects.

        Returns:
            Tuple of (system_instruction, list of Content objects)
        """
        system_instruction = None
        contents = []

        for msg in messages:
            role = msg["role"]
            content = msg["content"]

            if isinstance(content, list):
                text_content = "\n".join(
                    item["text"] for item in content if item.get("type") == "text"
                )
            else:
                text_content = content

            if role == "system":
                system_instruction = text_content
            elif role == "user":
                contents.append(
                    genai_types.Content(
                        role="user",
                        parts=[genai_types.Part(text=text_content)],
                    )
                )
            elif role == "assistant":
                contents.append(
                    genai_types.Content(
                        role="model",
                        parts=[genai_types.Part(text=text_content)],
                    )
                )

        return system_instruction, contents

    def _create_response_schema(self, response_format: Optional[Dict[str, str]]) -> Optional[Dict[str, Any]]:
        """
        Create Gemini response schema from OpenAI-style response_format.

        Matches the translation engine's expected output structure.

        Supports a non-standard "mode" key in response_format:
            - "entity_only": pass-1 of two-pass mode — only the entities field
            - "translate_only": pass-2 of two-pass mode — title/chapter/summary/content only
            - unset / "full" (default): full schema with everything
        """
        if not response_format or response_format.get("type") != "json_object":
            return None

        mode = response_format.get("mode", "full")

        def _cat(extra_props=None):
            inner = {"translation": {"type": "string"}, "last_chapter": {"type": "integer"}}
            if extra_props:
                inner.update(extra_props)
            return {
                "type": "object",
                "properties": {
                    "example": {"type": "object", "properties": inner},
                },
            }

        # Build the entities schema from the book's actual categories when the
        # engine supplies them, marking gender-tracked categories with a gender
        # field. Fall back to the legacy default category set otherwise.
        categories = response_format.get("categories")
        gendered = set(response_format.get("gendered_categories") or [])
        if categories:
            entity_props = {
                cat: _cat({"gender": {"type": "string"}} if cat in gendered else None)
                for cat in categories
            }
        else:
            entity_props = {
                "characters": _cat({"gender": {"type": "string"}}),
                "places": _cat(),
                "organizations": _cat(),
                "abilities": _cat(),
                "titles": _cat(),
                "equipment": _cat(),
                "creatures": _cat(),
            }
        entities_schema = {
            "type": "object",
            "properties": entity_props,
        }

        text_props = {
            "title": {
                "type": "string",
                "description": "The title of the chapter",
            },
            "chapter": {
                "type": "integer",
                "description": "The chapter number",
            },
            "summary": {
                "type": "string",
                "description": "A concise 75-word or less summary of the chapter",
            },
            "content": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Array of translated content lines",
            },
        }

        if mode == "entity_only":
            return {
                "type": "object",
                "properties": {"entities": entities_schema},
                "required": ["entities"],
            }
        if mode == "translate_only":
            return {
                "type": "object",
                "properties": text_props,
                "required": ["title", "chapter", "summary", "content"],
            }
        # Default ("full"): everything required
        return {
            "type": "object",
            "properties": {**text_props, "entities": entities_schema},
            "required": ["title", "chapter", "summary", "content", "entities"],
        }

    def _build_safety_settings(self) -> List[Any]:
        """Build the most permissive safety settings the SDK exposes."""
        # Categories present in the current SDK; CIVIC_INTEGRITY is sometimes
        # rejected by the API depending on the model, so try BLOCK_NONE first
        # and let the API surface any errors rather than guessing here.
        category_names = [
            "HARM_CATEGORY_HARASSMENT",
            "HARM_CATEGORY_HATE_SPEECH",
            "HARM_CATEGORY_SEXUALLY_EXPLICIT",
            "HARM_CATEGORY_DANGEROUS_CONTENT",
            "HARM_CATEGORY_CIVIC_INTEGRITY",
        ]
        settings = []
        for name in category_names:
            if hasattr(genai_types.HarmCategory, name):
                settings.append(
                    genai_types.SafetySetting(
                        category=getattr(genai_types.HarmCategory, name),
                        threshold=genai_types.HarmBlockThreshold.BLOCK_NONE,
                    )
                )
        return settings

    def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        temperature: float = 1.0,
        top_p: float = 1.0,
        max_tokens: int = 8192,
        response_format: Optional[Dict[str, str]] = None,
        stream: bool = False,
        **kwargs,
    ) -> Union[Dict[str, Any], StreamingResponse]:
        """
        Perform a Gemini chat completion via the google-genai SDK.
        """
        system_instruction, contents = self._convert_messages_to_gemini_format(messages)

        config_kwargs: Dict[str, Any] = {
            "temperature": temperature,
            "top_p": top_p,
            "safety_settings": self._build_safety_settings(),
        }

        if system_instruction:
            config_kwargs["system_instruction"] = system_instruction

        if self.max_output_tokens is not None:
            config_kwargs["max_output_tokens"] = self.max_output_tokens

        if response_format and response_format.get("type") == "json_object":
            config_kwargs["response_mime_type"] = "application/json"
            schema = self._create_response_schema(response_format)
            if schema:
                config_kwargs["response_schema"] = schema

        # Allow callers to pass arbitrary additional config overrides via
        # kwargs['generation_config'] (preserves prior interface).
        config_kwargs.update(kwargs.get('generation_config', {}))

        config = genai_types.GenerateContentConfig(**config_kwargs)

        if stream:
            response_stream = self.client.models.generate_content_stream(
                model=model,
                contents=contents,
                config=config,
            )
            return StreamingResponse(iter(response_stream))

        response = self.client.models.generate_content(
            model=model,
            contents=contents,
            config=config,
        )
        return self._response_to_openai_dict(response, model)

    def _response_to_openai_dict(self, response: Any, model: str) -> Dict[str, Any]:
        """Wrap a GenerateContentResponse in OpenAI-style output."""
        finish_reason = None
        if getattr(response, "candidates", None):
            finish_reason = getattr(response.candidates[0], "finish_reason", None)

        usage = getattr(response, "usage_metadata", None)
        return {
            "choices": [
                {
                    "message": {
                        "content": self._get_response_text(response),
                        "role": "assistant",
                    },
                    "finish_reason": self._map_finish_reason(finish_reason),
                }
            ],
            "usage": {
                "prompt_tokens": getattr(usage, "prompt_token_count", 0) or 0,
                "completion_tokens": getattr(usage, "candidates_token_count", 0) or 0,
                "total_tokens": getattr(usage, "total_token_count", 0) or 0,
            },
            "model": model,
        }

    def _get_response_text(self, response) -> str:
        """Safely extract text from a Gemini response, handling safety-filter cases."""
        try:
            text = getattr(response, "text", None)
            if text:
                return text
        except ValueError as e:
            return self._format_response_error(response, e)

        # `text` is None/empty — surface a useful error from finish_reason.
        if getattr(response, "candidates", None):
            return self._format_response_error(response, None)

        return ""

    def _format_response_error(self, response, exc: Optional[Exception]) -> str:
        """Generate a human-readable error string when text extraction fails."""
        if not getattr(response, "candidates", None):
            return f"Error: {exc}" if exc else "Error: No response candidates returned"

        candidate = response.candidates[0]
        finish_reason = getattr(candidate, "finish_reason", None)
        reason_name = getattr(finish_reason, "name", str(finish_reason)) if finish_reason else "UNKNOWN"

        if reason_name == "SAFETY":
            return "Error: Content blocked by safety filter"
        if reason_name == "RECITATION":
            return "Error: Content blocked due to recitation"
        if reason_name == "MAX_TOKENS":
            return "Error: Response truncated due to max tokens limit. Try increasing max_tokens or reducing input size."
        if reason_name in ("BLOCKLIST", "PROHIBITED_CONTENT"):
            return f"Error: Content blocked due to content policy ({reason_name})"
        if reason_name == "LANGUAGE":
            return "Error: Unsupported language detected"
        if reason_name == "SPII":
            return "Error: Content flagged for sensitive information"
        return f"Error: No content returned (finish_reason: {reason_name})"

    def _map_finish_reason(self, gemini_finish_reason) -> str:
        """Map Gemini finish reason to OpenAI-compatible format."""
        if gemini_finish_reason is None:
            return "stop"

        name = getattr(gemini_finish_reason, "name", str(gemini_finish_reason))
        mapping = {
            "STOP": "stop",
            "MAX_TOKENS": "length",
            "SAFETY": "content_filter",
            "RECITATION": "content_filter",
            "BLOCKLIST": "content_filter",
            "PROHIBITED_CONTENT": "content_filter",
            "SPII": "content_filter",
            "LANGUAGE": "stop",
            "OTHER": "stop",
        }
        return mapping.get(name, "stop")

    def get_response_content(self, response: Dict[str, Any]) -> str:
        """Extract content from completed response."""
        return response["choices"][0]["message"]["content"]

    def get_streaming_content(self, chunk: Any) -> Optional[str]:
        """Extract content from a streaming chunk."""
        try:
            text = getattr(chunk, "text", None)
            if text:
                return text
        except (ValueError, AttributeError):
            pass

        try:
            candidates = getattr(chunk, "candidates", None)
            if candidates:
                content = getattr(candidates[0], "content", None)
                parts = getattr(content, "parts", None) if content else None
                if parts:
                    return getattr(parts[0], "text", None)
        except (AttributeError, IndexError):
            pass

        return None

    def is_stream_complete(self, chunk: Any) -> bool:
        """Check if a streaming chunk indicates the stream is complete."""
        try:
            candidates = getattr(chunk, "candidates", None)
            if not candidates:
                return False
            finish_reason = getattr(candidates[0], "finish_reason", None)
            if not finish_reason:
                return False
            name = getattr(finish_reason, "name", str(finish_reason))
            return name in {
                "STOP", "MAX_TOKENS", "SAFETY", "RECITATION",
                "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "LANGUAGE", "OTHER",
            }
        except (AttributeError, IndexError):
            return False

    @property
    def provider_name(self) -> str:
        """Return provider name."""
        return "Google Gemini"

    @property
    def supported_features(self) -> List[str]:
        """Return supported features."""
        return [
            "streaming",
            "system_messages",
            "temperature_control",
            "top_p_control",
            "max_tokens",
            "json_mode_with_schema",
            "structured_output",
        ]
