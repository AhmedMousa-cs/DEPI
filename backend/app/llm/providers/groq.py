import json
import os

from app.config import settings
from app.llm.contracts import LLMUsage, ModelResponse
from app.llm.errors import LLMConfigurationError, LLMProviderError


class GroqProvider:
    def __init__(self, model: str | None = None, api_key: str | None = None):
        self.model = model or getattr(settings, "model_llm", os.getenv("MODEL_LLM", ""))
        self.api_key = api_key or getattr(settings, "groq_api_key", os.getenv("GROQ_API_KEY", ""))
        if not self.api_key or self.api_key == "dummy-key-for-tests":
            raise LLMConfigurationError("Missing or invalid GROQ_API_KEY")

    def invoke(self, *, messages: list[dict[str, str]], response_schema: dict | None = None,
               max_tokens: int = 2000) -> ModelResponse:
        try:
            from groq import Groq
        except ImportError as e:
            raise LLMConfigurationError("groq package is not installed.") from e

        client = Groq(api_key=self.api_key)
        formatted = list(messages)
        if response_schema and formatted:
            instruction = (
                "\n\nYou MUST respond strictly in valid JSON conforming to this schema:\n"
                f"{json.dumps(response_schema, indent=2)}\n"
                "Do not include markdown code block syntax or extra text."
            )
            formatted[-1] = {"role": formatted[-1]["role"], "content": formatted[-1]["content"] + instruction}

        kwargs: dict = {"model": self.model, "messages": formatted, "max_tokens": max_tokens, "temperature": 0.0}
        if response_schema:
            kwargs["response_format"] = {"type": "json_object"}
        if "gpt-oss" in self.model:
            kwargs["reasoning_effort"] = "low"  # reasoning tokens otherwise eat the output budget; remove if Groq rejects it

        try:
            response = client.chat.completions.create(**kwargs)
            content = response.choices[0].message.content or ""
            tin = response.usage.prompt_tokens if response.usage else 0
            tout = response.usage.completion_tokens if response.usage else 0
            # cost is computed in app/llm/pricing.py from LLM_PRICING_JSON, never here
            return ModelResponse(
                content=content,
                usage=LLMUsage(input_tokens=tin, output_tokens=tout, total_tokens=tin + tout, estimated_cost_usd=0.0),
                model=self.model,
            )
        except Exception as exc:
            raise LLMProviderError(f"Groq API error: {exc}") from exc
