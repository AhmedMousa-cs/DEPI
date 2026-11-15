"""Shared helpers for capacity / conflict maths (pure)."""
from __future__ import annotations

from app.schemas.scheduling import ScheduledTask, TeamMemberInput, WorkCalendar

EPS = 1e-9
DEFAULT_HOURS_PER_DAY = 8.0


def daily_capacity(
    member: TeamMemberInput | None,
    calendar: WorkCalendar,
    default_hours_per_day: float = DEFAULT_HOURS_PER_DAY,
) -> float:
    """Hours a person can work per working day = weekly capacity / working days per week."""
    if member is None:
        return default_hours_per_day
    return member.capacity_hours_per_week / calendar.working_days_per_week


def task_daily_load(task: ScheduledTask, cap_day: float) -> float:
    """Hours/day a task demands. With estimate_hours: spread evenly over its
    duration. Without: assume the assignee is fully allocated (= cap_day)."""
    if task.duration_days <= 0:
        return 0.0
    if task.estimate_hours is not None:
        return task.estimate_hours / task.duration_days
    return cap_day


def is_available(member: TeamMemberInput | None, d) -> bool:
    if member is None:
        return True
    if member.available_from and d < member.available_from:
        return False
    if member.available_to and d > member.available_to:
        return False
    return True
