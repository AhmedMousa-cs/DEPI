"""Plan approval gate: a draft plan waits here until the PM approves or rejects it.

A small LangGraph graph (one thread per project AND version) with a single interrupt(). The graph
never approves anything by itself: the business write is app/services/plan_approval.py, reached
only after a decision that came through the PM-only API, and every precondition is re-checked
inside the write's transaction.
"""
from __future__ import annotations

import uuid
from typing import Annotated, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select

from app.audit import write_audit
from app.db.models import Project
from app.db.session import SessionLocal
from app.services import plan_approval, plan_versions as pvs

PLAN_GRAPH_VERSION = "plan-1"
OPENABLE_STATUSES = ("planning", "plan_review", "active")


class PlanGateError(Exception):
    pass


class PlanGateNotWaiting(PlanGateError):
    pass


class PlanGateNotAllowed(PlanGateError):
    pass


class InvalidPlanDecision(ValueError):
    pass


def make_plan_thread_id(project_id: int, version: int) -> str:
    for n in (project_id, version):
        if isinstance(n, bool) or not isinstance(n, int) or n <= 0:
            raise ValueError("project_id and version must be positive integers")
    return f"project:{project_id}:plan:{version}"


class PlanDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    decision: Literal["approved", "rejected"]
    comment: str = Field(default="", max_length=2000)
    decided_by: Annotated[int, Field(strict=True, gt=0)]
    acknowledge_warnings: bool = False
    warnings_token: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def _comment_on_reject(self):
        if self.decision == "rejected" and not self.comment:
            raise ValueError("a comment is required when rejecting a plan")
        return self


def parse_plan_decision(raw) -> PlanDecision:
    if isinstance(raw, PlanDecision):
        return raw
    try:
        return PlanDecision.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(p) for p in first["loc"])
        raise InvalidPlanDecision(f"{loc}: {first['msg']}" if loc else first["msg"]) from None


class PlanGateState(TypedDict, total=False):
    project_id: int
    version: int
    run_id: str
    summary: dict
    decision: str | None
    comment: str | None
    decided_by: int | None
    acknowledge_warnings: bool
    warnings_token: str | None
    result: dict | None


class DbPlanWriter:
    """The only business writer behind the gate."""

    def approve(self, *, project_id, version, run_id, decided_by, comment, acknowledge_warnings,
                warnings_token=None) -> dict:
        with SessionLocal() as db:
            out = plan_approval.approve_plan(db, project_id=project_id, version=version, decided_by=decided_by,
                                             comment=comment, acknowledge_warnings=acknowledge_warnings,
                                             warnings_token=warnings_token, run_id=run_id)
            db.commit()
            return out

    def reject(self, *, project_id, version, run_id, decided_by, comment) -> dict:
        with SessionLocal() as db:
            out = plan_approval.reject_plan(db, project_id=project_id, version=version, decided_by=decided_by,
                                            comment=comment, run_id=run_id)
            db.commit()
            return out


def build_plan_graph(writer) -> StateGraph:
    def await_plan_node(state: PlanGateState) -> dict:
        # On resume this node re-runs from the top: everything before interrupt() must be idempotent.
        raw = interrupt({"kind": "plan_approval", "project_id": state["project_id"], "version": state["version"],
                         "run_id": state["run_id"], "summary": state.get("summary", {})})
        d = parse_plan_decision(raw)
        return {"decision": d.decision, "comment": d.comment or None, "decided_by": d.decided_by,
                "acknowledge_warnings": d.acknowledge_warnings, "warnings_token": d.warnings_token}

    def finalize_node(state: PlanGateState) -> dict:
        outcome = state.get("decision")
        common = dict(project_id=state["project_id"], version=state["version"], run_id=state["run_id"],
                      decided_by=state["decided_by"])
        if outcome == "approved":
            res = writer.approve(**common, comment=state.get("comment") or "",
                                 acknowledge_warnings=bool(state.get("acknowledge_warnings")),
                                 warnings_token=state.get("warnings_token"))
        elif outcome == "rejected":
            res = writer.reject(**common, comment=state.get("comment") or "")
        else:
            res = {"written": False}
        return {"result": {"outcome": outcome, **res}}

    def after_finalize(state: PlanGateState) -> str:
        # Preserve the waiting gate when the transaction detects an input or warning change
        # after the API precheck. The PM can review again, retry, or reject the draft.
        result = state.get("result") or {}
        if result.get("code") in ("inputs_busy", "inputs_changed", "review_in_progress", "warnings_changed",
                                  "warnings_not_acknowledged"):
            return "await_plan_node"
        return END

    g = StateGraph(PlanGateState)
    g.add_node("await_plan_node", await_plan_node)
    g.add_node("finalize_node", finalize_node)
    g.add_edge(START, "await_plan_node")
    g.add_edge("await_plan_node", "finalize_node")
    g.add_conditional_edges("finalize_node", after_finalize, ["await_plan_node", END])
    return g


class PlanGate:
    """Service wrapper. Authorization (PM role) is the API layer's job."""

    def __init__(self, checkpointer, writer=None):
        self._graph = build_plan_graph(writer or DbPlanWriter()).compile(checkpointer=checkpointer)

    def _config(self, project_id: int, version: int) -> dict:
        return {"configurable": {"thread_id": make_plan_thread_id(project_id, version)},
                "metadata": {"project_id": project_id, "version": version, "graph_version": PLAN_GRAPH_VERSION}}

    def status(self, project_id: int, version: int) -> dict:
        snap = self._graph.get_state(self._config(project_id, version))
        values = dict(snap.values or {})
        state = "waiting_for_pm" if snap.next else ("completed" if values else "not_started")
        return {"thread_id": make_plan_thread_id(project_id, version), "state": state,
                "run_id": values.get("run_id"), "result": values.get("result")}

    def open(self, project_id: int, version: int, *, for_rejection: bool = False) -> dict:
        """Put the project in plan_review and wait for the PM. Idempotent while already waiting."""
        current = self.status(project_id, version)
        if current["state"] == "waiting_for_pm":
            return current
        with SessionLocal() as db:
            project = db.scalar(select(Project).where(Project.id == project_id).with_for_update())
            if project is None:
                raise PlanGateNotAllowed("project not found")
            if project.status not in OPENABLE_STATUSES:
                raise PlanGateNotAllowed(f"a plan can't be reviewed while the project is '{project.status}'")
            pv = pvs.get_version(db, project_id, version)
            if pv is None:
                raise PlanGateNotAllowed("plan version not found")
            if pv.status != "draft":
                raise PlanGateNotAllowed(f"plan version {version} is '{pv.status}', only a draft can be approved")
            review = pv.snapshot.get("review") or {}
            if not review and not (for_rejection and pv.snapshot.get("change_request_id") is not None):
                raise PlanGateNotAllowed("Agent 4 has not reviewed this draft yet")
            summary = {"version": version, "verdict": review.get("verdict"),
                       "conflicts": len(pv.snapshot.get("conflicts", [])),
                       "risks": len(pv.snapshot.get("risks", [])),
                       "project_end": pv.snapshot["schedule"]["project_end"],
                       "warnings": plan_approval.plan_warnings(pv.snapshot)}
            if project.status != "plan_review":
                before = project.status
                project.status = "plan_review"
                write_audit(db, "system:graph", "plan.gate_opened", "plan_version", pv.id,
                            before={"status": before}, after={"status": "plan_review", "version": version})
            db.commit()
        self._graph.invoke(
            {"project_id": project_id, "version": version, "run_id": uuid.uuid4().hex, "summary": summary,
             "decision": None, "comment": None, "decided_by": None, "acknowledge_warnings": False,
             "warnings_token": None, "result": None},
            self._config(project_id, version))
        return self.status(project_id, version)

    def _resume(self, project_id: int, version: int, payload: dict) -> dict:
        if self.status(project_id, version)["state"] != "waiting_for_pm":
            raise PlanGateNotWaiting(f"the plan gate of project {project_id} v{version} is not waiting for a decision")
        self._graph.invoke(Command(resume=payload), self._config(project_id, version))
        return self.status(project_id, version)

    def approve(self, project_id: int, version: int, decided_by: int, comment: str = "",
                acknowledge_warnings: bool = False, warnings_token: str | None = None) -> dict:
        return self._resume(project_id, version, {"decision": "approved", "comment": comment or "",
                                                  "decided_by": decided_by, "acknowledge_warnings": acknowledge_warnings,
                                                  "warnings_token": warnings_token})

    def reject(self, project_id: int, version: int, decided_by: int, comment: str) -> dict:
        return self._resume(project_id, version, {"decision": "rejected", "comment": comment, "decided_by": decided_by})
