"""Schemas for the PM review of extracted requirements.

decided_by is NOT accepted from the client: it always comes from the authenticated PM (JWT).
"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ReqType = Literal["functional", "non_functional", "feature", "decision"]
ReqPriority = Literal["high", "medium", "low"]
ReqStatus = Literal["draft", "approved", "rejected"]


class RequirementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    type: str
    text: str
    priority: str | None
    status: str
    source_id: int | None
    quote: str | None
    quote_verified: bool
    confidence: float
    run_id: int | None
    created_by_agent: str | None
    locked_by_pm: bool
    pm_comment: str | None
    reviewed_by: int | None
    reviewed_at: datetime | None
    created_at: datetime


class RequirementDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    decision: Literal["approved", "rejected", "modified"]
    comment: str = Field(default="", max_length=2000)
    # Edits: only allowed together with decision="modified".
    text: str | None = Field(default=None, min_length=3, max_length=1000)
    type: ReqType | None = None
    priority: ReqPriority | None = None

    @model_validator(mode="after")
    def _rules(self):
        edits = [self.text, self.type, self.priority]
        has_edit = any(v is not None for v in edits)
        if self.decision == "modified":
            if not has_edit:
                raise ValueError("modified needs at least one of: text, type, priority")
            if not self.comment:
                raise ValueError("a comment is required when modifying")
        else:
            if has_edit:
                raise ValueError("text/type/priority can only be sent with decision=modified")
            if self.decision == "rejected" and not self.comment:
                raise ValueError("a comment is required when rejecting")
        return self