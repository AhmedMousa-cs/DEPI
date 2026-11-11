"""Scope approval gate: Agent 1's drafts wait here until the PM approves the scope.

A small LangGraph graph (its own thread per project) with a single interrupt(). The graph never
approves anything by itself: the business write (project -> planning) happens only after a PM
decision that came through the PM-only API, and only if every requirement has been decided.
"""
import uuid
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy import select

from app.audit import write_audit
from app.db.models import Project
from app.db.session import SessionLocal
from app.orchestrator.decisions import parse_decision
from app.services.scope import requirement_counts

SCOPE_GRAPH_VERSION = "scope-1"
OPENABLE_STATUSES = ("setup", "planning", "scope_review")


class GateError(Exception):
    pass


class GateNotWaiting(GateError):
    pass


class GateNotAllowed(GateError):
    pass


def make_scope_thread_id(project_id: int) -> str:
    if isinstance(project_id, bool) or not isinstance(project_id, int) or project_id <= 0:
        raise ValueError("project_id must be a positive integer")
    return f"project:{project_id}:scope"


class ScopeState(TypedDict, total=False):
    project_id: int
    run_id: str
    summary: dict
    decision: str | None
    comment: str | None
    decided_by: int | None
    result: dict | None


class DbScopeWriter:
    """The only place that moves a project out of scope_review."""

    def approve(self, *, project_id: int, run_id: str, decided_by: int, comment: str | None) -> bool:
        with SessionLocal() as db:
            project = db.scalar(select(Project).where(Project.id == project_id).with_for_update())
            if project is None or project.status != "scope_review":
                return False
            counts = requirement_counts(db, project_id)  # re-checked inside the transaction
            actor = f"user:{decided_by}"
            if counts["draft"] > 0 or counts["approved"] == 0:
                write_audit(db, actor, "scope.approval_blocked", "project", project_id,
                            after={**counts, "run_id": run_id})
                db.commit()
                return False
            project.status = "planning"
            write_audit(db, actor, "scope.approved", "project", project_id,
                        before={"status": "scope_review"},
                        after={"status": "planning", **counts, "run_id": run_id, "comment": comment})
            db.commit()
            return True


def build_scope_graph(writer) -> StateGraph:
    def await_scope_node(state: ScopeState) -> dict:
        # On resume this node re-runs from the top: everything before interrupt() must be idempotent.
        raw = interrupt(
            {
                "kind": "scope_approval",
                "project_id": state["project_id"],
                "run_id": state["run_id"],
                "summary": state.get("summary", {}),
            }
        )
        d = parse_decision(raw)
        return {"decision": d.decision, "comment": d.comment or None, "decided_by": d.decided_by}

    def finalize_node(state: ScopeState) -> dict:
        written = False
        if state.get("decision") == "approved":
            written = writer.approve(
                project_id=state["project_id"], run_id=state["run_id"],
                decided_by=state["decided_by"], comment=state.get("comment"),
            )
        return {"result": {"outcome": state.get("decision"), "written": written}}

    g = StateGraph(ScopeState)
    g.add_node("await_scope_node", await_scope_node)
    g.add_node("finalize_node", finalize_node)
    g.add_edge(START, "await_scope_node")
    g.add_edge("await_scope_node", "finalize_node")
    g.add_edge("finalize_node", END)
    return g


class ScopeGate:
    """Service wrapper. Authorization (PM role) is the API layer's job."""

    def __init__(self, checkpointer, writer=None):
        self._graph = build_scope_graph(writer or DbScopeWriter()).compile(checkpointer=checkpointer)

    def _config(self, project_id: int) -> dict:
        return {
            "configurable": {"thread_id": make_scope_thread_id(project_id)},
            "metadata": {"project_id": project_id, "graph_version": SCOPE_GRAPH_VERSION},
        }

    def status(self, project_id: int) -> dict:
        snap = self._graph.get_state(self._config(project_id))
        values = dict(snap.values or {})
        state = "waiting_for_pm" if snap.next else ("completed" if values else "not_started")
        return {
            "thread_id": make_scope_thread_id(project_id),
            "state": state,
            "run_id": values.get("run_id"),
            "result": values.get("result"),
        }

    def open(self, project_id: int) -> dict:
        """Put the project in scope_review and wait for the PM. Idempotent while already waiting."""
        current = self.status(project_id)
        if current["state"] == "waiting_for_pm":
            return current
        with SessionLocal() as db:
            project = db.scalar(select(Project).where(Project.id == project_id).with_for_update())
            if project is None:
                raise GateNotAllowed("project not found")
            if project.status not in OPENABLE_STATUSES:
                raise GateNotAllowed(f"scope cannot be reviewed while the project is '{project.status}'")
            counts = requirement_counts(db, project_id)
            if project.status != "scope_review":
                before = project.status
                project.status = "scope_review"
                write_audit(db, "system:graph", "scope.gate_opened", "project", project_id,
                            before={"status": before}, after={"status": "scope_review", **counts})
            db.commit()
        self._graph.invoke(
            {"project_id": project_id, "run_id": uuid.uuid4().hex, "summary": counts,
             "decision": None, "comment": None, "decided_by": None, "result": None},
            self._config(project_id),
        )
        return self.status(project_id)

    def approve(self, project_id: int, decided_by: int, comment: str = "") -> dict:
        if self.status(project_id)["state"] != "waiting_for_pm":
            raise GateNotWaiting(f"the scope gate of project {project_id} is not waiting for approval")
        self._graph.invoke(
            Command(resume={"decision": "approved", "comment": comment or "", "decided_by": decided_by}),
            self._config(project_id),
        )
        return self.status(project_id)