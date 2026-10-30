import json
import os
from app.config import settings
from app.llm.contracts import LLMUsage, ModelResponse
from app.llm.errors import LLMConfigurationError, LLMProviderError


class GroqProvider:
    def __init__(self, model: str | None = None, api_key: str | None = None):
        self.model = model or getattr(
            settings, "model_llm", os.getenv("MODEL_LLM", "llama-3.3-70b-versatile")
        )
        self.api_key = api_key or getattr(
            settings, "groq_api_key", os.getenv("GROQ_API_KEY", "")
        )
        if not self.api_key or self.api_key == "dummy-key-for-tests":
            raise LLMConfigurationError("Missing or invalid GROQ_API_KEY")

    def _calculate_cost(self, input_tokens: int, output_tokens: int) -> float:
        input_cost = (input_tokens / 1_000_000.0) * 0.59
        output_cost = (output_tokens / 1_000_000.0) * 0.79
        return round(input_cost + output_cost, 6)

    def invoke(
        self,
        *,
        messages: list[dict[str, str]],
        response_schema: dict | None = None,
        max_tokens: int = 2000,
    ) -> ModelResponse:
        try:
            from groq import Groq
        except ImportError as e:
            raise LLMConfigurationError("groq package is not installed.") from e

        client = Groq(api_key=self.api_key)
        formatted_messages = list(messages)

        if response_schema:
            schema_instruction = (
                f"\n\nYou MUST respond strictly in valid JSON conforming to this schema:\n"
                f"{json.dumps(response_schema, indent=2)}\nDo not include markdown code block syntax or extra text."
            )
            if formatted_messages:
                formatted_messages[-1] = {
                    "role": formatted_messages[-1]["role"],
                    "content": formatted_messages[-1]["content"] + schema_instruction,
                }

        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=formatted_messages,
                max_tokens=max_tokens,
                temperature=0.0,
                response_format={"type": "json_object"} if response_schema else None,
            )
            content_text = response.choices[0].message.content or ""

            in_tokens = response.usage.prompt_tokens if response.usage else 0
            out_tokens = response.usage.completion_tokens if response.usage else 0
            cost = self._calculate_cost(in_tokens, out_tokens)

            return ModelResponse(
                content=content_text,
                usage=LLMUsage(
                    input_tokens=in_tokens,
                    output_tokens=out_tokens,
                    total_tokens=in_tokens + out_tokens,
                    estimated_cost_usd=cost,
                ),
                model=self.model,
            )
        except Exception as exc:
            raise LLMProviderError(f"Groq API error: {exc}") from exc
