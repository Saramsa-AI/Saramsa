from openai import AzureOpenAI, AsyncAzureOpenAI
from threading import Lock
import os
import logging
from pathlib import Path
from dotenv import load_dotenv

# Load backend/.env so vars are found when this module is imported (e.g. in Celery) regardless of cwd
_backend_dir = Path(__file__).resolve().parent.parent.parent
load_dotenv(_backend_dir / ".env")

logger = logging.getLogger("apis.app")

# Single source of truth: env keys (must be set in .env or environment).
# Required: AZURE_ENDPOINT_URL, AZURE_DEPLOYMENT_NAME, AZURE_API_KEY, AZURE_API_VERSION
_DEFAULT_DEPLOYMENT_NAME = "gpt-5-mini"

_ENV_KEYS = {
    "ENDPOINT_URL": "AZURE_ENDPOINT_URL",
    "DEPLOYMENT_NAME": "AZURE_DEPLOYMENT_NAME",
    "API_KEY": "AZURE_API_KEY",
    "API_VERSION": "AZURE_API_VERSION",
}

AZURE_OPENAI = {k: os.getenv(env_key) for k, env_key in _ENV_KEYS.items()}


def get_azure_deployment_name() -> str:
    """Return the deployment name to use as 'model' in Azure OpenAI API (single source of truth)."""
    return os.getenv('AZURE_DEPLOYMENT_NAME') or _DEFAULT_DEPLOYMENT_NAME

class AzureOpenAIClient:
    """
    Thread-safe singleton for Azure OpenAI client with connection validation.
    """
    _instance = None
    _lock = Lock()  # Thread-safe initialization
    
    def __init__(self):
        pass

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            with cls._lock:
                if not cls._instance:
                    inst = super().__new__(cls, *args, **kwargs)
                    try:
                        inst._initialize()
                    except Exception:
                        # Do not cache a failed instance so the next call can retry (e.g. after fixing .env)
                        raise
                    cls._instance = inst
        return cls._instance

    def _initialize(self):
        """
        Initialize the Azure OpenAI client instance with validation.
        """
        # Validate configuration (re-read env so Celery/workers see updated .env after restart)
        config = {k: os.getenv(env_key) for k, env_key in _ENV_KEYS.items()}
        missing = [env_key for k, env_key in _ENV_KEYS.items() if not config.get(k)]
        if missing:
            msg = (
                f"Missing Azure OpenAI configuration. Set these env vars in backend/.env: {', '.join(missing)}. "
                "Then restart Django and Celery."
            )
            logger.error("Azure OpenAI: %s", msg)
            raise ValueError(msg)

        try:
            # Default timeout so callers that don't set with_options(timeout=...)
            # (e.g. test_connection) can't hang on the SDK's ~600s default.
            self.client = AzureOpenAI(
                azure_endpoint=config["ENDPOINT_URL"],
                api_key=config["API_KEY"],
                api_version=config["API_VERSION"],
                timeout=60.0,
            )
            self.async_client = AsyncAzureOpenAI(
                azure_endpoint=config["ENDPOINT_URL"],
                api_key=config["API_KEY"],
                api_version=config["API_VERSION"],
                timeout=60.0,
            )
            logger.info("Azure OpenAI client initialized successfully (sync + async)")
        except Exception as e:
            logger.error("Failed to initialize Azure OpenAI client: %s", e)
            raise ConnectionError(f"Failed to initialize Azure OpenAI client: {e}")

    def get_client(self):
        """
        Get the initialized sync Azure OpenAI client instance.
        """
        if not hasattr(self, 'client') or self.client is None:
            raise ConnectionError("Azure OpenAI client is not initialized")
        return self.client

    def get_async_client(self):
        """
        Get the initialized async Azure OpenAI client instance.
        """
        if not hasattr(self, 'async_client') or self.async_client is None:
            raise ConnectionError("Azure OpenAI async client is not initialized")
        return self.async_client
    
    def test_connection(self) -> bool:
        """
        Test the connection to Azure OpenAI service.
        
        Returns:
            bool: True if connection is successful
            
        Raises:
            ConnectionError: If connection test fails
        """
        try:
            client = self.get_client()
            # Simple test call to verify connection
            response = client.chat.completions.create(
                model=get_azure_deployment_name(),
                messages=[{"role": "user", "content": "test connection to Azure OpenAI"}],
                max_completion_tokens=16
            )
            logger.info("Azure OpenAI connection test successful")
            return True
        except Exception as e:
            logger.error(f"Azure OpenAI connection test failed: {e}")
            raise ConnectionError(f"Azure OpenAI connection test failed: {e}")


# Global instance for easy access
_azure_client = None

def get_azure_client() -> AzureOpenAIClient:
    """Get the global Azure OpenAI client instance."""
    global _azure_client
    if _azure_client is None:
        _azure_client = AzureOpenAIClient()
    return _azure_client