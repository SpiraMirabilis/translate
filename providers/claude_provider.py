"""
Claude provider implementation using the Anthropic API.

This provider supports Claude models (Claude 3.5 Sonnet, Claude 3 Opus, etc.)
through the official Anthropic API.
"""
from typing import Dict, List, Optional, Any, Union
from .base import ModelProvider, StreamingResponse, OverloadedError

try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False


# Models that reject the `temperature` parameter outright (e.g. claude-opus-4-8).
# Populated lazily the first time the API rejects it, so subsequent calls in the
# same process skip sending temperature and avoid a wasted round-trip.
_MODELS_REJECTING_TEMPERATURE = set()


class ClaudeProvider(ModelProvider):
    """
    Provider for Anthropic Claude models.
    
    Supports:
    - Claude 3.5 Sonnet
    - Claude 3 Opus  
    - Claude 3 Haiku
    - Other Claude models as they become available
    
    Note: Requires 'anthropic' package to be installed.
    """
    
    def __init__(self, api_key: str, base_url: Optional[str] = None, **kwargs):
        """
        Initialize Claude provider.

        Args:
            api_key: Anthropic API key
            base_url: Not used for Claude, kept for interface compatibility
            **kwargs: Additional configuration
                     Supports max_output_tokens for configuring output token limit
        """
        if not ANTHROPIC_AVAILABLE:
            raise ImportError(
                "anthropic package is required for Claude provider. "
                "Install with: pip install anthropic"
            )

        super().__init__(api_key, base_url, **kwargs)

        # Store provider-specific configuration
        self.max_output_tokens = kwargs.get('max_output_tokens', 8192)

        import httpx
        self.client = anthropic.Anthropic(
            api_key=api_key,
            timeout=httpx.Timeout(
                connect=self.connect_timeout_seconds,
                read=self.request_timeout_seconds,
                write=60.0,
                pool=30.0,
            ),
            max_retries=self.max_retries,
        )
    
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
        Perform Claude chat completion.
        
        Note: Claude API has some differences from OpenAI:
        - System messages are handled separately
        - JSON mode is implemented via prompt instructions
        - Different parameter names and ranges
        """
        # Convert OpenAI-style messages to Claude format
        system_message = None
        claude_messages = []
        
        for msg in messages:
            if msg["role"] == "system":
                # Claude handles system messages separately
                if isinstance(msg["content"], list):
                    # Extract text from content list
                    system_message = "\n".join([
                        item["text"] for item in msg["content"] 
                        if item.get("type") == "text"
                    ])
                else:
                    system_message = msg["content"]
            else:
                # Convert content format if needed
                if isinstance(msg["content"], list):
                    content = "\n".join([
                        item["text"] for item in msg["content"]
                        if item.get("type") == "text"
                    ])
                else:
                    content = msg["content"]
                
                claude_messages.append({
                    "role": msg["role"],
                    "content": content
                })
        
        # Handle JSON mode via prompt instruction.
        # Note: prefilling (trailing assistant message) is not supported on newer models.
        # Instead we instruct the model and strip any markdown fences from the response.
        json_mode = response_format and response_format.get("type") == "json_object"
        if json_mode:
            json_instruction = (
                "\n\nIMPORTANT: You must respond with valid JSON only. "
                "Do not include any text before or after the JSON object. "
                "Do not wrap the JSON in markdown code fences."
            )
            if claude_messages:
                claude_messages[-1]["content"] += json_instruction
        
        # Prepare Claude-specific parameters
        # Note: Anthropic API rejects requests with both temperature and top_p set.
        # Use temperature as the primary sampling parameter; only fall back to top_p
        # if the caller explicitly omits temperature (leaves it at default 1.0 sentinel).
        request_params = {
            "model": model,
            "messages": claude_messages,
            "max_tokens": self.max_output_tokens,  # Use configured max output tokens
            "stream": stream
        }
        # Only send temperature if this model is not known to reject it.
        if model not in _MODELS_REJECTING_TEMPERATURE:
            request_params["temperature"] = temperature

        if system_message:
            request_params["system"] = system_message

        # Add any additional parameters
        request_params.update(kwargs)

        try:
            response = self.client.messages.create(**request_params)
        except anthropic.BadRequestError as e:
            # Some newer models (e.g. claude-opus-4-8) reject `temperature`
            # entirely ("temperature is deprecated for this model"). Remember
            # that, drop it, and retry rather than failing the request.
            if "temperature" in str(e).lower() and "temperature" in request_params:
                _MODELS_REJECTING_TEMPERATURE.add(model)
                del request_params["temperature"]
                response = self.client.messages.create(**request_params)
            else:
                raise
        except anthropic.APIStatusError as e:
            # 529 "Overloaded" is transient — surface it as a retryable
            # overload so the engine waits and retries instead of failing.
            if getattr(e, "status_code", None) == 529 or "overload" in str(e).lower():
                raise OverloadedError(str(e)[:300])
            raise
        
        if stream:
            return StreamingResponse(response)
        else:
            raw_content = response.content[0].text if response.content else ""
            if json_mode:
                raw_content = self._strip_markdown_fences(raw_content)

            # Convert to OpenAI-compatible format
            return {
                "choices": [
                    {
                        "message": {
                            "content": raw_content,
                            "role": "assistant"
                        },
                        "finish_reason": "stop" if response.stop_reason == "end_turn" else response.stop_reason
                    }
                ],
                "usage": {
                    "prompt_tokens": getattr(response.usage, 'input_tokens', 0) if response.usage else 0,
                    "completion_tokens": getattr(response.usage, 'output_tokens', 0) if response.usage else 0,
                    "total_tokens": (
                        getattr(response.usage, 'input_tokens', 0) + 
                        getattr(response.usage, 'output_tokens', 0)
                    ) if response.usage else 0
                },
                "model": response.model if hasattr(response, 'model') else model
            }
    
    def get_response_content(self, response: Dict[str, Any]) -> str:
        """Extract content from completed response."""
        return response["choices"][0]["message"]["content"]
    
    def get_streaming_content(self, chunk: Any) -> Optional[str]:
        """Extract content from streaming chunk."""
        if hasattr(chunk, 'type'):
            if chunk.type == 'content_block_delta':
                if hasattr(chunk, 'delta') and hasattr(chunk.delta, 'text'):
                    return chunk.delta.text
            elif chunk.type == 'content_block_start':
                if hasattr(chunk, 'content_block') and hasattr(chunk.content_block, 'text'):
                    return chunk.content_block.text
        return None
    
    def is_stream_complete(self, chunk: Any) -> bool:
        """Check if streaming is complete."""
        return hasattr(chunk, 'type') and chunk.type == 'message_stop'
    
    @property
    def provider_name(self) -> str:
        """Return provider name."""
        return "Anthropic Claude"
    
    @property
    def supported_features(self) -> List[str]:
        """Return supported features."""
        return [
            "streaming",
            "system_messages",
            "temperature_control",
            "top_p_control", 
            "max_tokens",
            "json_mode_via_prompt"  # JSON mode via prompt instructions
        ]
    
    # _strip_markdown_fences and validate_json_response are inherited from ModelProvider