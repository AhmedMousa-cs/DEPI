"""PM-only change proposals and the publication status of their staged plans."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.api.common import get_project_or_404
from app.auth.dependencies import require_role
from app.db.models import ChangeRequest, PlanVersion
from app.db.session import get_db
from app.schemas.changes import ChangeDecision, ChangeRequestOut, TaskChangeProposal
from app.services.changes import ChangeError, decide_change, propose_task_change

router = APIRouter(prefix="/projects/{project_id}", tags=["changes"])


def _change_query(project_id: int):
    return (
        select(ChangeRequest, PlanVersion.version, PlanVersion.status, PlanVersion.approved_at)
        .outerjoin(PlanVersion, and_(PlanVersion.id == ChangeRequest.plan_version_id,
                                     PlanVersion.project_id == ChangeRequest.project_id))
        .where(ChangeRequest.project_id == project_id)
    )


def _change_response(change: ChangeRequest, version: int | None, status: str | None,
                     approved_at: datetime | None) -> ChangeRequestOut:
    result = ChangeRequestOut.model_validate(change)
    result.plan_version = version
    if approved_at is not None or status == "approved":
        result.publication_status = "published"
    elif status == "draft":
        result.publication_status = "pending"
    elif status in ("rejected", "superseded"):
        result.publication_status = status
    return result


@router.get("/changes", response_model=list[ChangeRequestOut])
def list_changes(
    project_id: int,
    status: str | None = None,
    db: Session = Depends(get_db),
    user=Depends(require_role("PM")),
):
    get_project_or_404(db, project_id)
    stmt = _change_query(project_id)
    if status:
        stmt = stmt.where(ChangeRequest.status == status)
    return [_change_response(*row) for row in db.execute(stmt.order_by(ChangeRequest.id.desc()))]


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
            after=body.model_dump(mode="json", exclude={"task_id", "reason"}, exclude_none=True),
            reason=body.reason, pm=user,
        )
    except ChangeError as e:
        raise HTTPException(e.status_code, e.detail) from e
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
        raise HTTPException(e.status_code, e.detail) from e
    row = db.execute(_change_query(project_id).where(ChangeRequest.id == cr.id)).one()
    result = _change_response(*row)
    db.commit()
    return result
