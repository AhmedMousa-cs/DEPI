"""Schemas for M11 change management (proposals and decisions on approved items)."""
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ChangeStatus = Literal["draft", "approved", "rejected"]


class TaskChangeProposal(BaseModel):
    """The PM proposes edits to ONE approved task; empty proposals are rejected."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    task_id: int
    title: str | None = Field(default=None, min_length=5, max_length=200)
    estimate_hours: float | None = Field(default=None, ge=0.5, le=500)
    start: date | None = None
    end: date | None = None
    assignee_member_id: int | None = None
    reason: str = Field(min_length=5, max_length=2000)


class ChangeDecision(BaseModel):
    """Approving applies the stored change; rejecting closes it. Both audited."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    decision: Literal["approved", "rejected"]
    comment: str = Field(default="", max_length=2000)


class ChangeRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    kind: str
    target_task_id: int | None
    payload: dict
    impact: dict
    reason: str
    status: ChangeStatus
    created_by: str
    reviewed_by: int | None
    reviewed_at: datetime | None = None
    created_at: datetime | None = None
