"""Schedule draft persistence + PM approval (M6.1). Row changes and audit rows share one transaction.

Draft-only rule: the agent writes ONLY schedule_drafts rows. tasks.start / tasks.end are written
by approve_schedule (a PM decision), never by the agent.
"""
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import user_actor, write_audit
from app.db.models import ScheduleDraft, Task


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


def approve_schedule(
    db: Session,
    *,
    draft: ScheduleDraft,
    pm_id: int,
    decision: str,
    comment: str = "",
) -> ScheduleDraft:
    """Apply a PM decision. On approval the dates land on the tasks; PM-locked tasks are skipped."""
    before_tasks = {
        str(t.id): {"start": t.start.isoformat() if t.start else None, "end": t.end.isoformat() if t.end else None}
        for t in db.scalars(select(Task).where(Task.project_id == draft.project_id))
    }

    if decision == "approved":
        dates = {str(t["id"]): (t["start_date"], t["end_date"]) for t in draft.payload["schedule"]["tasks"]}
        tasks = db.scalars(select(Task).where(Task.project_id == draft.project_id)).all()
        written = 0
        for task in tasks:
            if task.review_status != "approved":
                continue  # only tasks the PM approved are part of the plan; drafts/rejected stay undated
            # note: locked_by_pm is set by ANY PM decision (incl. plain approval) and only stops
            # agents from overwriting PM-reviewed content on re-runs. Approving the schedule IS
            # the PM decision that authorizes these dates, so approved tasks are dated.
            got = dates.get(str(task.id))
            if got is None:
                continue  # task created after the draft: it simply has no date yet
            task.start, task.end = date.fromisoformat(got[0]), date.fromisoformat(got[1])
            written += 1
    else:
        written = 0

    draft.review_status = decision
    draft.pm_comment = comment or None
    draft.reviewed_by = pm_id
    draft.reviewed_at = datetime.now(timezone.utc)
    write_audit(
        db,
        f"user:{pm_id}",
        "schedule.approved" if decision == "approved" else "schedule.rejected",
        "schedule_draft",
        draft.id,
        before={"tasks": before_tasks},
        after={
            "decision": decision,
            "comment": comment or None,
            "tasks_dated": written,
            "project_start": draft.project_start.isoformat(),
            "project_end": draft.project_end.isoformat(),
            "total_duration_days": draft.total_duration_days,
            "conflicts_count": draft.conflicts_count,
        },
    )
    return draft
