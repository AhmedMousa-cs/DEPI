from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.common import get_project_or_404
from app.auth.dependencies import require_role
from app.db.models import Project, User
from app.db.session import get_db
from app.orchestrator.checkpoints import postgres_checkpointer
from app.orchestrator.scope_gate import GateNotAllowed, GateNotWaiting, ScopeGate
from app.services.scope import requirement_counts

# Everything about scope approval is PM-only (Blueprint section 3).
router = APIRouter(prefix="/projects/{project_id}/scope", tags=["scope"])


class ApproveBody(BaseModel):
    """decided_by is NOT accepted: it always comes from the authenticated PM."""
    model_config = ConfigDict(extra="forbid")
    comment: str = Field(default="", max_length=2000)


def _payload(db: Session, project: Project, gate: dict) -> dict:
    db.refresh(project)
    return {
        "project_status": project.status,
        "gate": gate["state"],
        "run_id": gate["run_id"],
        "result": gate["result"],
        "counts": requirement_counts(db, project.id),
    }


@router.get("/status")
def scope_status(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_role("PM"))):
    project = get_project_or_404(db, project_id)
    with postgres_checkpointer() as saver:
        gate = ScopeGate(saver).status(project_id)
    return _payload(db, project, gate)


@router.post("/open")
def open_scope_gate(project_id: int, db: Session = Depends(get_db), user: User = Depends(require_role("PM"))):
    """Normally opened by the extraction job; use this to retry if that step failed or to re-review."""
    project = get_project_or_404(db, project_id)
    counts = requirement_counts(db, project_id)
    if sum(counts.values()) - counts["unverified_drafts"] == 0:
        raise HTTPException(409, "No requirements yet: run the extraction first.")
    with postgres_checkpointer() as saver:
        try:
            gate = ScopeGate(saver).open(project_id)
        except GateNotAllowed as exc:
            raise HTTPException(409, str(exc))
    return _payload(db, project, gate)


@router.post("/approve")
def approve_scope(project_id: int, body: ApproveBody, db: Session = Depends(get_db),
                  user: User = Depends(require_role("PM"))):
    project = get_project_or_404(db, project_id)
    counts = requirement_counts(db, project_id)
    if counts["draft"] > 0:
        raise HTTPException(409, {"message": "Decide on every requirement before approving the scope.",
                                  "drafts": counts["draft"]})
    if counts["approved"] == 0:
        raise HTTPException(409, {"message": "The scope needs at least one approved requirement."})
    with postgres_checkpointer() as saver:
        try:
            gate = ScopeGate(saver).approve(project_id, user.id, body.comment)
        except GateNotWaiting as exc:
            raise HTTPException(409, str(exc))
    if not (gate.get("result") or {}).get("written"):
        raise HTTPException(409, "The scope was not approved (requirements changed meanwhile). "
                                 "Re-open the gate and try again.")
    return _payload(db, project, gate)