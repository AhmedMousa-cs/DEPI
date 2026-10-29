from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class LLMUsage:
    input_tokens: int
    output_tokens: int
    total_tokens: int
    estimated_cost_usd: float


@dataclass(frozen=True)
class ModelResponse:
    content: str
    usage: LLMUsage
    model: str


@dataclass(frozen=True)
class StructuredResult:
    value: Any
    raw_text: str
    usage: LLMUsage
    model: str
    attempts: int


@runtime_checkable
class ChatModel(Protocol):
    def invoke(
        self,
        *,
        messages: list[dict[str, str]],
        response_schema: dict | None = None,
        max_tokens: int = 2000,
    ) -> ModelResponse:
        ...
