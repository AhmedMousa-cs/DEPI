"""M11 change management API: propose and decide edits to approved tasks (PM-only)."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.common import get_project_or_404
from app.auth.dependencies import require_role
from app.db.models import ChangeRequest
from app.db.session import get_db
from app.schemas.changes import ChangeDecision, ChangeRequestOut, TaskChangeProposal
from app.services.changes import ChangeError, decide_change, propose_task_change

router = APIRouter(prefix="/projects/{project_id}", tags=["changes"])


@router.get("/changes", response_model=list[ChangeRequestOut])
def list_changes(
    project_id: int,
    status: str | None = None,
    db: Session = Depends(get_db),
    user=Depends(require_role("PM")),
):
    get_project_or_404(db, project_id)
    stmt = select(ChangeRequest).where(ChangeRequest.project_id == project_id)
    if status:
        stmt = stmt.where(ChangeRequest.status == status)
    return list(db.scalars(stmt.order_by(ChangeRequest.id.desc())))


@router.post("/changes", response_model=ChangeRequestOut, status_code=201)
def propose_change(
    project_id: int,
    body: TaskChangeProposal,
    db: Session = Depends(get_db),
    user=Depends(require_role("PM")),
):
    get_project_or_404(db, project_id)
    try:
        cr = propose_task_change(
            db, project_id=project_id, task_id=body.task_id,
            after={
                "title": body.title,
                "estimate_hours": body.estimate_hours,
                "start": body.start.isoformat() if body.start else None,
                "end": body.end.isoformat() if body.end else None,
                "assignee_member_id": body.assignee_member_id,
            },
            reason=body.reason, pm=user,
        )
    except ChangeError as e:
        raise HTTPException(e.status_code, e.message)
    db.commit()
    db.refresh(cr)
    return cr


@router.post("/changes/{change_id}/decision", response_model=ChangeRequestOut)
def decide_change_request(
    project_id: int,
    change_id: int,
    body: ChangeDecision,
    db: Session = Depends(get_db),
    user=Depends(require_role("PM")),
):
    get_project_or_404(db, project_id)
    try:
        cr = decide_change(
            db, project_id=project_id, change_id=change_id,
            decision=body.decision, comment=body.comment or None, pm=user,
        )
    except ChangeError as e:
        raise HTTPException(e.status_code, e.message)
    db.commit()
    db.refresh(cr)
    return cr
