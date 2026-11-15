"""PM review of Agent 3 output: the schedule draft.

The agent saves a DRAFT (services/scheduling.py); approving it is the only way tasks.start/end get
their dates, it records an audit row, and skips PM-locked tasks. Everything here is PM-only.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.common import get_project_or_404
from app.auth.dependencies import require_role
from app.db.models import ScheduleDraft, User
from app.db.session import get_db
from app.schemas.scheduling import ScheduleDecision, ScheduleDraftOut
from app.services.scheduling import approve_schedule, latest_schedule_draft

router = APIRouter(prefix="/projects/{project_id}", tags=["scheduling"])


@router.get("/scheduling", response_model=ScheduleDraftOut)
def get_schedule(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("PM")),
):
    """The latest schedule draft (any review state; superseded drafts are history)."""
    get_project_or_404(db, project_id)
    draft = latest_schedule_draft(db, project_id)
    if draft is None:
        raise HTTPException(404, "No schedule yet: run the 'agent3.schedule' job first.")
    return draft


@router.post("/scheduling/{draft_id}/decision", response_model=ScheduleDraftOut)
def decide_schedule(
    project_id: int,
    draft_id: int,
    body: ScheduleDecision,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("PM")),
):
    get_project_or_404(db, project_id)
    draft = db.scalar(
        select(ScheduleDraft).where(ScheduleDraft.id == draft_id, ScheduleDraft.project_id == project_id)
    )
    if draft is None:
        raise HTTPException(404, "Schedule draft not found")
    if draft.review_status != "draft":
        raise HTTPException(409, f"This draft was already decided ({draft.review_status}).")
    latest = latest_schedule_draft(db, project_id)
    if latest is not None and latest.id != draft.id:
        raise HTTPException(409, "A newer draft exists: decide on the latest one.")

    draft = approve_schedule(db, draft=draft, pm_id=user.id, decision=body.decision, comment=body.comment)
    db.commit()
    db.refresh(draft)
    return draft
