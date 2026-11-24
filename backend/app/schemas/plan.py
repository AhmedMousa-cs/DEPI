"""Schemas for plan versions, risks and the plan review (Agent 4) + the plan approval API."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Severity = Literal["low", "medium", "high", "critical"]
Likelihood = Literal["low", "medium", "high"]
EvidenceType = Literal["task", "member", "requirement", "conflict"]
Verdict = Literal["ready", "needs_attention", "blocked"]
Confidence = Literal["low", "medium", "high"]
# Advisory only: this is what Agent 4 would do, never a decision. The PM decides.
Recommendation = Literal["approve", "approve_with_changes", "revise"]
PlanStatus = Literal["draft", "approved", "rejected", "superseded"]

SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}
RECOMMENDATION_FOR_VERDICT: dict[str, str] = {
    "ready": "approve",
    "needs_attention": "approve_with_changes",
    "blocked": "revise",
}


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
    """Agent 4's review. Advisory: nothing here approves, rejects or changes a plan."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    verdict: Verdict
    summary: str = Field(min_length=3, max_length=2000)
    findings: list[str] = Field(default_factory=list, max_length=20)
    confidence: Confidence = "medium"
    recommendation: Recommendation | None = None
    open_questions: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("findings")
    @classmethod
    def _trim(cls, v: list[str]) -> list[str]:
        return [f.strip()[:500] for f in v if f and f.strip()]

    @field_validator("open_questions")
    @classmethod
    def _trim_questions(cls, v: list[str]) -> list[str]:
        return [q.strip()[:300] for q in v if q and q.strip()][:8]

    @model_validator(mode="after")
    def _consistent_recommendation(self):
        # "approve" next to a non-ready verdict is a contradiction the PM would have to untangle.
        if self.recommendation == "approve" and self.verdict != "ready":
            raise ValueError("recommendation 'approve' requires verdict 'ready'; use "
                             "'approve_with_changes' or 'revise' for this verdict")
        return self


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
    warnings: list[str] = []
    diff_vs_approved: dict | None = None


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


class ReviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rules_only: bool = False


class GenerateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overrides: list[TaskOverride] = Field(default_factory=list, max_length=200)
    start_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
