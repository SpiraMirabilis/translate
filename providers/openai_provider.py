"""
OpenAI provider implementation.

This provider supports both OpenAI and OpenAI-compatible APIs like DeepSeek
by using different base URLs.
"""
from typing import Dict, List, Optional, Any, Union
import httpx
from openai import OpenAI
from .base import ModelProvider, StreamingResponse


# Models that rejected temperature/top_p or response_format at runtime.
# Memoized per-process (keyed by model name) so later calls to the same model
# skip the incompatible parameter and avoid a wasted round-trip.
_MODELS_REJECTING_SAMPLING = set()
_MODELS_REJECTING_JSON_MODE = set()


class OpenAIProvider(ModelProvider):
    """
    Provider for OpenAI and OpenAI-compatible APIs.

    Supports:
    - OpenAI GPT models (gpt-5.x, gpt-4.1, o-series)
    - DeepSeek, xAI (Grok), Meta (Muse), and other OpenAI-compatible APIs
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
        client_kwargs = {
            "api_key": api_key,
            "timeout": httpx.Timeout(
                connect=self.connect_timeout_seconds,
                read=self.request_timeout_seconds,
                write=60.0,
                pool=30.0,
            ),
            "max_retries": self.max_retries,
        }
        if base_url:
            client_kwargs["base_url"] = base_url

        # Filter out provider-specific config that shouldn't go to OpenAI client
        openai_kwargs = {k: v for k, v in kwargs.items()
                        if k not in ['max_chars', 'max_output_tokens', 'default_model', 'models',
                                     'connect_timeout_seconds', 'request_timeout_seconds', 'max_retries']}
        client_kwargs.update(openai_kwargs)

        self.client = OpenAI(**client_kwargs)

    def _is_reasoning_model(self, model: str) -> bool:
        """
        OpenAI reasoning-tier models (o-series, gpt-5+) require
        max_completion_tokens and reject temperature/top_p outright.
        Everything else — including OpenAI-compatible providers like
        DeepSeek/xAI/Meta — takes max_tokens plus sampling params.
        """
        return model.lower().startswith(('o1', 'o3', 'o4', 'gpt-5', 'gpt-6'))

    def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        temperature: float = 1.0,
        top_p: float = 1.0,
        max_tokens: int = 20000,
        response_format: Optional[Dict[str, str]] = None,
        stream: bool = False,
        thinking_effort: Optional[str] = None,  # absorbed; see base (no-op here)
        **kwargs
    ) -> Union[Dict[str, Any], StreamingResponse]:
        """
        Perform OpenAI chat completion.
        """
        # Determine which token parameter to use based on model
        reasoning = self._is_reasoning_model(model)
        token_param_name = "max_completion_tokens" if reasoning else "max_tokens"

        # Prepare request parameters
        request_params = {
            "model": model,
            "messages": messages,
            "stream": stream,
            token_param_name: self.max_output_tokens,  # Use configured max output tokens
            **kwargs  # Allow additional parameters
        }

        # Reasoning models (o-series, gpt-5+) reject sampling parameters;
        # everything else gets them unless it complained on a previous call.
        if not reasoning and model not in _MODELS_REJECTING_SAMPLING:
            request_params["temperature"] = temperature
            request_params["top_p"] = top_p

        if response_format and model not in _MODELS_REJECTING_JSON_MODE:
            # Strip non-OpenAI extension keys (e.g. "mode", used internally to
            # pick a schema variant for providers that support it) before
            # sending — OpenAI rejects unknown fields in response_format.
            safe_response_format = {k: v for k, v in response_format.items() if k in ("type", "schema", "json_schema")}
            request_params["response_format"] = safe_response_format

        # Remove parameters that might not be supported by all providers
        # (but keep them in kwargs for flexibility)
        openai_params = {
            k: v for k, v in request_params.items()
            if k not in ['frequency_penalty', 'presence_penalty'] or v != 0
        }

        # Make the API call, dropping/renaming parameters an OpenAI-compatible
        # backend rejects. Each fallback branch removes or renames a parameter
        # and is guarded on its presence, so the loop always terminates.
        while True:
            try:
                response = self.client.chat.completions.create(**openai_params)
                break
            except TypeError as e:
                # Older SDKs don't know max_completion_tokens
                if 'max_completion_tokens' in str(e) and 'max_completion_tokens' in openai_params:
                    openai_params['max_tokens'] = openai_params.pop('max_completion_tokens')
                    continue
                raise
            except Exception as e:
                msg = str(e)
                if 'max_tokens' in msg and 'max_completion_tokens' in msg and 'max_tokens' in openai_params:
                    # Backend wants the newer parameter name
                    openai_params['max_completion_tokens'] = openai_params.pop('max_tokens')
                    continue
                if 'response_format' in msg and 'response_format' in openai_params:
                    _MODELS_REJECTING_JSON_MODE.add(model)
                    del openai_params['response_format']
                    continue
                if ('temperature' in msg or 'top_p' in msg) and 'temperature' in openai_params:
                    _MODELS_REJECTING_SAMPLING.add(model)
                    openai_params.pop('temperature', None)
                    openai_params.pop('top_p', None)
                    continue
                raise
        
        if stream:
            return StreamingResponse(response)
        else:
            # Convert to dict format for consistency
            #print(f"API Finish reason: {response.choices[0].finish_reason}, Usage: {response.usage}")
            raw_content = response.choices[0].message.content
            # Strip markdown fences from JSON responses — some backends
            # ignore response_format (or had it dropped above) and wrap
            # JSON in ```json ... ``` blocks.
            if response_format and response_format.get("type") == "json_object" and raw_content:
                raw_content = self._strip_markdown_fences(raw_content)
            return {
                "choices": [
                    {
                        "message": {
                            "content": raw_content,
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
        elif self.base_url and "x.ai" in self.base_url.lower():
            return "xAI (via OpenAI API)"
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
