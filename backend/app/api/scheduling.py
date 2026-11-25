"""PM-only schedule previews from Agent 3. Publication happens in Plan Review."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.common import get_project_or_404
from app.auth.dependencies import require_role
from app.db.models import User
from app.db.session import get_db
from app.schemas.scheduling import ScheduleDraftOut
from app.services.scheduling import latest_schedule_draft

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


@router.post("/scheduling/{draft_id}/decision", deprecated=True, status_code=410)
def decide_schedule(
    project_id: int,
    draft_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("PM")),
):
    """Direct callers of the retired endpoint to the versioned approval flow."""
    get_project_or_404(db, project_id)
    raise HTTPException(410, {
        "code": "schedule_decision_retired",
        "message": "Schedule decisions have moved to Plan Review. Review and decide the plan version there.",
    })
