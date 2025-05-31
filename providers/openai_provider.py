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
        """
        super().__init__(api_key, base_url, **kwargs)
        
        # Initialize OpenAI client
        client_kwargs = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        
        # Filter out provider-specific config that shouldn't go to OpenAI client
        openai_kwargs = {k: v for k, v in kwargs.items() 
                        if k not in ['max_chars', 'default_model', 'models']}
        client_kwargs.update(openai_kwargs)
        
        self.client = OpenAI(**client_kwargs)
    
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
        Perform OpenAI chat completion.
        """
        # Prepare request parameters - don't include max_tokens to use model defaults
        request_params = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "top_p": top_p,
            "stream": stream,
            **kwargs  # Allow additional parameters
        }
        
        # Add response format if specified and supported
        if response_format:
            request_params["response_format"] = response_format
        
        # Remove parameters that might not be supported by all providers
        # (but keep them in kwargs for flexibility)
        openai_params = {
            k: v for k, v in request_params.items() 
            if k not in ['frequency_penalty', 'presence_penalty'] or v != 0
        }
        
        response = self.client.chat.completions.create(**openai_params)
        
        if stream:
            return StreamingResponse(response)
        else:
            # Convert to dict format for consistency
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