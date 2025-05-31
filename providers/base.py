"""
Abstract base class for model providers in the translation system.
"""
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any, Iterator, Union
import json


class StreamingResponse:
    """Wrapper for streaming responses to standardize interface across providers"""
    
    def __init__(self, response_iterator):
        self.response_iterator = response_iterator
    
    def __iter__(self):
        return self
    
    def __next__(self):
        return next(self.response_iterator)


class ModelProvider(ABC):
    """
    Abstract base class for all model providers.
    
    This defines the interface that all LLM providers must implement
    to work with the translation engine.
    """
    
    def __init__(self, api_key: str, base_url: Optional[str] = None, **kwargs):
        """
        Initialize the provider with credentials and configuration.
        
        Args:
            api_key: API key for the service
            base_url: Optional custom base URL (useful for OpenAI-compatible APIs)
            **kwargs: Additional provider-specific configuration
        """
        self.api_key = api_key
        self.base_url = base_url
        self.config = kwargs
    
    @abstractmethod
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
        Perform a chat completion request.
        
        Args:
            messages: List of message dictionaries with 'role' and 'content'
            model: Model name to use
            temperature: Sampling temperature
            top_p: Nucleus sampling parameter
            max_tokens: Maximum tokens to generate
            response_format: Optional response format specification
            stream: Whether to stream the response
            **kwargs: Additional provider-specific parameters
            
        Returns:
            Either a complete response dict or a StreamingResponse iterator
        """
        pass
    
    @abstractmethod
    def get_response_content(self, response: Dict[str, Any]) -> str:
        """
        Extract the text content from a completed response.
        
        Args:
            response: The response dictionary from chat_completion
            
        Returns:
            The text content of the response
        """
        pass
    
    @abstractmethod
    def get_streaming_content(self, chunk: Any) -> Optional[str]:
        """
        Extract content from a streaming response chunk.
        
        Args:
            chunk: A single chunk from the streaming response
            
        Returns:
            The text content of the chunk, or None if no content
        """
        pass
    
    @abstractmethod
    def is_stream_complete(self, chunk: Any) -> bool:
        """
        Check if a streaming chunk indicates the stream is complete.
        
        Args:
            chunk: A single chunk from the streaming response
            
        Returns:
            True if the stream is complete
        """
        pass
    
    def validate_json_response(self, content: str) -> Dict[str, Any]:
        """
        Validate and parse JSON response content.
        
        Args:
            content: The response content string
            
        Returns:
            Parsed JSON as a dictionary
            
        Raises:
            json.JSONDecodeError: If the content is not valid JSON
        """
        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            raise json.JSONDecodeError(
                f"Failed to parse JSON response from {self.__class__.__name__}: {str(e)}",
                content,
                e.pos
            )
    
    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the name of this provider for logging/identification."""
        pass
    
    @property
    @abstractmethod
    def supported_features(self) -> List[str]:
        """
        Return list of supported features.
        
        Common features might include:
        - 'streaming': Supports streaming responses
        - 'json_mode': Supports structured JSON output
        - 'system_messages': Supports system role messages
        - 'function_calling': Supports function/tool calling
        """
        pass
    
    @property
    def max_chars(self) -> int:
        """
        Return the maximum character count for input chunks for this provider.
        
        Returns:
            Maximum characters per chunk, defaults to 5000 if not configured
        """
        return self.config.get('max_chars', 5000)