"""Requirement counts used by the scope gate and the review screen."""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Requirement


def requirement_counts(db: Session, project_id: int) -> dict:
    rows = dict(
        db.execute(
            select(Requirement.status, func.count())
            .where(Requirement.project_id == project_id)
            .group_by(Requirement.status)
        ).all()
    )
    unverified = db.scalar(
        select(func.count())
        .select_from(Requirement)
        .where(
            Requirement.project_id == project_id,
            Requirement.status == "draft",
            Requirement.quote_verified.is_(False),
        )
    )
    return {
        "draft": rows.get("draft", 0),
        "approved": rows.get("approved", 0),
        "rejected": rows.get("rejected", 0),
        "unverified_drafts": unverified or 0,
    }