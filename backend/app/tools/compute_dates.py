"""compute_dates: deterministic CPM scheduling (pure code, no LLM).

Forward pass  -> earliest start/finish (es/ef)
Backward pass -> latest start/finish (ls/lf)
float = ls - es ; critical <=> float == 0

Resources are NOT considered here (that is detect_resource_conflicts' job);
the schedule is the dependency-driven, "everyone is available" plan.
"""
from __future__ import annotations

import heapq
from bisect import bisect_left
from datetime import date, timedelta
from typing import Sequence

from app.schemas.scheduling import ScheduledTask, ScheduleResult, TaskInput, WorkCalendar
from app.tools.scheduling_errors import (
    CycleError,
    DuplicateTaskError,
    InvalidInputError,
    MissingDurationError,
    SelfDependencyError,
    UnknownDependencyError,
)

_ONE_DAY = timedelta(days=1)
MAX_HORIZON_DAYS = 3650  # not_before can't be more than ~10y after the start


class WorkdayIndex:
    """Maps working-day offsets <-> calendar dates (lazy, deterministic)."""

    def __init__(self, start: date, calendar: WorkCalendar):
        self._cal = calendar
        self._cursor = start
        self._days: list[date] = []
        self._append_next()
        self.first: date = self._days[0]

    def _append_next(self) -> None:
        while not self._cal.is_working_day(self._cursor):
            self._cursor += _ONE_DAY
        self._days.append(self._cursor)
        self._cursor += _ONE_DAY

    def date_at(self, n: int) -> date:
        while len(self._days) <= n:
            self._append_next()
        return self._days[n]

    def offset_of(self, target: date) -> int:
        """Number of working days strictly before ``target`` (non-working target
        maps to the next working day's offset)."""
        if target <= self.first:
            return 0
        while self._days[-1] < target:
            self._append_next()
        return bisect_left(self._days, target)


def _validate(tasks: Sequence[TaskInput]) -> dict[str, TaskInput]:
    by_id: dict[str, TaskInput] = {}
    for t in tasks:
        if t.id in by_id:
            raise DuplicateTaskError(f"Duplicate task id: {t.id}")
        by_id[t.id] = t
    for t in tasks:
        if t.duration_days is None:
            raise MissingDurationError(f"Task {t.id} has no duration_days (estimate it first)")
        for dep in t.depends_on:
            if dep == t.id:
                raise SelfDependencyError(f"Task {t.id} depends on itself")
            if dep not in by_id:
                raise UnknownDependencyError(f"Task {t.id} depends on unknown task {dep}")
    return by_id


def _topological_order(tasks: Sequence[TaskInput]) -> list[str]:
    """Kahn's algorithm; ties broken by input order -> deterministic."""
    pos = {t.id: i for i, t in enumerate(tasks)}
    indeg = {t.id: len(t.depends_on) for t in tasks}
    succ: dict[str, list[str]] = {t.id: [] for t in tasks}
    for t in tasks:
        for d in t.depends_on:
            succ[d].append(t.id)

    heap = [pos[i] for i, n in indeg.items() if n == 0]
    heapq.heapify(heap)
    order: list[str] = []
    while heap:
        tid = tasks[heapq.heappop(heap)].id
        order.append(tid)
        for s in succ[tid]:
            indeg[s] -= 1
            if indeg[s] == 0:
                heapq.heappush(heap, pos[s])

    if len(order) < len(tasks):
        done = set(order)
        remaining = {t.id: t for t in tasks if t.id not in done}
        # every remaining node has a remaining predecessor: walk back until a repeat
        cur = next(t.id for t in tasks if t.id in remaining)
        chain: list[str] = []
        while cur not in chain:
            chain.append(cur)
            cur = next(d for d in remaining[cur].depends_on if d in remaining)
        cycle = chain[chain.index(cur):]
        cycle.reverse()  # predecessor -> successor order
        raise CycleError(cycle + [cycle[0]])
    return order


def compute_dates(
    tasks: Sequence[TaskInput],
    project_start: date,
    calendar: WorkCalendar | None = None,
) -> ScheduleResult:
    cal = calendar or WorkCalendar()
    by_id = _validate(tasks)
    idx = WorkdayIndex(project_start, cal)
    order = _topological_order(tasks)

    # ---- forward pass
    es: dict[str, int] = {}
    ef: dict[str, int] = {}
    for tid in order:
        t = by_id[tid]
        lower = 0
        if t.not_before is not None:
            if (t.not_before - idx.first).days > MAX_HORIZON_DAYS:
                raise InvalidInputError(f"Task {tid}: not_before is too far in the future")
            lower = idx.offset_of(t.not_before)
        start = max([ef[d] for d in t.depends_on] + [lower])
        es[tid] = start
        ef[tid] = start + t.duration_days  # type: ignore[operator]
    total = max(ef.values(), default=0)

    # ---- backward pass
    succ: dict[str, list[str]] = {t.id: [] for t in tasks}
    for t in tasks:
        for d in t.depends_on:
            succ[d].append(t.id)
    ls: dict[str, int] = {}
    lf: dict[str, int] = {}
    for tid in reversed(order):
        lf[tid] = min([ls[s] for s in succ[tid]], default=total)
        ls[tid] = lf[tid] - by_id[tid].duration_days  # type: ignore[operator]

    float_: dict[str, int] = {tid: ls[tid] - es[tid] for tid in order}
    critical = {tid for tid, f in float_.items() if f == 0}

    # ---- representative critical chain (walk back from a finishing critical task)
    chain: list[str] = []
    if critical:
        topo_pos = {tid: i for i, tid in enumerate(order)}
        # latest critical task finishing at `total` (so zero-duration tails extend the chain)
        cur = max((t.id for t in tasks if t.id in critical and ef[t.id] == total), key=topo_pos.__getitem__)
        chain.append(cur)
        while True:
            preds = [p for p in by_id[cur].depends_on if p in critical and ef[p] == es[cur]]
            if not preds:
                break
            cur = preds[0]
            chain.append(cur)
        chain.reverse()

    # ---- dates
    scheduled: list[ScheduledTask] = []
    for t in tasks:
        d = t.duration_days or 0
        if d == 0:  # milestone-like: happens at the end of the previous working day
            start_date = end_date = idx.date_at(max(es[t.id] - 1, 0))
        else:
            start_date, end_date = idx.date_at(es[t.id]), idx.date_at(ef[t.id] - 1)
        scheduled.append(
            ScheduledTask(
                id=t.id, title=t.title, assignee_id=t.assignee_id, duration_days=d,
                estimate_hours=t.estimate_hours, start_date=start_date, end_date=end_date,
                es=es[t.id], ef=ef[t.id], ls=ls[t.id], lf=lf[t.id],
                total_float=float_[t.id], is_critical=t.id in critical,
            )
        )

    working_days = tuple(idx.date_at(i) for i in range(total))
    return ScheduleResult(
        project_start=idx.first,
        project_end=idx.date_at(total - 1) if total > 0 else idx.first,
        total_duration_days=total,
        tasks=tuple(scheduled),
        critical_path=tuple(chain),
        critical_task_ids=tuple(t.id for t in tasks if t.id in critical),
        working_days=working_days,
    )
