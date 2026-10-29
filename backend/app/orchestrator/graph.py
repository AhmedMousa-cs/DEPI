"""Minimal LangGraph spike: draft -> PM approval gate (interrupt) -> finalize.

Rules enforced here: the graph never writes an approved business record before a PM decision,
the decision is validated before resuming, and the business write is idempotent per run.
"""
from __future__ import annotations

import uuid
from typing import Any, Callable, Protocol

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy import select, text

from app.audit import write_audit
from app.db.models import AuditLog
from app.db.session import SessionLocal
from app.orchestrator.decisions import InvalidDecision, PMDecision, parse_decision
from app.orchestrator.state import GRAPH_VERSION, GraphState, make_thread_id

MAX_INPUT_CHARS = 20_000


class GraphError(Exception):
    pass


class AlreadyWaiting(GraphError):
    pass


class NotWaitingForDecision(GraphError):
    pass


class BusinessWriter(Protocol):
    def write(self, *, project_id: int, thread_id: str, run_id: str, outcome: str, payload: dict) -> bool: ...


class AuditBusinessWriter:
    """Spike-level 'business write': one graph.finalized audit row per run (idempotent)."""

    ACTION = "graph.finalized"

    def write(self, *, project_id: int, thread_id: str, run_id: str, outcome: str, payload: dict) -> bool:
        with SessionLocal() as db:
            db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"{self.ACTION}:{run_id}"})
            exists = db.scalar(
                select(AuditLog.id).where(
                    AuditLog.action == self.ACTION,
                    AuditLog.entity_type == "graph_run",
                    AuditLog.entity_id == run_id,
                )
            )
            if exists is not None:
                return False
            write_audit(
                db, "system:graph", self.ACTION, "graph_run", run_id,
                after={"project_id": project_id, "thread_id": thread_id, "outcome": outcome, **payload},
            )
            db.commit()
            return True


def build_graph(writer: BusinessWriter) -> StateGraph:
    def draft_node(state: GraphState) -> dict:
        text_in = (state.get("input_text") or "").strip()
        return {"draft": f"DRAFT (project {state['project_id']}): {text_in[:500]}", "approval_status": "pending"}

    def await_pm_node(state: GraphState) -> dict:
        # On resume this node re-runs from the top, so everything before interrupt() must be idempotent.
        raw = interrupt(
            {
                "kind": "pm_approval",
                "project_id": state["project_id"],
                "thread_id": state["thread_id"],
                "run_id": state["run_id"],
                "draft": state.get("draft"),
            }
        )
        try:
            d = parse_decision(raw)
        except InvalidDecision as exc:  # defence in depth; the runner validates first
            return {"approval_status": "rejected", "errors": [*state.get("errors", []), f"invalid decision: {exc}"]}
        return {"approval_status": d.decision, "pm_comment": d.comment or None, "decided_by": d.decided_by}

    def finalize_node(state: GraphState) -> dict:
        status = state.get("approval_status")
        written = False
        if status in ("approved", "modified"):
            written = writer.write(
                project_id=state["project_id"], thread_id=state["thread_id"], run_id=state["run_id"],
                outcome=status,
                payload={"draft": state.get("draft"), "pm_comment": state.get("pm_comment"), "decided_by": state.get("decided_by")},
            )
        return {"result": {"outcome": status, "written": written}}

    g = StateGraph(GraphState)
    g.add_node("draft_node", draft_node)
    g.add_node("await_pm_node", await_pm_node)
    g.add_node("finalize_node", finalize_node)
    g.add_edge(START, "draft_node")
    g.add_edge("draft_node", "await_pm_node")
    g.add_edge("await_pm_node", "finalize_node")
    g.add_edge("finalize_node", END)
    return g


AuditHook = Callable[..., None]


class GraphRunner:
    """Service wrapper around the compiled graph. Authorization (PM role) is the API layer's job."""

    def __init__(self, checkpointer: Any, writer: BusinessWriter, audit: AuditHook | None = None):
        self._graph = build_graph(writer).compile(checkpointer=checkpointer)
        self._audit = audit

    def _config(self, project_id: int) -> dict:
        return {
            "configurable": {"thread_id": make_thread_id(project_id)},
            "metadata": {"project_id": project_id, "graph_version": GRAPH_VERSION},
        }

    def _emit(self, action: str, *, actor: str, project_id: int, run_id: str | None, payload: dict | None = None) -> None:
        if self._audit is not None:
            self._audit(action, actor=actor, project_id=project_id, thread_id=make_thread_id(project_id),
                        run_id=run_id or "", payload=payload or {})

    @staticmethod
    def _interrupt_payload(snapshot) -> dict | None:
        for task in getattr(snapshot, "tasks", ()) or ():
            for item in getattr(task, "interrupts", ()) or ():
                return getattr(item, "value", None)
        return None

    def status(self, project_id: int) -> dict:
        snap = self._graph.get_state(self._config(project_id))
        values = dict(snap.values or {})
        waiting = bool(snap.next)
        state = "waiting_for_pm" if waiting else ("completed" if values else "not_started")
        return {
            "thread_id": make_thread_id(project_id),
            "state": state,
            "run_id": values.get("run_id"),
            "interrupt": self._interrupt_payload(snap) if waiting else None,
            "values": values,
        }

    def start(self, project_id: int, input_text: str) -> dict:
        thread_id = make_thread_id(project_id)
        if self.status(project_id)["state"] == "waiting_for_pm":
            raise AlreadyWaiting(f"{thread_id} is already waiting for a PM decision")
        clean = (input_text or "").strip()
        if not clean or len(clean) > MAX_INPUT_CHARS:
            raise ValueError(f"input_text must be 1..{MAX_INPUT_CHARS} characters")
        run_id = uuid.uuid4().hex
        initial: GraphState = {
            "project_id": project_id, "thread_id": thread_id, "run_id": run_id, "input_text": clean,
            "draft": None, "approval_status": "pending", "pm_comment": None, "decided_by": None,
            "errors": [], "graph_version": GRAPH_VERSION, "result": None,
        }
        self._graph.invoke(initial, self._config(project_id))
        out = self.status(project_id)
        if out["state"] == "waiting_for_pm":
            self._emit("graph.interrupted", actor="system:graph", project_id=project_id, run_id=run_id)
        return out

    def resume(self, project_id: int, raw_decision: Any) -> dict:
        decision: PMDecision = parse_decision(raw_decision)  # raises InvalidDecision, graph untouched
        current = self.status(project_id)
        if current["state"] != "waiting_for_pm":
            raise NotWaitingForDecision(f"{current['thread_id']} is not waiting for a decision")
        run_id = current["run_id"]
        actor = f"user:{decision.decided_by}"
        self._emit("graph.resumed", actor=actor, project_id=project_id, run_id=run_id, payload={"decision": decision.decision})
        self._emit("pm.rejection" if decision.decision == "rejected" else "pm.approval", actor=actor,
                   project_id=project_id, run_id=run_id,
                   payload={"decision": decision.decision, "comment": decision.comment})
        self._graph.invoke(Command(resume=decision.model_dump()), self._config(project_id))
        return self.status(project_id)
