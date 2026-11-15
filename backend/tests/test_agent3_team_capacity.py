"""Deterministic tests: get_team_capacity."""
from datetime import date

import pytest

from app.schemas.scheduling import TaskInput, TeamMemberInput, WorkCalendar
from app.tools.compute_dates import compute_dates
from app.tools.scheduling_errors import InvalidInputError
from app.tools.team_capacity import get_team_capacity

SUN, THU, SAT = date(2026, 11, 15), date(2026, 11, 19), date(2026, 11, 21)


def M(id, hours=40, **kw):
    return TeamMemberInput(id=id, capacity_hours_per_week=hours, **kw)


def T(id, d, a=None, **kw):
    return TaskInput(id=id, duration_days=d, assignee_id=a, **kw)


def one(report):
    return report.members[0]


def test_full_week_40h():
    m = one(get_team_capacity([M("a", 40)], SUN, THU))
    assert (m.available_days, m.hours_per_day, m.available_hours) == (5, 8.0, 40.0)


def test_weekend_days_in_window_do_not_add_capacity():
    assert one(get_team_capacity([M("a", 40)], SUN, SAT)).available_hours == 40.0


def test_part_time():
    m = one(get_team_capacity([M("a", 20)], SUN, THU))
    assert m.hours_per_day == 4.0 and m.available_hours == 20.0


def test_availability_window_limits_days():
    m = one(get_team_capacity([M("a", 40, available_from=date(2026, 11, 17))], SUN, THU))
    assert m.available_days == 3 and m.available_hours == 24.0


def test_holiday_reduces_capacity():
    cal = WorkCalendar(holidays=frozenset({date(2026, 11, 16)}))
    assert one(get_team_capacity([M("a", 40)], SUN, THU, cal)).available_days == 4


def test_totals_across_members():
    r = get_team_capacity([M("a", 40), M("b", 20)], SUN, THU)
    assert r.total_available_hours == 60.0 and r.total_assigned_hours == 0.0


def test_assigned_hours_from_estimates_and_utilization():
    s = compute_dates([T("A", 2, "a", estimate_hours=16)], SUN)
    m = one(get_team_capacity([M("a", 40)], SUN, THU, schedule=s))
    assert m.assigned_hours == 16.0 and m.utilization == 0.4 and not m.is_overloaded


def test_tasks_without_hours_assume_full_allocation():
    s = compute_dates([T("A", 3, "a")], SUN)
    m = one(get_team_capacity([M("a", 40)], SUN, THU, schedule=s))
    assert m.assigned_hours == 24.0 and m.utilization == 0.6


def test_overloaded_member_flagged():
    s = compute_dates([T("A", 5, "a", estimate_hours=60)], SUN)
    m = one(get_team_capacity([M("a", 40)], SUN, THU, schedule=s))
    assert m.assigned_hours == 60.0 and m.is_overloaded and m.utilization == 1.5


def test_task_partially_outside_window_counts_only_inside():
    s = compute_dates([T("A", 5, "a", estimate_hours=50)], SUN)  # 10h/day, Sun..Thu
    m = one(get_team_capacity([M("a", 40)], SUN, date(2026, 11, 16), schedule=s))  # Sun+Mon
    assert m.assigned_hours == 20.0


def test_zero_available_hours():
    s = compute_dates([T("A", 1, "a")], SUN)
    m = one(get_team_capacity([M("a", 0)], SUN, THU, schedule=s))
    assert m.available_hours == 0 and m.utilization is None
    assert m.is_overloaded is False  # 0 assigned hours: tasks without hours load = cap = 0
    s2 = compute_dates([T("A", 1, "a", estimate_hours=8)], SUN)
    m2 = one(get_team_capacity([M("a", 0)], SUN, THU, schedule=s2))
    assert m2.is_overloaded and m2.utilization is None


def test_unknown_assignees_reported():
    s = compute_dates([T("A", 1, "ghost"), T("B", 1, "a")], SUN)
    assert get_team_capacity([M("a")], SUN, THU, schedule=s).unknown_assignees == ("ghost",)


def test_no_members():
    r = get_team_capacity([], SUN, THU)
    assert r.members == () and r.total_available_hours == 0


def test_single_day_window():
    assert one(get_team_capacity([M("a", 40)], SUN, SUN)).available_hours == 8.0


def test_window_on_weekend_only_has_zero_capacity():
    assert one(get_team_capacity([M("a")], date(2026, 11, 20), SAT)).available_hours == 0


@pytest.mark.parametrize("kind", ["reversed", "dup", "toolong"])
def test_invalid_inputs(kind):
    with pytest.raises(InvalidInputError):
        if kind == "reversed":
            get_team_capacity([M("a")], THU, SUN)
        elif kind == "dup":
            get_team_capacity([M("a"), M("a")], SUN, THU)
        else:
            get_team_capacity([M("a")], SUN, date(2060, 1, 1))


def test_member_schema_validation():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        M("a", -5)
    with pytest.raises(ValidationError):
        M("a", 40, available_from=THU, available_to=SUN)
