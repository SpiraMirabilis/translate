from openai import OpenAI
from dotenv import load_dotenv
import json
import os

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
        
        # Translation settings
        self.max_chars = int(os.getenv("MAX_CHARS", "5000"))

    def get_client(self, model_spec=None):
        """
        Return an appropriate API client based on model specification.
        
        Args:
            model_spec: String in format "provider:model" or just "model"
                        If not provided, uses translation_model
        
        Returns:
            tuple: (client, model_name)
        """
        if model_spec is None:
            model_spec = self.translation_model
        
        # Parse provider and model
        if ":" in model_spec:
            provider, model_name = model_spec.split(":", 1)
        else:
            # Default to OpenAI if no provider specified
            provider = "oai"
            model_name = model_spec
        
        # Create appropriate client
        if provider.lower() in ["deepseek", "ds"]:
            if not self.deepseek_key:
                raise ValueError("DeepSeek API key not configured. Set DEEPSEEK_KEY in .env file.")
            client = OpenAI(api_key=self.deepseek_key, base_url="https://api.deepseek.com")
        else:  # Default to OpenAI
            if not self.openai_key:
                raise ValueError("OpenAI API key not configured. Set OPENAI_KEY in .env file.")
            client = OpenAI(api_key=self.openai_key)
        
        return client, model_name
    
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

