"""get_team_capacity: how many hours the team can deliver in a window, and how
much of that the (optional) schedule already demands. Pure; the DB-reading
wrapper (members from team_members) is done by the caller."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Sequence

from app.schemas.scheduling import (
    MemberCapacity,
    ScheduleResult,
    TeamCapacityReport,
    TeamMemberInput,
    WorkCalendar,
)
from app.tools._load import EPS, daily_capacity, is_available, task_daily_load
from app.tools.scheduling_errors import InvalidInputError

MAX_WINDOW_DAYS = 3660


def get_team_capacity(
    members: Sequence[TeamMemberInput],
    window_start: date,
    window_end: date,
    calendar: WorkCalendar | None = None,
    schedule: ScheduleResult | None = None,
) -> TeamCapacityReport:
    cal = calendar or WorkCalendar()
    if window_end < window_start:
        raise InvalidInputError("window_end must be >= window_start")
    if (window_end - window_start).days > MAX_WINDOW_DAYS:
        raise InvalidInputError("window is too long")
    ids = [m.id for m in members]
    if len(set(ids)) != len(ids):
        raise InvalidInputError("Duplicate team member ids")

    window = [
        window_start + timedelta(days=i)
        for i in range((window_end - window_start).days + 1)
    ]
    working = [d for d in window if cal.is_working_day(d)]

    rows: list[MemberCapacity] = []
    for m in members:
        per_day = daily_capacity(m, cal)
        avail_days = sum(1 for d in working if is_available(m, d))
        avail_hours = avail_days * per_day

        assigned = 0.0
        if schedule is not None:
            for t in schedule.tasks:
                if t.assignee_id != m.id:
                    continue
                load = task_daily_load(t, per_day)
                assigned += load * sum(
                    1 for off in range(t.es, t.ef) if window_start <= schedule.working_days[off] <= window_end
                )
        util = round(assigned / avail_hours, 3) if avail_hours > EPS else None
        rows.append(
            MemberCapacity(
                member_id=m.id, name=m.name,
                capacity_hours_per_week=m.capacity_hours_per_week,
                hours_per_day=round(per_day, 2),
                available_days=avail_days,
                available_hours=round(avail_hours, 2),
                assigned_hours=round(assigned, 2),
                utilization=util,
                is_overloaded=assigned > avail_hours + EPS,
            )
        )

    unknown: tuple[str, ...] = ()
    if schedule is not None:
        known = set(ids)
        unknown = tuple(sorted({
            t.assignee_id for t in schedule.tasks
            if t.assignee_id is not None and t.assignee_id not in known
        }))
    return TeamCapacityReport(
        window_start=window_start, window_end=window_end, members=tuple(rows),
        total_available_hours=round(sum(r.available_hours for r in rows), 2),
        total_assigned_hours=round(sum(r.assigned_hours for r in rows), 2),
        unknown_assignees=unknown,
    )
