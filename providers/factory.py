"""
Provider factory for creating model providers from configuration.
"""
import json
import os
from typing import Dict, Any, Optional
from .base import ModelProvider
from .openai_provider import OpenAIProvider
from .claude_provider import ClaudeProvider
from .gemini_provider import GeminiProvider


class ProviderFactory:
    """Factory for creating model providers based on configuration."""
    
    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize the factory with provider configuration.
        
        Args:
            config_path: Path to models.json config file. If None, uses default location.
        """
        if config_path is None:
            config_path = os.path.join(os.path.dirname(__file__), 'models.json')
        
        self.config_path = config_path
        self._load_config()
    
    def _load_config(self):
        """Load provider configuration from JSON file."""
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                self.config = json.load(f)
        except FileNotFoundError:
            raise FileNotFoundError(f"Provider config file not found: {self.config_path}")
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in provider config: {e}")
        
        # Validate config structure
        if 'providers' not in self.config:
            raise ValueError("Provider config must contain 'providers' section")
    
    def create_provider(self, provider_name: str, api_key: Optional[str] = None, **kwargs) -> ModelProvider:
        """
        Create a provider instance.
        
        Args:
            provider_name: Name of the provider (supports aliases)
            api_key: API key to use. If None, will try to get from environment using api_key_env
            **kwargs: Additional provider-specific configuration
            
        Returns:
            ModelProvider instance
            
        Raises:
            ValueError: If provider is not supported or configuration is invalid
            RuntimeError: If API key cannot be found
        """
        # Resolve aliases
        resolved_name = self._resolve_provider_name(provider_name)
        
        if resolved_name not in self.config['providers']:
            raise ValueError(f"Unsupported provider: {provider_name} (resolved to: {resolved_name})")
        
        provider_config = self.config['providers'][resolved_name]
        
        # Get API key
        if api_key is None:
            api_key_env = provider_config.get('api_key_env')
            if api_key_env:
                api_key = os.getenv(api_key_env)
            
            if not api_key:
                raise RuntimeError(
                    f"API key not provided and not found in environment variable {api_key_env} "
                    f"for provider {provider_name}"
                )
        
        # Get provider class
        class_name = provider_config.get('class')
        if not class_name:
            raise ValueError(f"No class specified for provider {resolved_name}")
        
        provider_class = self._get_provider_class(class_name)
        
        # Prepare initialization arguments
        init_kwargs = {}
        
        # Add base_url if specified
        if 'base_url' in provider_config:
            init_kwargs['base_url'] = provider_config['base_url']
        
        # Add any additional config from the JSON
        for key, value in provider_config.items():
            if key not in ['class', 'api_key_env', 'default_model', 'models']:
                init_kwargs[key] = value
        
        # Override with user-provided kwargs
        init_kwargs.update(kwargs)
        
        # Create and return provider instance
        return provider_class(api_key, **init_kwargs)
    
    def _resolve_provider_name(self, name: str) -> str:
        """Resolve provider name using aliases."""
        aliases = self.config.get('aliases', {})
        return aliases.get(name.lower(), name.lower())
    
    def _get_provider_class(self, class_name: str):
        """Get provider class by name."""
        classes = {
            'OpenAIProvider': OpenAIProvider,
            'ClaudeProvider': ClaudeProvider,
            'GeminiProvider': GeminiProvider,
        }
        
        if class_name not in classes:
            raise ValueError(f"Unknown provider class: {class_name}")
        
        return classes[class_name]
    
    def get_default_model(self, provider_name: str) -> Optional[str]:
        """Get the default model for a provider."""
        resolved_name = self._resolve_provider_name(provider_name)
        if resolved_name in self.config['providers']:
            return self.config['providers'][resolved_name].get('default_model')
        return None
    
    def get_supported_models(self, provider_name: str) -> Optional[list]:
        """Get the list of supported models for a provider."""
        resolved_name = self._resolve_provider_name(provider_name)
        if resolved_name in self.config['providers']:
            return self.config['providers'][resolved_name].get('models')
        return None
    
    def get_supported_providers(self) -> list:
        """Return list of supported provider names (including aliases)."""
        providers = list(self.config['providers'].keys())
        aliases = list(self.config.get('aliases', {}).keys())
        return sorted(providers + aliases)
    
    def reload_config(self):
        """Reload configuration from file."""
        self._load_config()


# Global factory instance
_factory = None

def get_factory() -> ProviderFactory:
    """Get the global provider factory instance."""
    global _factory
    if _factory is None:
        _factory = ProviderFactory()
    return _factory

def create_provider(provider_name: str, api_key: Optional[str] = None, **kwargs) -> ModelProvider:
    """Convenience function to create a provider using the global factory."""
    return get_factory().create_provider(provider_name, api_key, **kwargs)