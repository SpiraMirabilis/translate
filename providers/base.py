"""
Abstract base class for model providers in the translation system.
"""
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any, Iterator, Union
import json
import re


class OverloadedError(Exception):
    """Raised when a provider reports a transient 529 "Overloaded" condition.

    This is distinct from a parse failure or a hard connection error: the
    service is temporarily saturated and the request should be retried after a
    wait rather than counted as a real failure. The translation engine catches
    this specifically and sleeps a configurable interval before retrying.
    """


class SessionLimitError(Exception):
    """Raised when the Claude Code CLI reports the user's session usage limit
    is exhausted, e.g. "You've hit your session limit · resets 10:40pm (UTC)".

    Unlike OverloadedError (a transient saturation retried after a fixed
    interval), this carries a concrete reset time. The translation engine
    waits until just past that time before resuming, effectively pausing the
    queue rather than burning the per-chunk retry budget. The original notice
    text is preserved so the engine can parse the reset clock time from it.
    """

    def __init__(self, message, reset_text: Optional[str] = None):
        super().__init__(message)
        # Text containing the "resets <time>" clause to parse. Defaults to the
        # message itself when a separate copy isn't supplied.
        self.reset_text = reset_text if reset_text is not None else str(message)


# The Claude Code CLI prints the usage-limit notice as plain text, e.g.
# "You've hit your session limit · resets 10:40pm (UTC)". Key on the stable
# "session limit" phrase rather than the (reworded-over-time) tail.
_SESSION_LIMIT_RE = re.compile(r"hit\s+your\s+session\s+limit", re.IGNORECASE)

# Pulls the reset clock time out of the notice. Handles 12-hour ("10:40pm",
# "3 pm") and bare 24-hour ("22:40") forms, with an optional parenthesised
# timezone such as "(UTC)".
_SESSION_RESET_RE = re.compile(
    r"resets?\s+(\d{1,2})(?::(\d{2}))?\s*([ap]m)?\s*(?:\(\s*([A-Za-z/]+)\s*\))?",
    re.IGNORECASE,
)


def looks_session_limited(text) -> bool:
    """Return True if `text` looks like a Claude Code session-limit notice."""
    if not text:
        return False
    return bool(_SESSION_LIMIT_RE.search(str(text)))


def parse_session_reset_seconds(text, now: Optional[datetime] = None,
                                grace_seconds: int = 60) -> Optional[int]:
    """Seconds to wait until `grace_seconds` past the reset time named in a
    session-limit notice, or None if no reset time can be parsed.

    The reset clause states a clock time (and usually a timezone). We resolve
    it to the next occurrence of that time — today if it's still ahead,
    otherwise tomorrow — and add the grace period (default 60s, per the
    requirement to resume "1 minute past the time specified").

    Timezone handling: an explicit "(UTC)"/"(GMT)" is honoured; anything else
    (a named zone we can't resolve here, or no zone at all) is treated as the
    server's local time, which matches how the CLI renders it for interactive
    users.
    """
    if not text:
        return None
    m = _SESSION_RESET_RE.search(str(text))
    if not m:
        return None

    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    ampm = (m.group(3) or "").lower()
    tzname = (m.group(4) or "").upper()

    if ampm == "pm" and hour != 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None

    if tzname in ("UTC", "GMT", "Z"):
        cur = (now.astimezone(timezone.utc)
               if (now is not None and now.tzinfo is not None)
               else datetime.now(timezone.utc))
    else:
        cur = now if now is not None else datetime.now()

    target = cur.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= cur:
        target += timedelta(days=1)

    wait = (target - cur).total_seconds() + grace_seconds
    return int(max(grace_seconds, wait))


# Matches the various shapes a 529 surfaces as: the Claude Code CLI prints
# "API Error: 529 Overloaded"; the Anthropic SDK raises with a body containing
# {"type":"overloaded_error"} and status 529. We require the "529"/"error"
# context so a translation that merely contains the word "overloaded" in prose
# doesn't trip the detector.
_OVERLOAD_RE = re.compile(
    r"(?:api[\s_]*error.*529|529.*overload|overload.*529|overloaded_error)",
    re.IGNORECASE | re.DOTALL,
)


def looks_overloaded(text, strict=True) -> bool:
    """Return True if `text` looks like a 529 "Overloaded" notice.

    strict=True (default) is for sniffing model *output*: it refuses to match
    inside a large or JSON-shaped payload, since a real translation could
    legitimately mention "overloaded" in prose. strict=False is for known
    error contexts (subprocess stderr, exception strings) where no such guard
    is needed.
    """
    if not text:
        return False
    s = str(text).strip()
    # The Claude Code CLI prints a 529 as a line beginning with
    # "API Error: 529" (followed by varying help text, e.g. "...Overloaded.
    # This is a server-side issue ... check https://status.claude.com."). Key
    # on the prefix so we stay robust if Anthropic rewords the tail.
    if s[:14].lower() == "api error: 529":
        return True
    if strict:
        if len(s) > 300 or s.startswith("{") or s.startswith("["):
            return False
    return bool(_OVERLOAD_RE.search(s))


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
        Validate and parse JSON response content.
        Strips markdown fences and attempts to extract JSON from surrounding text.

        Args:
            content: The response content string

        Returns:
            Parsed JSON as a dictionary

        Raises:
            json.JSONDecodeError: If the content is not valid JSON
        """
        # Try as-is first
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass

        # Strip markdown fences and retry
        stripped = self._strip_markdown_fences(content)
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass

        # Try to find a JSON object within the response
        start_idx = stripped.find('{')
        if start_idx != -1:
            brace_count = 0
            end_idx = start_idx
            for i, char in enumerate(stripped[start_idx:], start_idx):
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        end_idx = i + 1
                        break
            try:
                return json.loads(stripped[start_idx:end_idx])
            except json.JSONDecodeError:
                pass

        raise json.JSONDecodeError(
            f"Failed to parse JSON response from {self.__class__.__name__}: {content[:100]}...",
            content,
            0
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