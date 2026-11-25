"""Test doubles for Agent 4: a scripted LangChain chat model and an in-memory PlanStore.

Used by the unit tests and the offline evals (ToolCorrectness). Not used by any production path.
"""
from __future__ import annotations

import copy
from decimal import Decimal
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from app.schemas.plan import PlanReviewDraft, RiskDraft
from app.services.plan_versions import PlanVersionError, RiskValidationError, diff_snapshots


class ScriptedChatModel(BaseChatModel):
    """Returns the scripted AIMessages in order (then 'done'). bind_tools is a no-op."""

    script: list = Field(default_factory=list)
    idx: int = 0
    seen: list = Field(default_factory=list)  # message lists received, one per model call

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):  # noqa: ARG002
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ARG002
        self.seen.append(list(messages))
        msg = self.script[self.idx] if self.idx < len(self.script) else AIMessage(content="done")
        self.idx += 1
        return ChatResult(generations=[ChatGeneration(message=msg.model_copy(deep=True))])


class NullLedger:
    def start(self, **_kw):
        return None

    def complete(self, *_a, **_kw):
        return None

    def fail(self, *_a, **_kw):
        return None


def tool_msg(*calls: tuple[str, dict], tokens: tuple[int, int] = (400, 40)) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": n, "args": a, "id": f"call_{n}_{i}", "type": "tool_call"} for i, (n, a) in enumerate(calls)],
        usage_metadata={"input_tokens": tokens[0], "output_tokens": tokens[1], "total_tokens": sum(tokens)},
    )


def final_msg(text: str = "Review saved.", tokens: tuple[int, int] = (300, 10)) -> AIMessage:
    return AIMessage(content=text, usage_metadata={"input_tokens": tokens[0], "output_tokens": tokens[1],
                                                   "total_tokens": sum(tokens)})


READ_ALL = ("get_plan_health", "get_project_brief", "get_agent3_results", "get_tasks_and_dependencies",
            "get_team_capacity", "get_open_risks")


def happy_path_script(*, risks: list[dict], verdict: str = "needs_attention",
                      summary: str = "Plan reviewed.", findings: list[str] | None = None) -> list[AIMessage]:
    """The ideal run: read everything, save risks once, save the review once, stop."""
    return [
        tool_msg(*[(n, {}) for n in READ_ALL]),
        tool_msg(("save_draft_risks", {"risks": risks})),
        tool_msg(("save_plan_review", {"verdict": verdict, "summary": summary, "findings": findings or []})),
        final_msg(),
    ]


class InMemoryPlanStore:
    """Same contract as DbPlanStore, no database. Records what the agent saved."""

    def __init__(self, snapshot: dict, *, brief: dict | None = None, team: list[dict] | None = None,
                 requirement_ids=(1,), approved_risks: list[dict] | None = None, approved_snapshot: dict | None = None):
        self._snap = copy.deepcopy(snapshot)
        self._brief = brief or {"project": {"name": "Demo", "goal": "g", "status": "planning"},
                                "brief_untrusted": [], "injection_flags": []}
        self._team = team or [{"id": m["member_id"], "name": m["name"], "skills": [], "available_from": None,
                               "available_to": None} for m in snapshot["capacity"]["members"]]
        self._requirements = set(requirement_ids)
        self._approved_risks = approved_risks or []
        self._approved_snapshot = approved_snapshot
        self.agent_risks: list[RiskDraft] = []
        self.rule_risks: list[Any] = []
        self.reviews: list[tuple[PlanReviewDraft, str]] = []
        self.calls: list[str] = []

    def _check(self, project_id: int, plan_version_id: int) -> None:
        proj = self._snap["project"]["id"]
        if project_id != proj or plan_version_id != self._snap.get("_version_id", plan_version_id):
            raise PlanVersionError("plan version not found in this project")

    def snapshot(self, project_id, plan_version_id):
        self.calls.append("snapshot")
        self._check(project_id, plan_version_id)
        snap = copy.deepcopy(self._snap)
        snap["risks"] = [{"title": r.title, "severity": r.severity} for r in self.agent_risks]
        return snap

    def brief(self, project_id):
        self.calls.append("brief")
        return self._brief

    def team(self, project_id):
        return self._team

    def open_risks(self, project_id, plan_version_id):
        return {"approved_plan_risks": self._approved_risks,
                "draft_risks": [{"title": r.title, "severity": r.severity} for r in self.agent_risks]}

    def diff(self, project_id, plan_version_id, against):
        base = self._approved_snapshot
        if base is None:
            return {"initial": True, "summary": {"changes": 0}, "changes": []}
        return {"against_version": 1, **diff_snapshots(base, self._snap)}

    def _validate(self, risks):
        tasks = {t["id"] for t in self._snap["tasks"]}
        members = {m["member_id"] for m in self._snap["capacity"]["members"]}
        keys = {c["key"] for c in self._snap["conflicts"]}
        errors = []
        for r in risks:
            for e in r.evidence_refs:
                ok = (e.id in keys) if e.type == "conflict" else (
                    e.id.isdigit() and int(e.id) in {"task": tasks, "member": members,
                                                     "requirement": self._requirements}[e.type])
                if not ok:
                    errors.append(f"risk {r.title[:50]!r}: evidence {e.type}:{e.id} does not exist in this plan/project")
        return errors

    def save_agent_risks(self, project_id, plan_version_id, risks, run_id):
        self._check(project_id, plan_version_id)
        errors = self._validate(risks)
        if errors:
            raise RiskValidationError(errors)
        self.agent_risks = list(risks)
        return {"saved": len(risks), "skipped_duplicates": 0, "risk_ids": list(range(1, len(risks) + 1))}

    def save_rule_risks(self, project_id, plan_version_id, findings, run_id):
        self.rule_risks = list(findings)
        return len(findings)

    def save_review(self, project_id, plan_version_id, review, source, run_id):
        self._check(project_id, plan_version_id)
        self.reviews.append((review, source))
        self._snap["review"] = {**review.model_dump(), "source": source, "run_id": run_id}
        return {"saved": True, "verdict": review.verdict}


def sample_snapshot(*, project_id: int = 1, version_id: int = 1, target_end: str | None = "2026-12-01",
                    overlap: bool = True) -> dict:
    """A small realistic snapshot (ids are ints, like DB ids). With overlap=True Ahmed is double booked."""
    tasks = [
        {"id": 11, "title": "Design booking flow", "assignee_member_id": 101, "duration_days": 2,
         "estimate_hours": 16, "start": "2026-11-15", "end": "2026-11-16", "es": 0, "ef": 2, "total_float": 0,
         "is_critical": True, "depends_on": []},
        {"id": 12, "title": "Build booking API", "assignee_member_id": 101, "duration_days": 3,
         "estimate_hours": 24, "start": "2026-11-17", "end": "2026-11-19", "es": 2, "ef": 5, "total_float": 0,
         "is_critical": True, "depends_on": [11]},
        {"id": 13, "title": "Build booking UI", "assignee_member_id": 101 if overlap else 102, "duration_days": 3,
         "estimate_hours": 24, "start": "2026-11-17", "end": "2026-11-19", "es": 2, "ef": 5, "total_float": 1,
         "is_critical": False, "depends_on": [11]},
        {"id": 14, "title": "Test end to end", "assignee_member_id": 102, "duration_days": 1,
         "estimate_hours": 8, "start": "2026-11-22", "end": "2026-11-22", "es": 5, "ef": 6, "total_float": 0,
         "is_critical": True, "depends_on": [12, 13]},
    ]
    conflicts = []
    if overlap:
        conflicts.append({"kind": "overallocation", "assignee_id": 101, "task_ids": [12, 13],
                          "start_date": "2026-11-17", "end_date": "2026-11-19", "working_days": 3,
                          "peak_load_hours_per_day": 16.0, "capacity_hours_per_day": 8.0, "overload_ratio": 2.0,
                          "key": "101|overallocation|2026-11-17|12,13"})
    members = [
        {"member_id": 101, "name": "Ahmed", "capacity_hours_per_week": 40, "hours_per_day": 8.0, "available_days": 6,
         "available_hours": 48.0, "assigned_hours": 64.0 if overlap else 40.0,
         "utilization": 1.333 if overlap else 0.833, "is_overloaded": overlap},
        {"member_id": 102, "name": "Sara", "capacity_hours_per_week": 40, "hours_per_day": 8.0, "available_days": 6,
         "available_hours": 48.0, "assigned_hours": 8.0, "utilization": 0.167, "is_overloaded": False},
    ]
    return {
        "_version_id": version_id,
        "schema_version": 1,
        "project": {"id": project_id, "name": "Clinic Booking", "status": "planning", "start_date": "2026-11-15",
                    "end_date": target_end},
        "schedule": {"project_start": "2026-11-15", "project_end": "2026-11-22", "total_duration_days": 6,
                     "critical_path": [11, 12, 14], "critical_task_ids": [11, 12, 14]},
        "tasks": tasks, "conflicts": conflicts,
        "capacity": {"window_start": "2026-11-15", "window_end": "2026-11-22", "members": members,
                     "total_available_hours": 96.0, "total_assigned_hours": 72.0, "unknown_assignees": []},
        "estimates": [{"task_id": t["id"], "duration_days": t["duration_days"], "source": "hours", "rationale": ""}
                      for t in tasks],
        "explanation": {"summary": "s", "critical_path": "c", "conflicts": [], "estimates": [], "source": "template"},
        "overrides": [], "inputs_fingerprint": "x", "prompt_version": "agent3_v1", "risks": [], "review": None,
    }
