"""Graph audit hook: pass db_graph_audit as GraphRunner(..., audit=db_graph_audit)."""
from app.audit import write_audit
from app.db.session import SessionLocal

GRAPH_EVENTS = ("graph.interrupted", "graph.resumed", "pm.approval", "pm.rejection")


def db_graph_audit(action: str, *, actor: str, project_id: int, thread_id: str, run_id: str, payload: dict) -> None:
    with SessionLocal() as db:
        write_audit(db, actor, action, "graph_run", run_id,
                    after={"project_id": project_id, "thread_id": thread_id, **payload})
        db.commit()
