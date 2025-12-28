"""
Google Gemini provider implementation using the Google Generative AI API.

This provider supports Gemini models (Gemini 1.5 Pro, Gemini 1.5 Flash, etc.)
through the official Google Generative AI SDK.
"""
from typing import Dict, List, Optional, Any, Union
import json
from .base import ModelProvider, StreamingResponse

try:
    import google.generativeai as genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False


class GeminiProvider(ModelProvider):
    """
    Provider for Google Gemini models.
    
    Supports:
    - Gemini 1.5 Pro
    - Gemini 1.5 Flash  
    - Gemini 1.0 Pro
    - Other Gemini models as they become available
    
    Note: Requires 'google-generativeai' package to be installed.
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
                "google-generativeai package is required for Gemini provider. "
                "Install with: pip install google-generativeai"
            )

        super().__init__(api_key, base_url, **kwargs)

        # Store provider-specific configuration
        self.max_output_tokens = kwargs.get('max_output_tokens', None)

        genai.configure(api_key=api_key)
    
    def _convert_messages_to_gemini_format(self, messages: List[Dict[str, Any]]) -> tuple[Optional[str], List[Dict[str, Any]]]:
        """
        Convert OpenAI-style messages to Gemini format.
        
        Returns:
            Tuple of (system_instruction, converted_messages)
        """
        system_instruction = None
        gemini_messages = []
        
        for msg in messages:
            role = msg["role"]
            content = msg["content"]
            
            if role == "system":
                # Gemini handles system messages as system_instruction
                if isinstance(content, list):
                    system_instruction = "\n".join([
                        item["text"] for item in content 
                        if item.get("type") == "text"
                    ])
                else:
                    system_instruction = content
            elif role == "user":
                # Convert content format if needed
                if isinstance(content, list):
                    text_content = "\n".join([
                        item["text"] for item in content
                        if item.get("type") == "text"
                    ])
                else:
                    text_content = content
                
                gemini_messages.append({
                    "role": "user",
                    "parts": [text_content]
                })
            elif role == "assistant":
                # Convert content format if needed
                if isinstance(content, list):
                    text_content = "\n".join([
                        item["text"] for item in content
                        if item.get("type") == "text"
                    ])
                else:
                    text_content = content
                
                gemini_messages.append({
                    "role": "model",  # Gemini uses "model" instead of "assistant"
                    "parts": [text_content]
                })
        
        return system_instruction, gemini_messages
    
    def _create_response_schema(self, response_format: Optional[Dict[str, str]]) -> Optional[Dict[str, Any]]:
        """
        Create Gemini response schema from OpenAI-style response_format.
        
        This matches the expected structure from the translation engine based on system_prompt.txt.
        """
        if not response_format or response_format.get("type") != "json_object":
            return None
        
        # Translation response schema matching the system prompt template
        return {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "The title of the chapter"
                },
                "chapter": {
                    "type": "integer",
                    "description": "The chapter number"
                },
                "summary": {
                    "type": "string",
                    "description": "A concise 75-word or less summary of the chapter"
                },
                "content": {
                    "type": "array",
                    "items": {
                        "type": "string"
                    },
                    "description": "Array of translated content lines"
                },
                "entities": {
                    "type": "object",
                    "properties": {
                        "characters": {
                            "type": "object",
                            "properties": {
                                "example_character": {
                                    "type": "object",
                                    "properties": {
                                        "translation": {"type": "string"},
                                        "gender": {"type": "string"},
                                        "last_chapter": {"type": "integer"}
                                    }
                                }
                            }
                        },
                        "places": {
                            "type": "object",
                            "properties": {
                                "example_place": {
                                    "type": "object",
                                    "properties": {
                                        "translation": {"type": "string"},
                                        "last_chapter": {"type": "integer"}
                                    }
                                }
                            }
                        },
                        "organizations": {
                            "type": "object",
                            "properties": {
                                "example_organization": {
                                    "type": "object",
                                    "properties": {
                                        "translation": {"type": "string"},
                                        "last_chapter": {"type": "integer"}
                                    }
                                }
                            }
                        },
                        "abilities": {
                            "type": "object",
                            "properties": {
                                "example_ability": {
                                    "type": "object",
                                    "properties": {
                                        "translation": {"type": "string"},
                                        "last_chapter": {"type": "integer"}
                                    }
                                }
                            }
                        },
                        "titles": {
                            "type": "object",
                            "properties": {
                                "example_title": {
                                    "type": "object",
                                    "properties": {
                                        "translation": {"type": "string"},
                                        "last_chapter": {"type": "integer"}
                                    }
                                }
                            }
                        },
                        "equipment": {
                            "type": "object",
                            "properties": {
                                "example_equipment": {
                                    "type": "object",
                                    "properties": {
                                        "translation": {"type": "string"},
                                        "last_chapter": {"type": "integer"}
                                    }
                                }
                            }
                        }
                    }
                }
            },
            "required": ["title", "chapter", "summary", "content", "entities"]
        }
    
    def chat_completion(
        self,
        messages: List[Dict[str, Any]], 
        model: str,
        temperature: float = 1.0,
        top_p: float = 1.0,
        max_tokens: int = 8192,
        response_format: Optional[Dict[str, str]] = None,
        stream: bool = False,
        **kwargs
    ) -> Union[Dict[str, Any], StreamingResponse]:
        """
        Perform Gemini chat completion.
        
        Note: Gemini API has some differences from OpenAI:
        - System messages are handled as system_instruction
        - Messages use "parts" instead of "content"
        - Assistant role is called "model"
        - JSON mode requires response_schema specification
        """
        # Convert messages to Gemini format
        system_instruction, gemini_messages = self._convert_messages_to_gemini_format(messages)
        
        # Create model instance
        generation_config = {
            "temperature": temperature,
            "top_p": top_p,
        }

        # Add max_output_tokens if configured (otherwise use model's default)
        if self.max_output_tokens is not None:
            generation_config["max_output_tokens"] = self.max_output_tokens
        
        # Handle JSON mode with response schema
        if response_format and response_format.get("type") == "json_object":
            generation_config["response_mime_type"] = "application/json"
            response_schema = self._create_response_schema(response_format)
            if response_schema:
                generation_config["response_schema"] = response_schema
        
        # Add any additional generation config from kwargs
        generation_config.update(kwargs.get('generation_config', {}))
        
        # Safety settings to allow fictional content - disable all safety filters
        from google.generativeai.types import HarmCategory, HarmBlockThreshold
        
        # Core Gemini harm categories (per API error message)
        core_categories = [
            "HARM_CATEGORY_HARASSMENT",
            "HARM_CATEGORY_HATE_SPEECH", 
            "HARM_CATEGORY_SEXUALLY_EXPLICIT",
            "HARM_CATEGORY_DANGEROUS_CONTENT",
            "HARM_CATEGORY_CIVIC_INTEGRITY"
        ]
        
        # Additional legacy categories that may exist in newer versions
        additional_categories = [
            "HARM_CATEGORY_DEROGATORY",
            "HARM_CATEGORY_TOXICITY", 
            "HARM_CATEGORY_VIOLENCE",
            "HARM_CATEGORY_SEXUAL",
            "HARM_CATEGORY_MEDICAL",
            "HARM_CATEGORY_DANGEROUS"
        ]
        
        safety_settings = []
        
        # Add core categories with BLOCK_NONE threshold (most permissive available in SDK)
        for category_name in core_categories:
            if hasattr(HarmCategory, category_name):
                safety_settings.append({
                    "category": getattr(HarmCategory, category_name),
                    "threshold": HarmBlockThreshold.BLOCK_NONE
                })
        
        # Add additional categories if they exist
        for category_name in additional_categories:
            if hasattr(HarmCategory, category_name):
                safety_settings.append({
                    "category": getattr(HarmCategory, category_name),
                    "threshold": HarmBlockThreshold.BLOCK_NONE
                })
        
        model_instance = genai.GenerativeModel(
            model_name=model,
            system_instruction=system_instruction,
            generation_config=generation_config,
            safety_settings=safety_settings
        )
        
        if stream:
            # Streaming response
            try:
                response_stream = model_instance.generate_content(
                    gemini_messages,
                    stream=True
                )
                # Check if response_stream is actually iterable
                if hasattr(response_stream, '__iter__'):
                    return StreamingResponse(iter(response_stream))
                else:
                    # Fallback to non-streaming if streaming fails
                    response = response_stream
                    return {
                        "choices": [
                            {
                                "message": {
                                    "content": self._get_response_text(response),
                                    "role": "assistant"
                                },
                                "finish_reason": self._map_finish_reason(response.candidates[0].finish_reason if response.candidates else None)
                            }
                        ],
                        "usage": {
                            "prompt_tokens": getattr(response.usage_metadata, 'prompt_token_count', 0) if hasattr(response, 'usage_metadata') and response.usage_metadata else 0,
                            "completion_tokens": getattr(response.usage_metadata, 'candidates_token_count', 0) if hasattr(response, 'usage_metadata') and response.usage_metadata else 0,
                            "total_tokens": getattr(response.usage_metadata, 'total_token_count', 0) if hasattr(response, 'usage_metadata') and response.usage_metadata else 0
                        },
                        "model": model
                    }
            except Exception as e:
                # If streaming fails, fall back to non-streaming
                response = model_instance.generate_content(gemini_messages)
                return {
                    "choices": [
                        {
                            "message": {
                                "content": self._get_response_text(response),
                                "role": "assistant"
                            },
                            "finish_reason": self._map_finish_reason(response.candidates[0].finish_reason if response.candidates else None)
                        }
                    ],
                    "usage": {
                        "prompt_tokens": getattr(response.usage_metadata, 'prompt_token_count', 0) if hasattr(response, 'usage_metadata') and response.usage_metadata else 0,
                        "completion_tokens": getattr(response.usage_metadata, 'candidates_token_count', 0) if hasattr(response, 'usage_metadata') and response.usage_metadata else 0,
                        "total_tokens": getattr(response.usage_metadata, 'total_token_count', 0) if hasattr(response, 'usage_metadata') and response.usage_metadata else 0
                    },
                    "model": model
                }
        else:
            # Non-streaming response
            response = model_instance.generate_content(gemini_messages)
            
            # Convert to OpenAI-compatible format
            return {
                "choices": [
                    {
                        "message": {
                            "content": self._get_response_text(response),
                            "role": "assistant"
                        },
                        "finish_reason": self._map_finish_reason(response.candidates[0].finish_reason if response.candidates else None)
                    }
                ],
                "usage": {
                    "prompt_tokens": getattr(response.usage_metadata, 'prompt_token_count', 0) if hasattr(response, 'usage_metadata') and response.usage_metadata else 0,
                    "completion_tokens": getattr(response.usage_metadata, 'candidates_token_count', 0) if hasattr(response, 'usage_metadata') and response.usage_metadata else 0,
                    "total_tokens": getattr(response.usage_metadata, 'total_token_count', 0) if hasattr(response, 'usage_metadata') and response.usage_metadata else 0
                },
                "model": model
            }
    
    def _get_response_text(self, response) -> str:
        """Safely extract text from Gemini response, handling safety filter cases."""
        try:
            return response.text if response.text else ""
        except ValueError as e:
            # Handle safety filter or other response issues with detailed debugging
            print(f"DEBUG: ValueError when getting response text: {e}")
            
            if hasattr(response, 'candidates') and response.candidates:
                candidate = response.candidates[0]
                print(f"DEBUG: Candidate finish_reason: {getattr(candidate, 'finish_reason', 'None')}")
                
                # Check for safety ratings
                if hasattr(candidate, 'safety_ratings'):
                    print(f"DEBUG: Safety ratings: {candidate.safety_ratings}")
                
                # Check for content
                if hasattr(candidate, 'content'):
                    print(f"DEBUG: Candidate content: {candidate.content}")
                
                if hasattr(candidate, 'finish_reason'):
                    finish_reason = candidate.finish_reason
                    # Convert enum to string if needed
                    if hasattr(finish_reason, 'name'):
                        reason_name = finish_reason.name
                    else:
                        reason_name = str(finish_reason)
                    
                    print(f"DEBUG: Finish reason name: {reason_name}")
                    print(f"DEBUG: Finish reason value: {finish_reason}")
                    
                    # Mapping based on Gemini API documentation:
                    # STOP = Natural stop point
                    # MAX_TOKENS = Maximum number of tokens reached  
                    # SAFETY = Content flagged for safety reasons
                    # RECITATION = Content flagged for recitation reasons
                    # LANGUAGE = Unsupported language
                    # OTHER = Unknown reason
                    # BLOCKLIST = Contains forbidden terms
                    # PROHIBITED_CONTENT = Potentially prohibited content
                    # SPII = Potentially contains sensitive info
                    # MALFORMED_FUNCTION_CALL = Invalid function call
                    
                    if reason_name in ["SAFETY"] or finish_reason == 3:
                        return "Error: Content blocked by safety filter"
                    elif reason_name in ["RECITATION"] or finish_reason == 4:
                        return "Error: Content blocked due to recitation"
                    elif reason_name in ["MAX_TOKENS"] or finish_reason == 2:
                        return f"Error: Response truncated due to max tokens limit. Try increasing max_tokens or reducing input size."
                    elif reason_name in ["BLOCKLIST", "PROHIBITED_CONTENT"]:
                        return f"Error: Content blocked due to content policy ({reason_name})"
                    elif reason_name in ["LANGUAGE"]:
                        return f"Error: Unsupported language detected"
                    elif reason_name in ["SPII"]:
                        return f"Error: Content flagged for sensitive information"
                    else:
                        return f"Error: No content returned (finish_reason: {reason_name}, value: {finish_reason})"
            else:
                print(f"DEBUG: No candidates in response or empty candidates")
                
            return f"Error: {str(e)}"
    
    def _map_finish_reason(self, gemini_finish_reason) -> str:
        """Map Gemini finish reason to OpenAI-compatible format."""
        if not gemini_finish_reason:
            return "stop"
        
        # Map Gemini finish reasons to OpenAI equivalents
        mapping = {
            "STOP": "stop",
            "MAX_TOKENS": "length",
            "SAFETY": "content_filter",
            "RECITATION": "content_filter",
            "OTHER": "stop"
        }
        
        return mapping.get(str(gemini_finish_reason), "stop")
    
    def get_response_content(self, response: Dict[str, Any]) -> str:
        """Extract content from completed response."""
        return response["choices"][0]["message"]["content"]
    
    def get_streaming_content(self, chunk: Any) -> Optional[str]:
        """Extract content from streaming chunk."""
        try:
            # First try to access text directly, but catch ValueError for safety filter issues
            if hasattr(chunk, 'text'):
                try:
                    text = chunk.text
                    if text:
                        return text
                except ValueError:
                    # Fall through to manual content extraction
                    pass
            
            # Try to extract content manually from candidates
            if hasattr(chunk, 'candidates') and chunk.candidates:
                candidate = chunk.candidates[0]
                if hasattr(candidate, 'content') and hasattr(candidate.content, 'parts'):
                    parts = candidate.content.parts
                    if parts and hasattr(parts[0], 'text'):
                        return parts[0].text
        except (AttributeError, IndexError, ValueError):
            pass
        
        return None
    
    def is_stream_complete(self, chunk: Any) -> bool:
        """Check if streaming is complete."""
        try:
            # Check if chunk has candidates and if the finish_reason indicates completion
            if hasattr(chunk, 'candidates') and chunk.candidates:
                candidate = chunk.candidates[0]
                if hasattr(candidate, 'finish_reason') and candidate.finish_reason:
                    finish_reason = candidate.finish_reason
                    # Convert enum to string if needed
                    if hasattr(finish_reason, 'name'):
                        finish_reason = finish_reason.name
                    else:
                        finish_reason = str(finish_reason)
                    
                    # Consider stream complete for these finish reasons
                    completion_reasons = ["STOP", "MAX_TOKENS", "SAFETY", "RECITATION", "OTHER"]
                    return finish_reason in completion_reasons
        except (AttributeError, IndexError):
            pass
        
        # If we can't determine, assume not complete
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
            "json_mode_with_schema",  # Gemini supports structured output with schemas
            "structured_output"
        ]
    
    
    
    
