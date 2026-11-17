"""M7 PM assistant chat: the PM talks to a portfolio-aware assistant in the dashboard.

The client sends the recent conversation (stateless backend), the service attaches a
deterministic portfolio snapshot as read-only DATA, and the LLM answers with a reply
plus concrete suggested actions. Everything goes through run_structured (budget,
agent_runs, audit, Langfuse); suggestions are sanitised by code — the model can only
point at projects the user can actually see and only at actions the UI can perform.
"""
from pydantic import BaseModel, ConfigDict, Field, field_validator


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str
    content: str = Field(min_length=1, max_length=4000)

    @field_validator("role")
    @classmethod
    def _known_role(cls, v: str) -> str:
        if v not in ("user", "assistant"):
            raise ValueError("role must be 'user' or 'assistant'")
        return v


class PortfolioChatInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: list[ChatMessage] = Field(min_length=1, max_length=30)


class ChatSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=120)
    action: str = Field(min_length=1, max_length=40)
    project_id: int | None = None


class PortfolioChatOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reply: str = Field(min_length=1, max_length=4000)
    suggestions: list[ChatSuggestion] = Field(default_factory=list, max_length=5)
