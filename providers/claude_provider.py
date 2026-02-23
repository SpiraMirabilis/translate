"""
Claude provider implementation using the Anthropic API.

This provider supports Claude models (Claude 3.5 Sonnet, Claude 3 Opus, etc.)
through the official Anthropic API.
"""
from typing import Dict, List, Optional, Any, Union
import json
from .base import ModelProvider, StreamingResponse

try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False


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

        self.client = anthropic.Anthropic(api_key=api_key)
    
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
            "temperature": temperature,
            "stream": stream
        }
        
        if system_message:
            request_params["system"] = system_message
        
        # Add any additional parameters
        request_params.update(kwargs)
        
        response = self.client.messages.create(**request_params)
        
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
    
    @staticmethod
    def _strip_markdown_fences(content: str) -> str:
        """Remove markdown code fences (e.g. ```json ... ```) from a response."""
        content = content.strip()
        if content.startswith("```"):
            # Drop the opening fence line
            content = content[content.index("\n") + 1:] if "\n" in content else content[3:]
            # Drop the closing fence
            if content.endswith("```"):
                content = content[:-3]
        return content.strip()

    def validate_json_response(self, content: str) -> Dict[str, Any]:
        """
        Validate and parse JSON response from Claude.
        
        Claude sometimes includes extra text, so we try to extract just the JSON.
        """
        # Try to parse as-is first
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass
        
        # Try to find JSON within the response
        content = content.strip()
        
        # Look for JSON object boundaries
        start_idx = content.find('{')
        if start_idx != -1:
            # Find the matching closing brace
            brace_count = 0
            end_idx = start_idx
            
            for i, char in enumerate(content[start_idx:], start_idx):
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        end_idx = i + 1
                        break
            
            # Try to parse the extracted JSON
            try:
                json_str = content[start_idx:end_idx]
                return json.loads(json_str)
            except json.JSONDecodeError:
                pass
        
        # If all else fails, raise the original error
        raise json.JSONDecodeError(
            f"Failed to parse JSON response from Claude: {content[:100]}...",
            content,
            0
        )