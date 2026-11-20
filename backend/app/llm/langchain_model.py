"""LangChain chat model for create_agent-based agents (Agent 4).

Separate from app.llm.factory on purpose: the structured-output agents keep using the
provider-neutral ChatModel contract; agent loops with tools need a LangChain BaseChatModel.
The provider/model/keys come from the same settings, so one env switches both.
"""
from __future__ import annotations

from app.config import settings
from app.llm.errors import LLMConfigurationError


def get_langchain_model(model_name: str | None = None):
    provider = (settings.llm_provider or "").lower()
    model_id = model_name or settings.model_llm
    timeout = settings.llm_timeout_seconds

    if provider == "groq":
        if not settings.groq_api_key:
            raise LLMConfigurationError("GROQ_API_KEY is not set")
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=model_id, api_key=settings.groq_api_key, base_url=settings.llm_base_url,
                          temperature=0, timeout=timeout, max_retries=1)
    if provider == "anthropic":
        if not settings.anthropic_api_key:
            raise LLMConfigurationError("ANTHROPIC_API_KEY is not set")
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=model_id, api_key=settings.anthropic_api_key, temperature=0,
                             timeout=timeout, max_retries=1)
    if provider == "ollama":
        from langchain_openai import ChatOpenAI

        base = (settings.llm_base_url or "http://localhost:11434").rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        return ChatOpenAI(model=model_id, api_key="ollama", base_url=f"{base}/v1", temperature=0, timeout=timeout)
    raise LLMConfigurationError(f"Unsupported LLM provider: '{provider}'")
