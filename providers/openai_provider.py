"""
OpenAI provider implementation.

This provider supports both OpenAI and OpenAI-compatible APIs like DeepSeek
by using different base URLs.
"""
from typing import Dict, List, Optional, Any, Union
from openai import OpenAI
from .base import ModelProvider, StreamingResponse


class OpenAIProvider(ModelProvider):
    """
    Provider for OpenAI and OpenAI-compatible APIs.
    
    Supports:
    - OpenAI GPT models (gpt-4, gpt-3.5-turbo, etc.)
    - DeepSeek models via OpenAI-compatible API
    - Any other OpenAI-compatible API endpoint
    """
    
    def __init__(self, api_key: str, base_url: Optional[str] = None, **kwargs):
        """
        Initialize OpenAI provider.

        Args:
            api_key: OpenAI API key
            base_url: Optional custom base URL for OpenAI-compatible APIs
                     (e.g., "https://api.deepseek.com" for DeepSeek)
            **kwargs: Additional configuration passed to OpenAI client
                     Supports max_output_tokens for configuring output token limit
        """
        super().__init__(api_key, base_url, **kwargs)

        # Store provider-specific configuration
        self.max_output_tokens = kwargs.get('max_output_tokens', 8192)

        # Initialize OpenAI client
        client_kwargs = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url

        # Filter out provider-specific config that shouldn't go to OpenAI client
        openai_kwargs = {k: v for k, v in kwargs.items()
                        if k not in ['max_chars', 'max_output_tokens', 'default_model', 'models']}
        client_kwargs.update(openai_kwargs)

        self.client = OpenAI(**client_kwargs)

    def _uses_legacy_max_tokens(self, model: str) -> bool:
        """
        Check if the model uses the legacy max_tokens parameter.

        Older models (gpt-3.5, gpt-4.x up to 4.4, gpt-4-turbo, gpt-4o) use max_tokens.
        OpenAI-compatible providers (DeepSeek, etc.) also use max_tokens.
        Newer models (o1, o3, gpt-4.5+, gpt-5+, etc.) use max_completion_tokens.

        Args:
            model: Model name to check

        Returns:
            True if model uses max_tokens (legacy), False if it uses max_completion_tokens (new)
        """
        model_lower = model.lower()

        # Check for newer models first (these use max_completion_tokens)
        if model_lower.startswith('o1') or model_lower.startswith('o3'):
            return False

        if model_lower.startswith('gpt-5') or model_lower.startswith('gpt-6'):
            return False

        # Check for gpt-4.5 and higher (these are new models)
        if model_lower.startswith('gpt-4.'):
            try:
                version_part = model_lower.replace('gpt-4.', '').split('-')[0].split()[0]
                version = float(version_part)
                if version >= 5:  # gpt-4.5 and above
                    return False
            except (ValueError, IndexError):
                pass

        # Legacy models that use max_tokens
        legacy_patterns = [
            'gpt-3.5',
            'gpt-4',  # gpt-4, gpt-4.0 through gpt-4.4
            'gpt-4-turbo',
            'gpt-4o',
            'deepseek',
        ]

        # Check if model matches any legacy pattern
        for pattern in legacy_patterns:
            if pattern == 'gpt-4':
                # Match gpt-4 and gpt-4-* but already handled gpt-4.5+ above
                if model_lower == 'gpt-4' or model_lower.startswith('gpt-4-') or model_lower.startswith('gpt-4.'):
                    return True
            elif pattern in model_lower:
                return True

        # Default: newer models use max_completion_tokens
        return False

    def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        temperature: float = 1.0,
        top_p: float = 1.0,
        max_tokens: int = 20000,
        response_format: Optional[Dict[str, str]] = None,
        stream: bool = False,
        **kwargs
    ) -> Union[Dict[str, Any], StreamingResponse]:
        """
        Perform OpenAI chat completion.
        """
        # Determine which token parameter to use based on model
        uses_legacy = self._uses_legacy_max_tokens(model)
        token_param_name = "max_tokens" if uses_legacy else "max_completion_tokens"

        # Prepare request parameters
        request_params = {
            "model": model,
            "messages": messages,
            "stream": stream,
            token_param_name: self.max_output_tokens,  # Use configured max output tokens
            **kwargs  # Allow additional parameters
        }

        # Legacy models support temperature and top_p parameters
        # Newer models (o1, o3 series) don't support these parameters
        if uses_legacy:
            request_params["temperature"] = temperature
            request_params["top_p"] = top_p

        # Add response format if specified and supported
        # Note: Newer reasoning models may have limited support for response_format
        if response_format and uses_legacy:
            request_params["response_format"] = response_format

        # Remove parameters that might not be supported by all providers
        # (but keep them in kwargs for flexibility)
        openai_params = {
            k: v for k, v in request_params.items()
            if k not in ['frequency_penalty', 'presence_penalty'] or v != 0
        }

        # Try to make the API call, with fallback for older SDK versions
        try:
            response = self.client.chat.completions.create(**openai_params)
        except TypeError as e:
            # If max_completion_tokens is not supported (older SDK), fall back to max_tokens
            if 'max_completion_tokens' in str(e) and not uses_legacy:
                # Replace max_completion_tokens with max_tokens for older SDK versions
                openai_params['max_tokens'] = openai_params.pop('max_completion_tokens')
                response = self.client.chat.completions.create(**openai_params)
            else:
                raise
        
        if stream:
            return StreamingResponse(response)
        else:
            # Convert to dict format for consistency
            print(f"API Finish reason: {response.choices[0].finish_reason}, Usage: {response.usage}")
            return {
                "choices": [
                    {
                        "message": {
                            "content": response.choices[0].message.content,
                            "role": response.choices[0].message.role
                        },
                        "finish_reason": response.choices[0].finish_reason
                    }
                ],
                "usage": {
                    "prompt_tokens": getattr(response.usage, 'prompt_tokens', 0) if response.usage else 0,
                    "completion_tokens": getattr(response.usage, 'completion_tokens', 0) if response.usage else 0,
                    "total_tokens": getattr(response.usage, 'total_tokens', 0) if response.usage else 0
                },
                "model": response.model if hasattr(response, 'model') else model
            }
    
    def get_response_content(self, response: Dict[str, Any]) -> str:
        """Extract content from completed response."""
        return response["choices"][0]["message"]["content"]
    
    def get_streaming_content(self, chunk: Any) -> Optional[str]:
        """Extract content from streaming chunk."""
        if hasattr(chunk, 'choices') and len(chunk.choices) > 0:
            delta = chunk.choices[0].delta
            if hasattr(delta, 'content') and delta.content:
                return delta.content
        return None
    
    def is_stream_complete(self, chunk: Any) -> bool:
        """Check if streaming is complete."""
        if hasattr(chunk, 'choices') and len(chunk.choices) > 0:
            return chunk.choices[0].finish_reason is not None
        return False
    
    @property
    def provider_name(self) -> str:
        """Return provider name."""
        if self.base_url and "deepseek" in self.base_url.lower():
            return "DeepSeek (via OpenAI API)"
        elif self.base_url:
            return f"OpenAI-Compatible ({self.base_url})"
        else:
            return "OpenAI"
    
    @property
    def supported_features(self) -> List[str]:
        """Return supported features."""
        return [
            "streaming",
            "json_mode", 
            "system_messages",
            "temperature_control",
            "top_p_control",
            "max_tokens"
        ]
    
    def get_usage_info(self, response: Dict[str, Any]) -> Optional[Dict[str, int]]:
        """
        Extract token usage information from response.
        
        Args:
            response: Response dictionary from chat_completion
            
        Returns:
            Dictionary with usage information or None if not available
        """
        if "usage" in response:
            return response["usage"]
        return None