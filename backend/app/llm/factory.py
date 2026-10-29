import os
from app.config import settings
from app.llm.contracts import ChatModel
from app.llm.errors import LLMConfigurationError
from app.llm.providers.anthropic import AnthropicProvider
from app.llm.providers.groq import GroqProvider


def get_llm_model(
    model_name: str | None = None,
    api_key: str | None = None,
) -> ChatModel:
    provider = getattr(
        settings, "llm_provider", os.getenv("LLM_PROVIDER", "groq")
    ).lower()

    if provider == "groq":
        model_id = model_name or getattr(
            settings, "model_llm", os.getenv("MODEL_LLM", "llama-3.3-70b-versatile")
        )
        key = api_key or getattr(
            settings, "groq_api_key", os.getenv("GROQ_API_KEY", "")
        )
        return GroqProvider(model=model_id, api_key=key)
    elif provider == "anthropic":
        model_id = model_name or getattr(
            settings, "model_llm", os.getenv("MODEL_LLM", "claude-3-5-sonnet-20241022")
        )
        key = api_key or getattr(
            settings, "anthropic_api_key", os.getenv("ANTHROPIC_API_KEY", "")
        )
        return AnthropicProvider(model=model_id, api_key=key)

    raise LLMConfigurationError(f"Unsupported LLM provider: '{provider}'")
