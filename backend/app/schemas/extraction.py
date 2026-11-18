from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ReqPriority = Literal["high", "medium", "low"]


class ExtractedRequirement(BaseModel):
    """What the agent may output: a draft. It has no status/approval field by design."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    type: Literal["functional", "non_functional", "feature", "decision"]
    text: str = Field(min_length=3, max_length=1000)
    source_id: int = Field(gt=0)
    exact_quote: str = Field(min_length=5, max_length=1000)
    confidence: float = Field(ge=0, le=1)
    # M4.1: grouping + priority suggestion + one clarify question for implied/vague items
    module: str | None = Field(default=None, min_length=2, max_length=60)
    priority: ReqPriority | None = None
    clarify_question: str | None = Field(default=None, min_length=10, max_length=300)

    @model_validator(mode="after")
    def _rules(self):
        if self.module is not None:
            self.module = " ".join(self.module.split())
            if not self.module:
                self.module = None
        if self.type == "decision" and self.priority is not None:
            # a conflict/open question has no execution priority
            self.priority = None
        return self


class ExtractionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirements: list[ExtractedRequirement] = Field(max_length=60)


# ------------------------------------------------------------- coverage gaps (M4.1, LLM-judged)
class GapAssessment(BaseModel):
    """One area verdict. matched_ids MUST come from the payload; unknown ones are dropped by code."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    area: str = Field(min_length=2, max_length=120)
    covered: bool
    matched_ids: list[int] = Field(default_factory=list, max_length=60)
    note: str = Field(default="", max_length=200)


class GapAnalysisOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assessments: list[GapAssessment] = Field(min_length=1, max_length=30)
