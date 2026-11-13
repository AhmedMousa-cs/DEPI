"""Agent-side persistence and deterministic checks for Agent 2 (stories & tasks).

Rules:
- Only APPROVED requirements are visible to Agent 2; a draft/rejected id is rejected by the code.
- Drafts only: this module has no approval parameter (approval is in app/api/planning.py).
- Re-running Agent 2 never overwrites anything the PM decided on (locked_by_pm / review_status).
- Dependency cycles and dangling references are rejected before a write.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.db.models import Requirement, Task, TaskDependency, UserStory
from app.schemas.planning import PlanningOutput, find_cycle
from app.services.quotes import normalize

AGENT_NAME = "agent2_plan"


class PlanValidationError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


@dataclass(frozen=True)
class SavedPlan:
    stories: list[UserStory] = field(default_factory=list)
    tasks: list[Task] = field(default_factory=list)
    dependencies: int = 0
    skipped_protected: int = 0


def get_approved_requirements(db: Session, project_id: int) -> list[dict]:
    """The Agent 2 read tool. Drafts and rejected items are deliberately invisible."""
    rows = db.scalars(
        select(Requirement)
        .where(Requirement.project_id == project_id, Requirement.status == "approved")
        .order_by(Requirement.id)
    )
    return [
        {"id": r.id, "type": r.type, "text": r.text, "priority": r.priority}
        for r in rows
    ]


def validate_plan(output: PlanningOutput, approved_ids: set[int]) -> None:
    """Extra checks that need the database (Pydantic already blocks cycles and dangling refs)."""
    errors: list[str] = []
    used = {s.requirement_id for s in output.stories}
    unknown = sorted(used - approved_ids)
    if unknown:
        errors.append(f"stories reference requirements that are not approved: {unknown}")

    seen_stories: set[tuple[int, str]] = set()
    for story in output.stories:
        key = (story.requirement_id, normalize(story.text))
        if key in seen_stories:
            errors.append(f"duplicate story for requirement {story.requirement_id}: {story.text[:60]!r}")
        seen_stories.add(key)

    seen_tasks: set[tuple[str, str]] = set()
    for task in output.tasks:
        key = (task.story_ref, normalize(task.title))
        if key in seen_tasks:
            errors.append(f"duplicate task under {task.story_ref}: {task.title[:60]!r}")
        seen_tasks.add(key)

    if errors:
        raise PlanValidationError(errors)


def save_draft_plan(
    db: Session,
    *,
    project_id: int,
    output: PlanningOutput,
    run_id: int | None = None,
    agent: str = AGENT_NAME,
) -> SavedPlan:
    """Write Agent 2 output as drafts, protecting every PM decision already made."""
    approved_ids = set(
        db.scalars(
            select(Requirement.id).where(
                Requirement.project_id == project_id, Requirement.status == "approved"
            )
        )
    )
    validate_plan(output, approved_ids)

    existing_stories = list(
        db.scalars(select(UserStory).where(UserStory.project_id == project_id))
    )
    existing_tasks = list(db.scalars(select(Task).where(Task.project_id == project_id)))

    def story_protected(s: UserStory) -> bool:
        return s.locked_by_pm or s.review_status != "draft"

    def task_protected(t: Task) -> bool:
        # "todo" + draft means untouched by anyone; anything else is in use.
        return t.locked_by_pm or t.review_status != "draft" or t.status != "todo"

    protected_story_ids = {s.id for s in existing_stories if story_protected(s)}
    # A task under a PM-decided story is kept too: re-running must never rewrite a decided plan.
    protected_task_ids = {
        t.id
        for t in existing_tasks
        if task_protected(t) or t.story_id in protected_story_ids
    }

    delete_stories = [
        s
        for s in existing_stories
        if s.id not in protected_story_ids
        and not any(t.story_id == s.id and t.id in protected_task_ids for t in existing_tasks)
    ]
    delete_story_ids = {s.id for s in delete_stories}
    delete_tasks = [
        t for t in existing_tasks if t.id not in protected_task_ids and t.story_id in delete_story_ids
    ]
    deleted_task_ids = {t.id for t in delete_tasks}

    for task in delete_tasks:
        db.delete(task)
    for story in delete_stories:
        db.delete(story)
    db.flush()

    protected_stories = {
        (s.requirement_id, normalize(s.text)): s
        for s in existing_stories
        if s.id not in delete_story_ids
    }
    protected_tasks = {
        (t.story_id, normalize(t.title)): t for t in existing_tasks if t.id not in deleted_task_ids
    }
    # Tasks that survived, grouped by story and kept in a stable order (id). Used for positional
    # matching so a PM-edited title never causes a duplicate.
    kept_tasks_by_story: dict[int, list[Task]] = {}
    for t in existing_tasks:
        if t.id not in deleted_task_ids:
            kept_tasks_by_story.setdefault(t.story_id, []).append(t)
    for kept in kept_tasks_by_story.values():
        kept.sort(key=lambda t: t.id)

    skipped = 0
    story_by_ref: dict[str, UserStory] = {}
    created_stories: list[UserStory] = []
    for item in output.stories:
        key = (item.requirement_id, normalize(item.text))
        existing = protected_stories.get(key)
        if existing is not None:
            story_by_ref[item.ref] = existing
            skipped += 1
            continue
        story = UserStory(
            project_id=project_id,
            requirement_id=item.requirement_id,
            text=item.text,
            acceptance_criteria=list(item.acceptance_criteria),
            confidence=item.confidence,
            review_status="draft",
            locked_by_pm=False,
            run_id=run_id,
            created_by_agent=agent,
        )
        db.add(story)
        db.flush()
        created_stories.append(story)
        story_by_ref[item.ref] = story
        protected_stories[key] = story

    created_tasks: list[Task] = []
    task_id_by_ref: dict[str, int] = {}
    used_index_by_story: dict[int, int] = {}
    for item in output.tasks:
        story = story_by_ref[item.story_ref]
        kept = kept_tasks_by_story.get(story.id, [])
        index = used_index_by_story.get(story.id, 0)
        if index < len(kept):  # a surviving task for this story: keep it, never rewrite it
            existing = kept[index]
            used_index_by_story[story.id] = index + 1
            task_id_by_ref[item.ref] = existing.id
            skipped += 1
            continue
        key = (story.id, normalize(item.title))
        existing = protected_tasks.get(key)
        if existing is not None:
            task_id_by_ref[item.ref] = existing.id
            skipped += 1
            continue
        task = Task(
            project_id=project_id,
            story_id=story.id,
            title=item.title,
            estimate_hours=item.estimate_hours,
            status="todo",
            progress_pct=0,
            is_critical=False,
            confidence=item.confidence,
            review_status="draft",
            locked_by_pm=False,
            run_id=run_id,
            created_by_agent=agent,
        )
        db.add(task)
        db.flush()
        created_tasks.append(task)
        task_id_by_ref[item.ref] = task.id
        protected_tasks[key] = task

    existing_edges = set(
        db.execute(
            select(TaskDependency.task_id, TaskDependency.depends_on_task_id).where(
                TaskDependency.task_id.in_([t.id for t in [*existing_tasks, *created_tasks]] or [0])
            )
        ).all()
    )
    dependencies = 0
    for item in output.tasks:
        task_id = task_id_by_ref[item.ref]
        for dep_ref in item.depends_on:
            dep_id = task_id_by_ref.get(dep_ref)
            if dep_id is None or dep_id == task_id:
                continue
            edge = (task_id, dep_id)
            if edge in existing_edges:
                continue
            db.add(TaskDependency(task_id=task_id, depends_on_task_id=dep_id))
            existing_edges.add(edge)
            dependencies += 1
    db.flush()

    write_audit(
        db,
        f"agent:{agent}",
        "planning.drafts_saved",
        "project",
        project_id,
        after={
            "stories_created": len(created_stories),
            "tasks_created": len(created_tasks),
            "dependencies_created": dependencies,
            "skipped_protected": skipped,
            "run_id": run_id,
        },
    )
    return SavedPlan(created_stories, created_tasks, dependencies, skipped)


def planning_coverage(db: Session, project_id: int) -> dict:
    approved_ids = set(
        db.scalars(
            select(Requirement.id).where(
                Requirement.project_id == project_id, Requirement.status == "approved"
            )
        )
    )
    stories = list(db.scalars(select(UserStory).where(UserStory.project_id == project_id).order_by(UserStory.id)))
    tasks = list(db.scalars(select(Task).where(Task.project_id == project_id).order_by(Task.id)))
    task_ids = {t.id for t in tasks}
    edges = db.execute(
        select(TaskDependency.task_id, TaskDependency.depends_on_task_id).where(
            TaskDependency.task_id.in_(task_ids or {0}),
            TaskDependency.depends_on_task_id.in_(task_ids or {0}),
        )
    ).all()

    story_ids = {s.id for s in stories}
    requirements_with_story = {s.requirement_id for s in stories}
    story_has_task = {t.story_id for t in tasks}
    cycles: list[list[int]] = []
    adjacency: dict[str, list[str]] = {str(t.id): [] for t in tasks}
    for task_id, dep_id in edges:
        adjacency[str(task_id)].append(str(dep_id))
    cycle = find_cycle(adjacency)
    if cycle:
        cycles.append(sorted(int(x) for x in cycle))

    unreviewed_stories = sum(1 for s in stories if s.review_status == "draft")
    unreviewed_tasks = sum(1 for t in tasks if t.review_status == "draft")
    missing_story = sorted(approved_ids - requirements_with_story)
    missing_tasks = sorted(story_ids - story_has_task)
    missing_estimate = sorted(t.id for t in tasks if t.estimate_hours is None or t.estimate_hours <= 0)
    total_hours = round(sum(float(t.estimate_hours or 0) for t in tasks), 2)

    return {
        "approved_requirements": len(approved_ids),
        "requirements_without_story": missing_story,
        "stories": len(stories),
        "approved_stories": sum(1 for s in stories if s.review_status == "approved"),
        "unreviewed_stories": unreviewed_stories,
        "stories_without_tasks": missing_tasks,
        "tasks": len(tasks),
        "approved_tasks": sum(1 for t in tasks if t.review_status == "approved"),
        "unreviewed_tasks": unreviewed_tasks,
        "tasks_without_estimate": missing_estimate,
        "total_estimate_hours": total_hours,
        "dependency_cycles": cycles,
        "clean": not missing_story
        and not missing_tasks
        and not missing_estimate
        and not cycles
        and unreviewed_stories == 0
        and unreviewed_tasks == 0
        and len(stories) > 0,
    }
