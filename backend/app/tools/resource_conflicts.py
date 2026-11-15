"""detect_resource_conflicts: deterministic, per-working-day load check.

Two kinds of conflict:
* overallocation        - on some days a person's tasks demand more hours/day than
                          their capacity. Tasks without estimate_hours count as
                          100% allocation, so any overlap of two such tasks conflicts.
* outside_availability  - a task is scheduled on days outside the assignee's
                          available_from / available_to.
Consecutive days with the same set of overlapping tasks are merged in one window.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Sequence

from app.schemas.scheduling import (
    ResourceConflict,
    ScheduledTask,
    ScheduleResult,
    TeamMemberInput,
    WorkCalendar,
)
from app.tools._load import (
    DEFAULT_HOURS_PER_DAY,
    EPS,
    daily_capacity,
    is_available,
    task_daily_load,
)


def _runs(days: list[int]) -> list[tuple[int, int]]:
    """[0,1,2,5,6] -> [(0,2),(5,6)]"""
    out: list[tuple[int, int]] = []
    for d in days:
        if out and out[-1][1] == d - 1:
            out[-1] = (out[-1][0], d)
        else:
            out.append((d, d))
    return out


def _overallocations(
    schedule: ScheduleResult, assignee: str, tasks: list[ScheduledTask], cap: float
) -> list[ResourceConflict]:
    wd = schedule.working_days
    found: list[ResourceConflict] = []
    cur: dict | None = None

    def flush() -> None:
        nonlocal cur
        if cur is None:
            return
        peak = cur["peak"]
        found.append(
            ResourceConflict(
                kind="overallocation",
                assignee_id=assignee,
                task_ids=cur["key"],
                start_date=wd[cur["start"]],
                end_date=wd[cur["end"]],
                working_days=cur["end"] - cur["start"] + 1,
                peak_load_hours_per_day=round(peak, 2),
                capacity_hours_per_day=round(cap, 2),
                overload_ratio=round(peak / cap, 2) if cap > EPS else None,
            )
        )
        cur = None

    for d in range(min(t.es for t in tasks), max(t.ef for t in tasks)):
        active = [t for t in tasks if t.es <= d < t.ef]
        load = sum(task_daily_load(t, cap) for t in active)
        over = bool(active) and (load > cap + EPS or cap <= EPS)
        if not over:
            flush()
            continue
        key = tuple(sorted(t.id for t in active))
        if cur is not None and cur["key"] == key and cur["end"] == d - 1:
            cur["end"] = d
            cur["peak"] = max(cur["peak"], load)
        else:
            flush()
            cur = {"key": key, "start": d, "end": d, "peak": load}
    flush()
    return found


def _availability(
    schedule: ScheduleResult, assignee: str, tasks: list[ScheduledTask], member: TeamMemberInput
) -> list[ResourceConflict]:
    wd = schedule.working_days
    found: list[ResourceConflict] = []
    for t in tasks:
        bad = [d for d in range(t.es, t.ef) if not is_available(member, wd[d])]
        for a, b in _runs(bad):
            found.append(
                ResourceConflict(
                    kind="outside_availability",
                    assignee_id=assignee,
                    task_ids=(t.id,),
                    start_date=wd[a],
                    end_date=wd[b],
                    working_days=b - a + 1,
                )
            )
    return found


def detect_resource_conflicts(
    schedule: ScheduleResult,
    members: Sequence[TeamMemberInput] = (),
    calendar: WorkCalendar | None = None,
    default_hours_per_day: float = DEFAULT_HOURS_PER_DAY,
) -> list[ResourceConflict]:
    cal = calendar or WorkCalendar()
    by_member = {m.id: m for m in members}
    by_assignee: dict[str, list[ScheduledTask]] = defaultdict(list)
    for t in schedule.tasks:
        if t.assignee_id is not None and t.duration_days > 0:
            by_assignee[t.assignee_id].append(t)

    conflicts: list[ResourceConflict] = []
    for assignee in sorted(by_assignee):
        tasks = by_assignee[assignee]
        member = by_member.get(assignee)
        cap = daily_capacity(member, cal, default_hours_per_day)
        conflicts += _overallocations(schedule, assignee, tasks, cap)
        if member is not None:
            conflicts += _availability(schedule, assignee, tasks, member)
    conflicts.sort(key=lambda c: (c.start_date, c.assignee_id, c.kind, c.task_ids))
    return conflicts
