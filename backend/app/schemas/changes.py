"""Schemas for M11 change management (proposals and decisions on approved items)."""
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ChangeStatus = Literal["draft", "approved", "rejected"]
PublicationStatus = Literal["pending", "published", "rejected", "superseded"]


class TaskChangeProposal(BaseModel):
    """The PM proposes edits to ONE approved task; empty proposals are rejected."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    task_id: int = Field(gt=0)
    title: str | None = Field(default=None, min_length=5, max_length=200)
    estimate_hours: float | None = Field(default=None, ge=0.5, le=500)
    start: date | None = None
    end: date | None = None
    assignee_member_id: int | None = Field(default=None, gt=0)
    reason: str = Field(min_length=5, max_length=2000)


class ChangeDecision(BaseModel):
    """Accept and stage a plan draft or close the proposed change."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    decision: Literal["approved", "rejected"]
    comment: str = Field(default="", max_length=2000)


class ChangeRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    kind: str
    target_task_id: int | None
    plan_version_id: int | None = None
    plan_version: int | None = None
    publication_status: PublicationStatus | None = None
    payload: dict
    impact: dict
    reason: str
    status: ChangeStatus
    created_by: str
    reviewed_by: int | None
    reviewed_at: datetime | None = None
    created_at: datetime | None = None
