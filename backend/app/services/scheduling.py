"""Store and retrieve Agent 3 schedule previews and historical drafts."""

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.db.models import AuditLog, PlanVersion, ScheduleDraft
from app.schemas.scheduling import ScheduleDraftOut


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


def latest_schedule_draft(db: Session, project_id: int) -> ScheduleDraftOut | None:
    """Show the open preview or the published schedule, with their plan version status."""
    # Older Agent 3 previews recorded the association in an audit row in the same
    # transaction. A preview from before plan versioning has neither association.
    recorded_version = (
        select(AuditLog.after["version"].as_integer())
        .where(
            AuditLog.action == "agent3.schedule_completed",
            AuditLog.entity_type == "project",
            AuditLog.entity_id == str(project_id),
            AuditLog.after["draft_id"].as_integer() == ScheduleDraft.id,
        )
        .order_by(AuditLog.id.desc())
        .limit(1)
        .correlate(ScheduleDraft)
        .scalar_subquery()
    )
    linked_version = func.coalesce(ScheduleDraft.payload["plan_version"].as_integer(), recorded_version)
    row = db.execute(
        select(ScheduleDraft, PlanVersion.version, PlanVersion.status, PlanVersion.approved_by, PlanVersion.approved_at)
        .outerjoin(PlanVersion, and_(PlanVersion.project_id == project_id, PlanVersion.version == linked_version))
        .where(
            ScheduleDraft.project_id == project_id,
            or_(
                PlanVersion.status.in_(("draft", "approved")),
                and_(linked_version.is_(None), ScheduleDraft.review_status != "superseded"),
            ),
        )
        .order_by(ScheduleDraft.id.desc())
        .limit(1)
    ).one_or_none()
    if row is None:
        return None
    draft, version, status, approved_by, approved_at = row
    preview = ScheduleDraftOut.model_validate(draft)
    if version is None:
        return preview
    return preview.model_copy(update={
        "plan_version": version,
        "review_status": status,
        "reviewed_by": approved_by,
        "reviewed_at": approved_at,
    })
