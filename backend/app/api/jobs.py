from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import app.services.job_handlers  # noqa: F401  (registers handlers so types can be validated)
from app.api.common import get_project_or_404, user_has_project_access
from app.audit import user_actor, write_audit
from app.auth.dependencies import get_current_user, require_project_access, require_role
from app.db.models import Job, User
from app.db.session import get_db
from app.schemas.job import JobCreate, JobOut
from app.services.jobs import ACTIVE_STATUSES, HANDLERS

router = APIRouter(tags=["jobs"])


@router.post("/projects/{project_id}/jobs", response_model=JobOut, status_code=202)
def create_job(
    project_id: int,
    body: JobCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("PM")),
):
    get_project_or_404(db, project_id)
    if body.type not in HANDLERS:
        raise HTTPException(422, f"Unknown job type '{body.type}'")
    job = Job(project_id=project_id, type=body.type, params=body.params, created_by=user.id)
    db.add(job)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(
            select(Job).where(
                Job.project_id == project_id,
                Job.type == body.type,
                Job.status.in_(ACTIVE_STATUSES),
            )
        )
        raise HTTPException(
            409,
            {
                "message": f"A '{body.type}' job is already active for this project.",
                "job_id": existing.id if existing else None,
            },
        )
    write_audit(
        db, user_actor(user), "job.create", "job", job.id,
        after={"project_id": project_id, "type": body.type},
    )
    db.commit()
    db.refresh(job)
    return job


@router.get("/projects/{project_id}/jobs", response_model=list[JobOut])
def list_jobs(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_project_access),
):
    get_project_or_404(db, project_id)
    return list(
        db.scalars(
            select(Job).where(Job.project_id == project_id).order_by(Job.id.desc()).limit(20)
        )
    )


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(
    job_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    if not user_has_project_access(db, user, job.project_id):
        raise HTTPException(403, "No access to this project")
    return job
