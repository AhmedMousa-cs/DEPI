from datetime import date
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import user_actor, write_audit
from app.auth.dependencies import (
    get_current_user,
    require_project_access,
    require_role,
)
from app.db.models import Project, ProjectMember, Source, User
from app.db.session import get_db
from app.schemas.project import (
    ProjectCreate,
    ProjectDetail,
    ProjectOut,
    ProjectUpdate,
    SourceSummary,
)

router = APIRouter(prefix="/projects", tags=["projects"])


def _jsonable(value):
    return value.isoformat() if isinstance(value, date) else value


def _get_or_404(db: Session, project_id: int) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return project


def _brief_source(db: Session, project_id: int) -> Source | None:
    return db.scalar(
        select(Source)
        .where(Source.project_id == project_id, Source.kind == "brief")
        .order_by(Source.id)
    )


def _detail(db: Session, project: Project) -> ProjectDetail:
    sources = list(
        db.scalars(
            select(Source).where(Source.project_id == project.id).order_by(Source.id)
        )
    )
    brief = next((s.content_text for s in sources if s.kind == "brief"), None)
    base = ProjectOut.model_validate(project).model_dump()
    return ProjectDetail(
        **base,
        brief=brief,
        sources=[SourceSummary.model_validate(s) for s in sources],
    )


@router.post("", response_model=ProjectDetail, status_code=status.HTTP_201_CREATED)
def create_project(
    body: ProjectCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("PM")),
):
    project = Project(
        name=body.name,
        goal=body.goal,
        owner_id=user.id,
        start_date=body.start_date,
        end_date=body.end_date,
    )
    db.add(project)
    db.flush()
    db.add(
        Source(
            project_id=project.id,
            kind="brief",
            title="Project Brief",
            content_text=body.brief,
            uploaded_by=user.id,
            phase="initial",
        )
    )
    if body.transcript:
        db.add(
            Source(
                project_id=project.id,
                kind="transcript",
                title="Initial transcript",
                content_text=body.transcript,
                uploaded_by=user.id,
                phase="initial",
            )
        )
    write_audit(
        db,
        user_actor(user),
        "project.create",
        "project",
        project.id,
        after={
            **body.model_dump(mode="json", exclude={"brief", "transcript"}),
            "brief_length": len(body.brief),
            "has_transcript": bool(body.transcript),
        },
    )
    db.commit()
    db.refresh(project)
    return _detail(db, project)


@router.get("", response_model=list[ProjectOut])
def list_projects(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    stmt = select(Project).order_by(Project.id.desc())
    if user.role != "PM":
        stmt = stmt.join(ProjectMember, ProjectMember.project_id == Project.id).where(
            ProjectMember.user_id == user.id
        )
    return list(db.scalars(stmt))


@router.get("/{project_id}", response_model=ProjectDetail)
def get_project(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_project_access),
):
    return _detail(db, _get_or_404(db, project_id))


@router.patch("/{project_id}", response_model=ProjectDetail)
def update_project(
    project_id: int,
    body: ProjectUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("PM")),
):
    project = _get_or_404(db, project_id)
    data = body.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No fields to update")

    has_brief = "brief" in data
    new_brief = data.pop("brief", None)

    before: dict = {}
    after: dict = {}
    for field, value in data.items():
        old = getattr(project, field)
        if old != value:
            before[field] = _jsonable(old)
            after[field] = _jsonable(value)
            setattr(project, field, value)

    if project.start_date and project.end_date and project.end_date < project.start_date:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "end_date must not be before start_date",
        )

    if has_brief:
        src = _brief_source(db, project.id)
        if src is None:
            db.add(
                Source(
                    project_id=project.id,
                    kind="brief",
                    title="Project Brief",
                    content_text=new_brief,
                    uploaded_by=user.id,
                    phase="initial",
                )
            )
            before["brief_length"] = 0
            after["brief_length"] = len(new_brief)
        elif src.content_text != new_brief:
            before["brief_length"] = len(src.content_text)
            after["brief_length"] = len(new_brief)
            src.content_text = new_brief

    if before or after:
        write_audit(
            db,
            user_actor(user),
            "project.update",
            "project",
            project.id,
            before=before,
            after=after,
        )
        db.commit()
        db.refresh(project)
    return _detail(db, project)
