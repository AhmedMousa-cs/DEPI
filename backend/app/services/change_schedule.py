"""Deterministic schedule inputs for a task change and later plan rebuilds."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.agents.agent3_schedule import Agent3Output, run_agent3
from app.db.models import Task
from app.schemas.scheduling import EGYPT_CALENDAR, TaskInput, TeamMemberInput
from app.services.plan_versions import PlanInputs, build_snapshot, current_approved, load_plan_inputs
from app.tools.compute_dates import MAX_HORIZON_DAYS
from app.tools.scheduling_errors import InvalidInputError


def schedule_constraints(baseline: dict, task_ids: set[int], requested: list[dict] | None = None
                         ) -> tuple[dict[int, dict], dict[int, dict]]:
    """Carry forward published durations and exact date windows for still-approved tasks."""
    overrides = {
        row["task_id"]: {"task_id": row["task_id"], "duration_days": row["duration_days"]}
        for row in baseline.get("overrides", [])
        if row["task_id"] in task_ids and row.get("duration_days") is not None
    }
    for row in requested or []:
        overrides.setdefault(row["task_id"], {}).update(row)
    fixed_dates = {row["task_id"]: dict(row) for row in baseline.get("fixed_dates", []) if row["task_id"] in task_ids}
    return overrides, fixed_dates


def build_schedule_inputs(inputs: PlanInputs, overrides: dict[int, dict], fixed_dates: dict[int, dict]
                          ) -> tuple[list[TaskInput], list[TeamMemberInput]]:
    tasks = []
    for row in inputs.tasks:
        task_id = row["id"]
        override = overrides.get(task_id, {})
        duration = override.get("duration_days")
        fixed_start = None
        if window := fixed_dates.get(task_id):
            fixed_start = date.fromisoformat(window["start"])
            fixed_end = date.fromisoformat(window["end"])
            if fixed_end < fixed_start:
                raise InvalidInputError(f"Task {task_id}: fixed end must be on or after its fixed start")
            if not (EGYPT_CALENDAR.is_working_day(fixed_start) and EGYPT_CALENDAR.is_working_day(fixed_end)):
                raise InvalidInputError(f"Task {task_id}: both fixed dates must be working days")
            span = (fixed_end - fixed_start).days
            if span > MAX_HORIZON_DAYS:
                raise InvalidInputError(f"Task {task_id}: fixed date window is too long")
            fixed_duration = sum(EGYPT_CALENDAR.is_working_day(fixed_start + timedelta(days=day))
                                 for day in range(span + 1))
            if duration is not None and duration != fixed_duration:
                raise InvalidInputError(f"Task {task_id}: duration conflicts with its fixed dates")
            duration = fixed_duration
        tasks.append(TaskInput(
            id=task_id,
            title=row["title"],
            estimate_hours=row["estimate_hours"],
            duration_days=duration,
            assignee_id=override.get("assignee_member_id", row["assignee_member_id"]),
            depends_on=inputs.deps[task_id],
            fixed_start=fixed_start,
        ))
    members = [TeamMemberInput.model_validate(member) for member in inputs.members]
    return tasks, members


def build_change_snapshot(db: Session, project_id: int, task_id: int, after: dict) -> tuple[dict, Agent3Output]:
    """Preview a task change against the published project schedule, without persisting it."""
    published = current_approved(db, project_id)
    if published is None:
        raise InvalidInputError("An approved plan is required before a change can be scheduled")
    inputs = load_plan_inputs(db, project_id)
    task_ids = {row["id"] for row in inputs.tasks}
    if task_id not in task_ids:
        raise InvalidInputError("The change must target an approved task in this project")
    if inputs.dangling:
        raise InvalidInputError(f"Approved tasks depend on tasks that are not approved: {inputs.dangling[:5]}")
    assignee_id = after.get("assignee_member_id")
    if assignee_id is not None and assignee_id not in {member["id"] for member in inputs.members}:
        raise InvalidInputError("The assignee must be a member of this project's team")

    baseline = published.snapshot
    overrides, fixed_dates = schedule_constraints(baseline, task_ids)
    if "start" in after or "end" in after:
        task = db.get(Task, task_id)
        start = after.get("start", task.start.isoformat() if task.start else None)
        end = after.get("end", task.end.isoformat() if task.end else None)
        if start is None or end is None:
            raise InvalidInputError("A date change requires both a start and an end date")
        fixed_dates[task_id] = {"task_id": task_id, "start": start, "end": end}
        # This new PM date window replaces any older explicit duration for the same task.
        overrides.pop(task_id, None)

    proposed_fields = {field: after[field] for field in ("title", "estimate_hours", "assignee_member_id")
                       if field in after}
    proposed_tasks = [row | proposed_fields if row["id"] == task_id else row for row in inputs.tasks]
    tasks, members = build_schedule_inputs(replace(inputs, tasks=proposed_tasks), overrides, fixed_dates)
    project_start = date.fromisoformat(baseline["schedule"]["project_start"])
    output = run_agent3(tasks, members, project_start, calendar=EGYPT_CALENDAR)
    snapshot = build_snapshot(inputs=inputs, output=output, overrides=[overrides[key] for key in sorted(overrides)])
    snapshot["fixed_dates"] = [fixed_dates[key] for key in sorted(fixed_dates)]
    return snapshot, output
