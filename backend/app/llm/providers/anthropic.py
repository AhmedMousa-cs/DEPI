import os
from app.config import settings
from app.llm.contracts import LLMUsage, ModelResponse
from app.llm.errors import LLMConfigurationError, LLMProviderError


class AnthropicProvider:
    def __init__(self, model: str | None = None, api_key: str | None = None):
        self.model = model or getattr(settings, "model_llm", "claude-3-5-sonnet-20241022")
        self.api_key = api_key or getattr(
            settings, "anthropic_api_key", os.getenv("ANTHROPIC_API_KEY", "")
        )
        if not self.api_key or self.api_key == "dummy-key-for-tests":
            raise LLMConfigurationError("Missing or invalid ANTHROPIC_API_KEY")

    def _calculate_cost(self, input_tokens: int, output_tokens: int) -> float:
        input_cost = (input_tokens / 1000.0) * 0.003
        output_cost = (output_tokens / 1000.0) * 0.015
        return round(input_cost + output_cost, 6)

    def invoke(
        self,
        *,
        messages: list[dict[str, str]],
        response_schema: dict | None = None,
        max_tokens: int = 2000,
    ) -> ModelResponse:
        try:
            import anthropic
        except ImportError as e:
            raise LLMConfigurationError("anthropic package is not installed.") from e

        client = anthropic.Anthropic(api_key=self.api_key)
        system_prompt = ""
        formatted_messages = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                system_prompt = content
            else:
                formatted_messages.append({"role": role, "content": content})

        if response_schema:
            schema_instruction = (
                f"\n\nYou MUST respond strictly in valid JSON conforming to this schema:\n"
                f"{response_schema}\nDo not include markdown code block syntax or extra text."
            )
            if formatted_messages:
                formatted_messages[-1]["content"] += schema_instruction

        try:
            kwargs = {
                "model": self.model,
                "max_tokens": max_tokens,
                "messages": formatted_messages,
            }
            if system_prompt:
                kwargs["system"] = system_prompt

            response = client.messages.create(**kwargs)
            content_text = "".join([block.text for block in response.content if hasattr(block, "text")])

            in_tokens = response.usage.input_tokens
            out_tokens = response.usage.output_tokens
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
            raise LLMProviderError(f"Anthropic API error: {exc}") from exc
