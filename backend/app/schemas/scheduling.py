"""Pydantic schemas for Agent 3 (scheduling / resources).

Conventions
-----------
* Durations are whole *working days*.  Day offsets (``es``/``ef``/``ls``/``lf``)
  count working days from the first working day of the project.  ``ef`` and
  ``lf`` are exclusive: a task with ``es=0, duration=3`` occupies offsets 0,1,2.
* Ids are normalised to ``str`` so DB ints / UUIDs both work.
"""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_DURATION_DAYS = 3650


class _Base(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")


def _coerce_id(v):
    return None if v is None else str(v)


# --------------------------------------------------------------------------- inputs
class WorkCalendar(_Base):
    """Working-week definition. Default = Egypt (Fri + Sat weekend).

    ``weekend_days`` uses ``date.weekday()``: Mon=0 ... Sun=6.
    """

    weekend_days: frozenset[int] = frozenset({4, 5})
    holidays: frozenset[date] = frozenset()

    @field_validator("weekend_days")
    @classmethod
    def _check_weekend(cls, v: frozenset[int]) -> frozenset[int]:
        if any(d < 0 or d > 6 for d in v):
            raise ValueError("weekend_days must be in 0..6 (Mon=0 ... Sun=6)")
        if len(v) >= 7:
            raise ValueError("calendar must have at least one working day per week")
        return v

    def is_working_day(self, d: date) -> bool:
        return d.weekday() not in self.weekend_days and d not in self.holidays

    @property
    def working_days_per_week(self) -> int:
        return 7 - len(self.weekend_days)


EGYPT_CALENDAR = WorkCalendar()  # Sun-Thu
STANDARD_CALENDAR = WorkCalendar(weekend_days=frozenset({5, 6}))  # Mon-Fri


class TaskInput(_Base):
    id: str = Field(min_length=1)
    title: str = ""
    description: str = ""
    duration_days: int | None = Field(default=None, ge=0, le=MAX_DURATION_DAYS)
    estimate_hours: float | None = Field(default=None, ge=0)
    assignee_id: str | None = None
    depends_on: tuple[str, ...] = ()
    not_before: date | None = None  # "can't start before" constraint

    @field_validator("id", "assignee_id", mode="before")
    @classmethod
    def _ids(cls, v):
        return _coerce_id(v)

    @field_validator("depends_on", mode="before")
    @classmethod
    def _deps(cls, v):
        if v is None:
            return ()
        out: list[str] = []
        for x in v:
            s = str(x)
            if s not in out:
                out.append(s)
        return tuple(out)


class TeamMemberInput(_Base):
    id: str = Field(min_length=1)
    name: str = ""
    capacity_hours_per_week: float = Field(ge=0)
    available_from: date | None = None
    available_to: date | None = None

    @field_validator("id", mode="before")
    @classmethod
    def _id(cls, v):
        return _coerce_id(v)

    @model_validator(mode="after")
    def _range(self):
        if self.available_from and self.available_to and self.available_from > self.available_to:
            raise ValueError("available_from must be <= available_to")
        return self


# -------------------------------------------------------------------------- outputs
class ScheduledTask(_Base):
    id: str
    title: str = ""
    assignee_id: str | None = None
    duration_days: int
    estimate_hours: float | None = None
    start_date: date
    end_date: date  # inclusive; for 0-duration tasks == start_date
    es: int
    ef: int
    ls: int
    lf: int
    total_float: int
    is_critical: bool


class ScheduleResult(_Base):
    project_start: date  # first working day on/after the requested start
    project_end: date  # last working day of the project
    total_duration_days: int
    tasks: tuple[ScheduledTask, ...]  # same order as the input
    critical_path: tuple[str, ...]  # one representative chain, in order
    critical_task_ids: tuple[str, ...]  # every task with zero float
    working_days: tuple[date, ...]  # date of each working-day offset 0..total-1


class ResourceConflict(_Base):
    kind: Literal["overallocation", "outside_availability"]
    assignee_id: str
    task_ids: tuple[str, ...]
    start_date: date
    end_date: date
    working_days: int
    peak_load_hours_per_day: float | None = None
    capacity_hours_per_day: float | None = None
    overload_ratio: float | None = None


class MemberCapacity(_Base):
    member_id: str
    name: str = ""
    capacity_hours_per_week: float
    hours_per_day: float
    available_days: int
    available_hours: float
    assigned_hours: float
    utilization: float | None  # None when available_hours == 0
    is_overloaded: bool


class TeamCapacityReport(_Base):
    window_start: date
    window_end: date
    members: tuple[MemberCapacity, ...]
    total_available_hours: float
    total_assigned_hours: float
    unknown_assignees: tuple[str, ...] = ()
