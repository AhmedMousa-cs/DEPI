from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ExtractedRequirement(BaseModel):
    """What the agent may output: a draft. It has no status/approval field by design."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    type: Literal["functional", "non_functional", "feature", "decision"]
    text: str = Field(min_length=3, max_length=1000)
    source_id: int = Field(gt=0)
    exact_quote: str = Field(min_length=5, max_length=1000)
    confidence: float = Field(ge=0, le=1)


class ExtractionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirements: list[ExtractedRequirement] = Field(max_length=60)
