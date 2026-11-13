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
    }


def _task_snapshot(task: Task) -> dict:
    return {
        "title": task.title,
        "estimate_hours": task.estimate_hours,
        "review_status": task.review_status,
        "status": task.status,
        "story_id": task.story_id,
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

    story.review_status = "rejected" if body.decision == "rejected" else "approved"
    story.locked_by_pm = True
    story.pm_comment = body.comment or None
    story.reviewed_by = user.id
    story.reviewed_at = datetime.now(timezone.utc)

    db.add(
        Decision(
            project_id=project_id,
            subject_type="story",
            subject_id=story.id,
            decision=body.decision,
            reason=body.comment or None,
            decided_by=user.id,
        )
    )
    write_audit(
        db,
        user_actor(user),
        f"story.{body.decision}",
        "story",
        story.id,
        before=before,
        after={**_story_snapshot(story), "project_id": project_id, "comment": body.comment or None},
    )
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

    task.review_status = "rejected" if body.decision == "rejected" else "approved"
    task.locked_by_pm = True
    task.pm_comment = body.comment or None
    task.reviewed_by = user.id
    task.reviewed_at = datetime.now(timezone.utc)

    db.add(
        Decision(
            project_id=project_id,
            subject_type="task",
            subject_id=task.id,
            decision=body.decision,
            reason=body.comment or None,
            decided_by=user.id,
        )
    )
    write_audit(
        db,
        user_actor(user),
        f"task.{body.decision}",
        "task",
        task.id,
        before=before,
        after={**_task_snapshot(task), "project_id": project_id, "comment": body.comment or None},
    )
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
