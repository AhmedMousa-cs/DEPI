from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.db.models import Project, ProjectMember, User


def get_project_or_404(db: Session, project_id: int) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return project


def user_has_project_access(db: Session, user: User, project_id: int) -> bool:
    if user.role == "PM":
        return True
    return db.get(ProjectMember, (project_id, user.id)) is not None
