"""Schemas for Agent 2 (stories & tasks) and for the PM review of its output.

The agent can only produce refs ("S1"/"T1"), never database ids or a status: ids are assigned by
save_draft_plan, and review_status/approval live behind the PM-only API.
"""
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Rejectable = Literal["approved", "rejected", "modified"]
ReviewStatus = Literal["draft", "approved", "rejected"]

STORY_REF = r"^S[1-9][0-9]*$"
TASK_REF = r"^T[1-9][0-9]*$"
MIN_ESTIMATE_HOURS = 0.5
MAX_ESTIMATE_HOURS = 80.0


def find_cycle(edges: dict[str, list[str]]) -> list[str] | None:
    """Return one dependency cycle as a list of refs, or None. Kahn's algorithm; deterministic order."""
    incoming = {ref: 0 for ref in edges}
    for deps in edges.values():
        for dep in deps:
            incoming[dep] = incoming.get(dep, 0) + 1
    ready = sorted(ref for ref, n in incoming.items() if n == 0)
    seen: list[str] = []
    while ready:
        ref = ready.pop(0)
        seen.append(ref)
        for dep in edges.get(ref, ()):  # ref -> dependency
            incoming[dep] -= 1
            if incoming[dep] == 0:
                ready.append(dep)
    if len(seen) == len(incoming):
        return None
    return sorted(ref for ref, n in incoming.items() if n > 0)


class AgentStory(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    ref: str = Field(pattern=STORY_REF)
    requirement_id: int = Field(gt=0)
    text: str = Field(min_length=10, max_length=500)
    acceptance_criteria: list[str] = Field(min_length=1, max_length=8)
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _criteria(self):
        for criterion in self.acceptance_criteria:
            if not 5 <= len(criterion.strip()) <= 300:
                raise ValueError("each acceptance criterion must be 5..300 characters")
        if len(set(self.acceptance_criteria)) != len(self.acceptance_criteria):
            raise ValueError("acceptance criteria must be unique")
        return self


class AgentTask(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    ref: str = Field(pattern=TASK_REF)
    story_ref: str = Field(pattern=STORY_REF)
    title: str = Field(min_length=5, max_length=200)
    estimate_hours: float = Field(ge=MIN_ESTIMATE_HOURS, le=MAX_ESTIMATE_HOURS)
    depends_on: list[str] = Field(default_factory=list, max_length=20)
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _dependencies(self):
        if self.ref in self.depends_on:
            raise ValueError("a task cannot depend on itself")
        if len(set(self.depends_on)) != len(self.depends_on):
            raise ValueError("depends_on must not contain duplicates")
        return self


class PlanningOutput(BaseModel):
    """Agent 2 output. Deterministic validators reject cycles and dangling references."""

    model_config = ConfigDict(extra="forbid")

    stories: list[AgentStory] = Field(min_length=1, max_length=80)
    tasks: list[AgentTask] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def _links(self):
        story_refs = [s.ref for s in self.stories]
        task_refs = [t.ref for t in self.tasks]
        if len(set(story_refs)) != len(story_refs):
            raise ValueError("story refs must be unique")
        if len(set(task_refs)) != len(task_refs):
            raise ValueError("task refs must be unique")
        unknown_story = sorted({t.story_ref for t in self.tasks} - set(story_refs))
        if unknown_story:
            raise ValueError(f"tasks reference unknown stories: {unknown_story}")
        unknown_dep = sorted({d for t in self.tasks for d in t.depends_on} - set(task_refs))
        if unknown_dep:
            raise ValueError(f"unknown task dependencies: {unknown_dep}")
        edges = {t.ref: list(t.depends_on) for t in self.tasks}
        cycle = find_cycle(edges)
        if cycle:
            raise ValueError(f"dependency cycle detected among {cycle}")
        return self


# ---- review API ---------------------------------------------------------------------------------


class StoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    requirement_id: int
    text: str
    acceptance_criteria: list[str]
    confidence: float
    review_status: str
    locked_by_pm: bool
    pm_comment: str | None
    run_id: int | None
    created_by_agent: str | None
    reviewed_by: int | None
    reviewed_at: datetime | None
    created_at: datetime


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    story_id: int
    title: str
    estimate_hours: float
    assignee_member_id: int | None
    status: str
    progress_pct: int
    start: date | None = None
    end: date | None = None
    is_critical: bool
    confidence: float
    review_status: str
    locked_by_pm: bool
    pm_comment: str | None
    run_id: int | None
    created_by_agent: str | None
    reviewed_by: int | None
    reviewed_at: datetime | None
    created_at: datetime
    depends_on: list[int] = Field(default_factory=list)


class StoryDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    decision: Rejectable
    comment: str = Field(default="", max_length=2000)
    text: str | None = Field(default=None, min_length=10, max_length=500)
    acceptance_criteria: list[str] | None = Field(default=None, min_length=1, max_length=8)

    @model_validator(mode="after")
    def _rules(self):
        has_edit = self.text is not None or self.acceptance_criteria is not None
        if self.decision == "modified":
            if not has_edit:
                raise ValueError("modified needs text or acceptance_criteria")
            if not self.comment:
                raise ValueError("a comment is required when modifying")
            for criterion in self.acceptance_criteria or []:
                if not 5 <= len(criterion.strip()) <= 300:
                    raise ValueError("each acceptance criterion must be 5..300 characters")
        else:
            if has_edit:
                raise ValueError("text/acceptance_criteria can only be sent with decision=modified")
            if self.decision == "rejected" and not self.comment:
                raise ValueError("a comment is required when rejecting")
        return self


class TaskDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    decision: Rejectable
    comment: str = Field(default="", max_length=2000)
    title: str | None = Field(default=None, min_length=5, max_length=200)
    estimate_hours: float | None = Field(default=None, ge=MIN_ESTIMATE_HOURS, le=MAX_ESTIMATE_HOURS)

    @model_validator(mode="after")
    def _rules(self):
        has_edit = self.title is not None or self.estimate_hours is not None
        if self.decision == "modified":
            if not has_edit:
                raise ValueError("modified needs title or estimate_hours")
            if not self.comment:
                raise ValueError("a comment is required when modifying")
        else:
            if has_edit:
                raise ValueError("title/estimate_hours can only be sent with decision=modified")
            if self.decision == "rejected" and not self.comment:
                raise ValueError("a comment is required when rejecting")
        return self


class CoverageOut(BaseModel):
    approved_requirements: int
    requirements_without_story: list[int]
    stories: int
    approved_stories: int
    unreviewed_stories: int
    stories_without_tasks: list[int]
    tasks: int
    approved_tasks: int
    unreviewed_tasks: int
    tasks_without_estimate: list[int]
    total_estimate_hours: float
    dependency_cycles: list[list[int]]
    clean: bool
