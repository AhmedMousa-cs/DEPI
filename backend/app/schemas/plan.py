"""Schemas for plan versions, risks and the plan review (Agent 4) + the plan approval API."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Severity = Literal["low", "medium", "high", "critical"]
Likelihood = Literal["low", "medium", "high"]
EvidenceType = Literal["task", "member", "requirement", "conflict"]
Verdict = Literal["ready", "needs_attention", "blocked"]
PlanStatus = Literal["draft", "approved", "rejected", "superseded"]

SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


class EvidenceRef(BaseModel):
    """Points at something real in the project. The service verifies it exists."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    type: EvidenceType
    id: str = Field(min_length=1, max_length=200)
    note: str = Field(default="", max_length=300)

    @field_validator("id", mode="before")
    @classmethod
    def _id_to_str(cls, v):
        return str(v) if isinstance(v, int) and not isinstance(v, bool) else v


class RiskDraft(BaseModel):
    """What an agent (or a rule) may propose. There is deliberately no status field."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=3, max_length=200)
    description: str = Field(min_length=3, max_length=2000)
    severity: Severity
    likelihood: Likelihood
    mitigation: str = Field(default="", max_length=1000)
    evidence_refs: list[EvidenceRef] = Field(min_length=1, max_length=10)


class PlanReviewDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    verdict: Verdict
    summary: str = Field(min_length=3, max_length=2000)
    findings: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("findings")
    @classmethod
    def _trim(cls, v: list[str]) -> list[str]:
        return [f.strip()[:500] for f in v if f and f.strip()]


# ------------------------------------------------------------------------------ API
class RiskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    plan_version_id: int | None
    title: str
    description: str
    severity: str
    likelihood: str
    mitigation: str | None
    evidence_refs: list
    status: str
    created_by: str


class PlanVersionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    version: int
    status: str
    created_by: str
    base_version: int | None
    created_at: datetime
    approved_by: int | None
    approved_at: datetime | None


class PlanVersionDetail(PlanVersionSummary):
    snapshot: dict
    diff_from_prev: dict | None
    risks: list[RiskOut] = []


class ApproveBody(BaseModel):
    """decided_by is NOT accepted: it always comes from the authenticated PM."""

    model_config = ConfigDict(extra="forbid")

    comment: str = Field(default="", max_length=2000)
    acknowledge_warnings: bool = False


class RejectBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    comment: str = Field(min_length=1, max_length=2000)


class TaskOverride(BaseModel):
    """PM what-if input for a new draft version."""

    model_config = ConfigDict(extra="forbid")

    task_id: int = Field(gt=0)
    duration_days: int | None = Field(default=None, ge=1, le=365)
    assignee_member_id: int | None = Field(default=None, gt=0)


class GenerateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overrides: list[TaskOverride] = Field(default_factory=list, max_length=200)
    start_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
