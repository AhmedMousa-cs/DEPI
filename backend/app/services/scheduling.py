"""Store and retrieve Agent 3 schedule previews and historical drafts."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ScheduleDraft


def save_schedule_draft(
    db: Session,
    *,
    project_id: int,
    payload: dict,
    project_start,
    project_end,
    total_duration_days: int,
    conflicts_count: int,
    run_ids: list[int],
    agent: str = "agent3_schedule",
) -> ScheduleDraft:
    """Store a fresh draft and supersede the previous draft ones (approved ones stay history)."""
    old_drafts = db.scalars(
        select(ScheduleDraft)
        .where(ScheduleDraft.project_id == project_id, ScheduleDraft.review_status == "draft")
        .with_for_update()
    ).all()
    for old in old_drafts:
        old.review_status = "superseded"
    draft = ScheduleDraft(
        project_id=project_id,
        run_ids=run_ids,
        payload=payload,
        project_start=project_start,
        project_end=project_end,
        total_duration_days=total_duration_days,
        conflicts_count=conflicts_count,
        review_status="draft",
        created_by_agent=agent,
    )
    db.add(draft)
    db.flush()
    return draft


def latest_schedule_draft(db: Session, project_id: int) -> ScheduleDraft | None:
    """The most recent draft in any review state (superseded ones are history, not the answer)."""
    return db.scalar(
        select(ScheduleDraft)
        .where(ScheduleDraft.project_id == project_id, ScheduleDraft.review_status != "superseded")
        .order_by(ScheduleDraft.id.desc())
        .limit(1)
    )
