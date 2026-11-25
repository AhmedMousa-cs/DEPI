"""Plan versions, diff, risks and the PM approval gate. PM only: Team Leaders can't see or decide plans."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.common import get_project_or_404
from app.audit import user_actor, write_audit
from app.auth.dependencies import require_role
from app.db.models import Job, Project, Risk, User
from app.db.session import get_db
from app.orchestrator.checkpoints import postgres_checkpointer
from app.orchestrator.plan_gate import (
    OPENABLE_STATUSES,
    PlanGate,
    PlanGateNotAllowed,
    PlanGateNotWaiting,
)
from app.schemas.job import JobOut
from app.schemas.plan import (
    ApproveBody,
    GenerateBody,
    PlanVersionDetail,
    PlanVersionSummary,
    RejectBody,
    ReviewBody,
    RiskOut,
)
from app.services import plan_approval, plan_versions as pvs
from app.services.jobs import ACTIVE_STATUSES
from app.services.plan_health import confidence_cap, plan_health

router = APIRouter(prefix="/projects/{project_id}/plan", tags=["plan"])
PM = Depends(require_role("PM"))


def _version_or_404(db: Session, project_id: int, version: int):
    pv = pvs.get_version(db, project_id, version)
    if pv is None:
        raise HTTPException(404, "Plan version not found")
    return pv


def _enqueue(db: Session, user: User, project_id: int, type_: str, params: dict) -> Job:
    job = Job(project_id=project_id, type=type_, params=params, created_by=user.id)
    db.add(job)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(Job).where(Job.project_id == project_id, Job.type == type_,
                                               Job.status.in_(ACTIVE_STATUSES)))
        raise HTTPException(409, {"message": f"A '{type_}' job is already active for this project.",
                                  "job_id": existing.id if existing else None})
    write_audit(db, user_actor(user), "job.create", "job", job.id, after={"project_id": project_id, "type": type_})
    db.commit()
    db.refresh(job)
    return job


@router.get("/versions", response_model=list[PlanVersionSummary])
def list_versions(project_id: int, db: Session = Depends(get_db), user: User = PM):
    get_project_or_404(db, project_id)
    return pvs.list_versions(db, project_id)


@router.get("/versions/{version}", response_model=PlanVersionDetail)
def get_version(project_id: int, version: int, db: Session = Depends(get_db), user: User = PM):
    get_project_or_404(db, project_id)
    pv = _version_or_404(db, project_id, version)
    approved = pvs.current_approved(db, project_id)
    vs_approved = None
    if approved is not None and approved.id != pv.id:
        vs_approved = {"against_version": approved.version, **pvs.diff_snapshots(approved.snapshot, pv.snapshot)}
    detail = PlanVersionDetail.model_validate(pv)
    detail.risks = [RiskOut.model_validate(r) for r in pvs.risks_of(db, pv.id, include_dismissed=True)]
    detail.warnings = plan_approval.plan_warnings(pv.snapshot) if pv.status == "draft" else []
    detail.diff_vs_approved = vs_approved
    return detail


@router.get("/diff")
def diff(project_id: int, from_version: int = Query(gt=0), to_version: int = Query(gt=0),
         db: Session = Depends(get_db), user: User = PM):
    get_project_or_404(db, project_id)
    try:
        return pvs.diff_versions(db, project_id, from_version, to_version)
    except pvs.PlanVersionError:
        raise HTTPException(404, "Plan version not found")


@router.get("/risks", response_model=list[RiskOut])
def list_risks(project_id: int, status: str | None = Query(default=None, pattern="^(draft|open|dismissed|superseded)$"),
               db: Session = Depends(get_db), user: User = PM):
    get_project_or_404(db, project_id)
    stmt = select(Risk).where(Risk.project_id == project_id).order_by(Risk.id)
    if status:
        stmt = stmt.where(Risk.status == status)
    return list(db.scalars(stmt))


@router.post("/generate", response_model=JobOut, status_code=202)
def generate(project_id: int, body: GenerateBody, db: Session = Depends(get_db), user: User = PM):
    """Queue Agent 3 (new draft version, optionally with what-if overrides); Agent 4 reviews it next."""
    project: Project = get_project_or_404(db, project_id)
    if project.status not in OPENABLE_STATUSES:
        raise HTTPException(409, f"A plan can't be built while the project is '{project.status}'; approve the scope first.")
    params = body.model_dump(exclude_none=True)
    return _enqueue(db, user, project_id, "agent3.schedule", params)


@router.post("/review", response_model=JobOut, status_code=202)
def review(project_id: int, body: ReviewBody | None = None, db: Session = Depends(get_db), user: User = PM):
    """Queue (another) Agent 4 review of the current draft."""
    get_project_or_404(db, project_id)
    draft = pvs.current_draft(db, project_id)
    if draft is None:
        raise HTTPException(409, "There is no draft plan to review.")
    params = {"version": draft.version, "rules_only": bool(body and body.rules_only)}
    return _enqueue(db, user, project_id, "agent4.review", params)


def _gate_view(state: dict, project_id: int, version: int) -> dict:
    return {"project_id": project_id, "version": version, **state}


@router.get("/versions/{version}/health")
def version_health(project_id: int, version: int, db: Session = Depends(get_db), user: User = PM):
    """Deterministic plan health for a stored version (no LLM): the facts behind a review."""
    get_project_or_404(db, project_id)
    pv = _version_or_404(db, project_id, version)
    health = plan_health(pv.snapshot or {})
    return {"version": pv.version, "health": health, "confidence_cap": confidence_cap(health)}


@router.get("/versions/{version}/gate")
def gate_status(project_id: int, version: int, db: Session = Depends(get_db), user: User = PM):
    get_project_or_404(db, project_id)
    _version_or_404(db, project_id, version)
    with postgres_checkpointer() as saver:
        return _gate_view(PlanGate(saver).status(project_id, version), project_id, version)


@router.post("/versions/{version}/open-gate")
def open_gate(project_id: int, version: int, db: Session = Depends(get_db), user: User = PM):
    get_project_or_404(db, project_id)
    _version_or_404(db, project_id, version)
    db.close()
    try:
        with postgres_checkpointer() as saver:
            return _gate_view(PlanGate(saver).open(project_id, version), project_id, version)
    except PlanGateNotAllowed as exc:
        raise HTTPException(409, str(exc))


def _precheck(db: Session, project_id: int, version: int, acknowledge: bool) -> None:
    """Refuse BEFORE the gate is resumed, so a refusal doesn't consume the waiting interrupt."""
    project = db.get(Project, project_id)
    pv = pvs.get_version(db, project_id, version)
    blocker = plan_approval.approval_blockers(db, project, pv, acknowledge)
    if blocker is not None:
        raise HTTPException(409, blocker)


@router.post("/versions/{version}/approve")
def approve(project_id: int, version: int, body: ApproveBody | None = None, db: Session = Depends(get_db),
            user: User = PM):
    """The only way a plan becomes approved. decided_by is the authenticated PM, never the body."""
    get_project_or_404(db, project_id)
    _version_or_404(db, project_id, version)
    body = body or ApproveBody()
    _precheck(db, project_id, version, body.acknowledge_warnings)
    db.close()
    try:
        with postgres_checkpointer() as saver:
            state = PlanGate(saver).approve(project_id, version, user.id, body.comment, body.acknowledge_warnings)
    except PlanGateNotWaiting:
        raise HTTPException(409, "The approval gate is not open for this version. Open it first.")
    result = state.get("result") or {}
    if not result.get("written"):
        raise HTTPException(409, {"message": result.get("message", "Approval was refused."),
                                  "code": result.get("code"), "hint": "Re-open the gate and try again."})
    return {"project_id": project_id, "version": version, "status": "approved", **{k: v for k, v in result.items()
                                                                                     if k != "written"}}


@router.post("/versions/{version}/reject")
def reject(project_id: int, version: int, body: RejectBody, db: Session = Depends(get_db), user: User = PM):
    get_project_or_404(db, project_id)
    _version_or_404(db, project_id, version)
    db.close()
    try:
        with postgres_checkpointer() as saver:
            state = PlanGate(saver).reject(project_id, version, user.id, body.comment)
    except PlanGateNotWaiting:
        raise HTTPException(409, "The approval gate is not open for this version. Open it first.")
    result = state.get("result") or {}
    if not result.get("written"):
        raise HTTPException(409, {"message": result.get("message", "Rejection was refused."), "code": result.get("code")})
    return {"project_id": project_id, "version": version, "status": "rejected"}
