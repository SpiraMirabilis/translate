from dotenv import load_dotenv
import json
import os
from providers import create_provider, get_factory

class TranslationConfig:
    """Configuration class for translation settings"""
    
    def __init__(self):
        load_dotenv()
        
        # API credentials
        self.deepseek_key = os.getenv("DEEPSEEK_KEY")
        self.openai_key = os.getenv("OPENAI_KEY")
        
        # Model settings - now stored with provider prefix
        self.translation_model = os.getenv("TRANSLATION_MODEL", "oai:o3-mini")
        self.advice_model = os.getenv("ADVICE_MODEL", "oai:o3-mini")
        
        # Debug mode
        self.debug_mode = os.getenv("DEBUG") == "True"
        
        # Paths
        self.script_dir = os.path.dirname(os.path.abspath(__file__)) + "/"
        
        # Translation settings (now per-provider via models.json)
        # Legacy fallback for MAX_CHARS env var if needed
        self._fallback_max_chars = int(os.getenv("MAX_CHARS", "5000"))

    def get_client(self, model_spec=None):
        """
        Return an appropriate provider based on model specification.
        
        Args:
            model_spec: String in format "provider:model" or just "model"
                        If not provided, uses translation_model
        
        Returns:
            tuple: (provider, model_name)
        """
        if model_spec is None:
            model_spec = self.translation_model
        
        # Parse provider and model
        provider, model_name = self.parse_model_spec(model_spec)
        
        # Create provider using factory
        try:
            provider_instance = create_provider(provider)
            return provider_instance, model_name
        except (ValueError, RuntimeError) as e:
            # Fallback error message with more context
            raise ValueError(f"Failed to create provider '{provider}' for model '{model_name}': {e}")
        
    def get_provider(self, model_spec=None):
        """
        Get provider instance for the specified model.
        
        Args:
            model_spec: String in format "provider:model" or just "model"
        
        Returns:
            ModelProvider instance
        """
        provider, _ = self.get_client(model_spec)
        return provider
    
    def parse_model_spec(self, model_spec):
        """
        Parse a model specification string.
        
        Args:
            model_spec: String in format "provider:model" or just "model"
        
        Returns:
            tuple: (provider, model_name)
        """
        if ":" in model_spec:
            provider, model_name = model_spec.split(":", 1)
        else:
            # Default to OpenAI if no provider specified
            provider = "oai"
            model_name = model_spec
            
        return provider.lower(), model_name
    
    def get_supported_providers(self):
        """Get list of supported providers."""
        return get_factory().get_supported_providers()
    
    def get_default_model(self, provider_name):
        """Get default model for a provider."""
        return get_factory().get_default_model(provider_name)
    
    def get_max_chars(self, model_spec=None):
        """
        Get the maximum character count for input chunks for the specified model.
        
        Args:
            model_spec: String in format "provider:model" or just "model"
                        If not provided, uses translation_model
        
        Returns:
            Maximum characters per chunk for the provider
        """
        try:
            provider = self.get_provider(model_spec)
            return provider.max_chars
        except (ValueError, RuntimeError):
            # Fallback to legacy MAX_CHARS environment variable
            return self._fallback_max_chars

