from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.common import get_project_or_404
from app.audit import user_actor, write_audit
from app.auth.dependencies import require_role
from app.db.models import AgentRun, Decision, Requirement, User
from app.db.session import get_db
from app.observability.langfuse import send_pm_score
from app.schemas.review import ReqStatus, RequirementDecision, RequirementOut

# Reviewing agent output is PM-only (Blueprint section 3).
router = APIRouter(prefix="/projects/{project_id}/requirements", tags=["requirements"])


def _snapshot(r: Requirement) -> dict:
    return {
        "status": r.status,
        "type": r.type,
        "text": r.text,
        "priority": r.priority,
        "locked_by_pm": r.locked_by_pm,
    }


def _load(db: Session, project_id: int, requirement_id: int, *, lock: bool = False) -> Requirement:
    # Filtering by project_id too: an id from another project is a 404, not a leak.
    stmt = select(Requirement).where(
        Requirement.id == requirement_id, Requirement.project_id == project_id
    )
    if lock:
        stmt = stmt.with_for_update()
    req = db.scalar(stmt)
    if req is None:
        raise HTTPException(404, "Requirement not found")
    return req


@router.get("", response_model=list[RequirementOut])
def list_requirements(
    project_id: int,
    status: ReqStatus | None = None,
    verified: bool | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("PM")),
):
    get_project_or_404(db, project_id)
    stmt = select(Requirement).where(Requirement.project_id == project_id)
    if status is not None:
        stmt = stmt.where(Requirement.status == status)
    if verified is not None:
        stmt = stmt.where(Requirement.quote_verified == verified)
    return list(db.scalars(stmt.order_by(Requirement.id)))


@router.get("/{requirement_id}", response_model=RequirementOut)
def get_requirement(
    project_id: int,
    requirement_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("PM")),
):
    get_project_or_404(db, project_id)
    return _load(db, project_id, requirement_id)


@router.post("/{requirement_id}/decision", response_model=RequirementOut)
def decide_requirement(
    project_id: int,
    requirement_id: int,
    body: RequirementDecision,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("PM")),
):
    get_project_or_404(db, project_id)
    req = _load(db, project_id, requirement_id, lock=True)
    before = _snapshot(req)

    if body.decision == "modified":
        if body.text is not None:
            req.text = body.text
        if body.type is not None:
            req.type = body.type
        if body.priority is not None:
            req.priority = body.priority

    req.status = "rejected" if body.decision == "rejected" else "approved"
    req.locked_by_pm = True  # any PM decision protects the item from being overwritten by a re-run
    req.pm_comment = body.comment or None
    req.reviewed_by = user.id
    req.reviewed_at = datetime.now(timezone.utc)

    db.add(
        Decision(
            project_id=project_id,
            subject_type="requirement",
            subject_id=req.id,
            decision=body.decision,
            reason=body.comment or None,
            decided_by=user.id,
        )
    )
    write_audit(
        db,
        user_actor(user),
        f"requirement.{body.decision}",
        "requirement",
        req.id,
        before=before,
        after={**_snapshot(req), "project_id": project_id, "comment": body.comment or None},
    )
    run_id = req.run_id
    db.commit()

    # Best-effort quality signal for Langfuse; it can never fail the request.
    trace_id = db.scalar(select(AgentRun.trace_id).where(AgentRun.id == run_id)) if run_id else None
    send_pm_score(trace_id=trace_id, decision=body.decision, requirement_id=requirement_id)

    db.refresh(req)
    return req