"""PM review of Agent 2 output: stories, tasks and the coverage report.

Everything here is PM-only. Agent 2 writes drafts (app/services/planning.py); approval happens
only through these endpoints, records a Decision + audit row, and locks the item.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.common import get_project_or_404
from app.audit import user_actor, write_audit
from app.auth.dependencies import require_role
from app.db.models import AgentRun, Decision, Task, TaskDependency, User, UserStory
from app.db.session import get_db
from app.observability.langfuse import send_pm_score
from app.schemas.planning import (
    BulkPlanDecision,
    CoverageOut,
    ReviewStatus,
    StoryDecision,
    StoryOut,
    TaskDecision,
    TaskOut,
)
from app.services.planning import planning_coverage

router = APIRouter(prefix="/projects/{project_id}", tags=["planning"])


def _story_snapshot(story: UserStory) -> dict:
    return {
        "text": story.text,
        "acceptance_criteria": list(story.acceptance_criteria or []),
        "review_status": story.review_status,
        "requirement_id": story.requirement_id,
        "plan_order": story.plan_order,
    }


def _task_snapshot(task: Task) -> dict:
    return {
        "title": task.title,
        "estimate_hours": task.estimate_hours,
        "review_status": task.review_status,
        "status": task.status,
        "story_id": task.story_id,
        "plan_order": task.plan_order,
        "is_critical": task.is_critical,
        "rationale": task.rationale,
    }


def _load_story(db: Session, project_id: int, story_id: int, *, lock: bool = False) -> UserStory:
    stmt = select(UserStory).where(UserStory.id == story_id, UserStory.project_id == project_id)
    if lock:
        stmt = stmt.with_for_update()
    story = db.scalar(stmt)
    if story is None:
        raise HTTPException(404, "User story not found")
    return story


def _load_task(db: Session, project_id: int, task_id: int, *, lock: bool = False) -> Task:
    stmt = select(Task).where(Task.id == task_id, Task.project_id == project_id)
    if lock:
        stmt = stmt.with_for_update()
    task = db.scalar(stmt)
    if task is None:
        raise HTTPException(404, "Task not found")
    return task


def _task_out(db: Session, task: Task) -> TaskOut:
    out = TaskOut.model_validate(task)
    out.depends_on = sorted(
        db.scalars(
            select(TaskDependency.depends_on_task_id).where(TaskDependency.task_id == task.id)
        )
    )
    return out


def _trace_id(db: Session, run_id: int | None) -> str | None:
    return db.scalar(select(AgentRun.trace_id).where(AgentRun.id == run_id)) if run_id else None


def _apply_story_decision(
    db: Session, project_id: int, user: User, story: UserStory, *, decision: str,
    comment: str | None, before: dict | None = None, bulk: bool = False,
) -> None:
    """The shared PM-decision write for a story (caller owns the commit)."""
    before = before if before is not None else _story_snapshot(story)
    story.review_status = "rejected" if decision == "rejected" else "approved"
    story.locked_by_pm = True
    story.pm_comment = comment or None
    story.reviewed_by = user.id
    story.reviewed_at = datetime.now(timezone.utc)
    db.add(
        Decision(
            project_id=project_id,
            subject_type="story",
            subject_id=story.id,
            decision=decision,
            reason=comment or None,
            decided_by=user.id,
        )
    )
    after = {**_story_snapshot(story), "project_id": project_id, "comment": comment or None}
    if bulk:
        after["bulk"] = True
    write_audit(db, user_actor(user), f"story.{decision}", "story", story.id, before=before, after=after)


def _apply_task_decision(
    db: Session, project_id: int, user: User, task: Task, *, decision: str,
    comment: str | None, before: dict | None = None, bulk: bool = False, cascade: str | None = None,
) -> None:
    """The shared PM-decision write for a task (caller owns the commit)."""
    before = before if before is not None else _task_snapshot(task)
    task.review_status = "rejected" if decision == "rejected" else "approved"
    task.locked_by_pm = True
    task.pm_comment = comment or None
    task.reviewed_by = user.id
    task.reviewed_at = datetime.now(timezone.utc)
    db.add(
        Decision(
            project_id=project_id,
            subject_type="task",
            subject_id=task.id,
            decision=decision,
            reason=comment or None,
            decided_by=user.id,
        )
    )
    after = {**_task_snapshot(task), "project_id": project_id, "comment": comment or None}
    if bulk:
        after["bulk"] = True
    if cascade:
        after["cascade"] = cascade
    write_audit(db, user_actor(user), f"task.{decision}", "task", task.id, before=before, after=after)


def _cascade_reject_story_tasks(db: Session, project_id: int, story: UserStory, *, comment: str | None, user: User) -> None:
    """Rejecting a story rejects its still-draft child tasks in the same transaction.

    Tasks the PM already decided on are never touched.
    """
    children = list(db.scalars(
        select(Task).where(Task.story_id == story.id, Task.review_status == "draft").with_for_update()
    ))
    for task in children:
        _apply_task_decision(
            db, project_id, user, task, decision="rejected", comment=comment,
            bulk=True, cascade=f"story:{story.id}",
        )


@router.get("/stories", response_model=list[StoryOut])
def list_stories(
    project_id: int,
    review_status: ReviewStatus | None = None,
    requirement_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("PM")),
):
    get_project_or_404(db, project_id)
    stmt = select(UserStory).where(UserStory.project_id == project_id)
    if review_status is not None:
        stmt = stmt.where(UserStory.review_status == review_status)
    if requirement_id is not None:
        stmt = stmt.where(UserStory.requirement_id == requirement_id)
    return list(db.scalars(stmt.order_by(UserStory.id)))


@router.get("/stories/{story_id}", response_model=StoryOut)
def get_story(
    project_id: int,
    story_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("PM")),
):
    get_project_or_404(db, project_id)
    return _load_story(db, project_id, story_id)


@router.post("/stories/decision", response_model=list[StoryOut])
def decide_stories_bulk(
    project_id: int,
    body: BulkPlanDecision,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("PM")),
):
    """One decision for many stories (plan-level review). One transaction for the whole batch."""
    get_project_or_404(db, project_id)
    wanted = list(dict.fromkeys(body.ids))
    rows = {
        s.id: s
        for s in db.scalars(
            select(UserStory)
            .where(UserStory.project_id == project_id, UserStory.id.in_(wanted))
            .with_for_update()
        )
    }
    missing = [sid for sid in wanted if sid not in rows]
    if missing:
        raise HTTPException(404, f"Story(s) not found in this project: {missing}")

    updated: list[UserStory] = []
    for sid in wanted:
        story = rows[sid]
        _apply_story_decision(db, project_id, user, story, decision=body.decision, comment=body.comment, bulk=True)
        if body.decision == "rejected":
            _cascade_reject_story_tasks(db, project_id, story, comment=body.comment, user=user)
        updated.append(story)
    db.commit()
    for story in updated:
        db.refresh(story)
    return updated


@router.post("/stories/{story_id}/decision", response_model=StoryOut)
def decide_story(
    project_id: int,
    story_id: int,
    body: StoryDecision,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("PM")),
):
    get_project_or_404(db, project_id)
    story = _load_story(db, project_id, story_id, lock=True)
    before = _story_snapshot(story)

    if body.decision == "modified":
        if body.text is not None:
            story.text = body.text
        if body.acceptance_criteria is not None:
            story.acceptance_criteria = list(body.acceptance_criteria)

    _apply_story_decision(db, project_id, user, story, decision=body.decision, comment=body.comment, before=before)
    if body.decision == "rejected":
        _cascade_reject_story_tasks(db, project_id, story, comment=body.comment, user=user)

    run_id = story.run_id
    db.commit()
    send_pm_score(
        trace_id=_trace_id(db, run_id),
        decision=body.decision,
        subject_type="story",
        subject_id=story.id,
    )
    db.refresh(story)
    return story


@router.get("/tasks", response_model=list[TaskOut])
def list_tasks(
    project_id: int,
    review_status: ReviewStatus | None = None,
    story_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("PM")),
):
    get_project_or_404(db, project_id)
    stmt = select(Task).where(Task.project_id == project_id)
    if review_status is not None:
        stmt = stmt.where(Task.review_status == review_status)
    if story_id is not None:
        stmt = stmt.where(Task.story_id == story_id)
    return [_task_out(db, t) for t in db.scalars(stmt.order_by(Task.id))]


@router.get("/tasks/{task_id}", response_model=TaskOut)
def get_task(
    project_id: int,
    task_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("PM")),
):
    get_project_or_404(db, project_id)
    return _task_out(db, _load_task(db, project_id, task_id))


@router.post("/tasks/decision", response_model=list[TaskOut])
def decide_tasks_bulk(
    project_id: int,
    body: BulkPlanDecision,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("PM")),
):
    """One decision for many tasks (plan-level review). One transaction for the whole batch."""
    get_project_or_404(db, project_id)
    wanted = list(dict.fromkeys(body.ids))
    rows = {
        t.id: t
        for t in db.scalars(
            select(Task).where(Task.project_id == project_id, Task.id.in_(wanted)).with_for_update()
        )
    }
    missing = [tid for tid in wanted if tid not in rows]
    if missing:
        raise HTTPException(404, f"Task(s) not found in this project: {missing}")

    updated: list[Task] = []
    for tid in wanted:
        task = rows[tid]
        _apply_task_decision(db, project_id, user, task, decision=body.decision, comment=body.comment, bulk=True)
        updated.append(task)
    db.commit()
    return [_task_out(db, t) for t in updated]


@router.post("/tasks/{task_id}/decision", response_model=TaskOut)
def decide_task(
    project_id: int,
    task_id: int,
    body: TaskDecision,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("PM")),
):
    get_project_or_404(db, project_id)
    task = _load_task(db, project_id, task_id, lock=True)
    before = _task_snapshot(task)

    if body.decision == "modified":
        if body.title is not None:
            task.title = body.title
        if body.estimate_hours is not None:
            task.estimate_hours = body.estimate_hours

    _apply_task_decision(db, project_id, user, task, decision=body.decision, comment=body.comment, before=before)

    run_id = task.run_id
    db.commit()
    send_pm_score(
        trace_id=_trace_id(db, run_id),
        decision=body.decision,
        subject_type="task",
        subject_id=task.id,
    )
    db.refresh(task)
    return _task_out(db, task)


@router.get("/planning/coverage", response_model=CoverageOut)
def get_coverage(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("PM")),
):
    get_project_or_404(db, project_id)
    return planning_coverage(db, project_id)
