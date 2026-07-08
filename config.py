from dotenv import load_dotenv
import json
import os
from providers import create_provider, get_factory

class TranslationConfig:
    """Configuration class for translation settings"""
    
    def __init__(self):
        load_dotenv()
        # Load JSON-backed settings store and mirror its values into os.environ
        # so the existing os.getenv() reads below pick up persisted user settings.
        # Must run after load_dotenv (so first-run migration can seed from .env)
        # and before any os.getenv() call for a managed key.
        import settings_store
        settings_store.load()

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
        
        # WordPress / Fictioneer publishing
        self.wp_url = os.getenv("WP_URL", "")
        self.wp_username = os.getenv("WP_USERNAME", "")
        self.wp_app_password = os.getenv("WP_APP_PASSWORD", "")

        # Site branding
        self.site_name = os.getenv("SITE_NAME", "T9")
        self.public_site_name = os.getenv("PUBLIC_SITE_NAME", "Boonnovels")

        # Database backend: "sqlite" (default) or "mysql"
        self.db_backend = os.getenv("DB_BACKEND", "sqlite")
        self.mysql_host = os.getenv("MYSQL_HOST", "localhost")
        self.mysql_user = os.getenv("MYSQL_USER", "")
        self.mysql_pass = os.getenv("MYSQL_PASS", "")
        self.mysql_db = os.getenv("MYSQL_DB", "t9")
        self.mysql_port = int(os.getenv("MYSQL_PORT", "3306"))

        # Cloudflare (Turnstile + user-firewall API for comment IP bans)
        self.cf_turnstile_site_key = os.getenv("CF_TURNSTILE_SITE_KEY", "")
        self.cf_turnstile_secret_key = os.getenv("CF_TURNSTILE_SECRET_KEY", "")
        self.cf_api_email = os.getenv("CF_API_EMAIL", "")
        self.cf_api_key = os.getenv("CF_API_KEY", "")

        # Comment auto-moderation
        self.comment_automod_enabled = os.getenv("COMMENT_AUTOMOD_ENABLED", "0").lower() in ("1", "true", "yes")
        self.comment_automod_model = os.getenv("COMMENT_AUTOMOD_MODEL", "claude:claude-haiku-4-5")

        # Pronoun repair model — used by pronoun_repair.py to fix wrong-gender pronouns
        self.pronoun_repair_model = os.getenv("PRONOUN_REPAIR_MODEL", "claude:claude-haiku-4-5")

        # Grammar/spell check (local LanguageTool server) + LLM polish pass
        self.grammar_check_enabled = os.getenv("GRAMMAR_CHECK_ENABLED", "0").lower() in ("1", "true", "yes")
        self.languagetool_url = os.getenv("LANGUAGETOOL_URL", "http://127.0.0.1:8081")
        self.grammar_language = os.getenv("GRAMMAR_LANGUAGE", "en-US")
        self.polish_model = os.getenv("POLISH_MODEL", "claude:claude-sonnet-4-6")

        # Traditional → Simplified Chinese preprocessing (global default; per-book overrides via books.trad_to_simp)
        self.trad_to_simp = os.getenv("TRAD_TO_SIMP", "0").lower() in ("1", "true", "yes")

        # Reply-notification emails. Backend is EMAIL_BACKEND ("ses" | "postfix",
        # non-secret setting); SES credentials/region are secrets and live in .env.
        self.email_from = os.getenv("EMAIL_FROM", "")        # e.g. editor@boondollars.com
        self.site_base_url = os.getenv("SITE_BASE_URL", "")  # e.g. https://reader.boondollars.com
        self.email_backend = os.getenv("EMAIL_BACKEND", "ses")
        self.ses_region = os.getenv("SES_REGION", "us-east-2")
        self.ses_access_key = os.getenv("SES_ACCESS_KEY_ID", "")
        self.ses_secret_key = os.getenv("SES_SECRET_ACCESS_KEY", "")

        # DigitalOcean Spaces (S3-compatible) CDN offload for covers/illustrations/EPUBs.
        # Secrets live in .env; the rest are non-secret settings (settings_store).
        self.spaces_enabled = os.getenv("SPACES_ENABLED", "0").lower() in ("1", "true", "yes")
        self.spaces_endpoint = os.getenv("BUCKET_ENDPOINT", "")     # e.g. https://nyc3.digitaloceanspaces.com
        self.spaces_access_key = os.getenv("BUCKET_ACCESS_ID", "")
        self.spaces_secret_key = os.getenv("BUCKET_SECRET", "")
        self.spaces_bucket = os.getenv("SPACES_BUCKET", "spiraspira")
        self.spaces_region = os.getenv("SPACES_REGION", "nyc3")
        self.spaces_prefix = os.getenv("SPACES_PREFIX", "t9")
        self.spaces_cdn_base = os.getenv("SPACES_CDN_BASE", "https://spiraspira.nyc3.cdn.digitaloceanspaces.com")

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

