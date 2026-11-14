"""Deterministic tests: compute_dates + validation edge cases.
Default calendar = Egypt (Sun-Thu working). 2026-11-15 is a Sunday."""
from datetime import date

import pytest
from pydantic import ValidationError

from app.schemas.scheduling import STANDARD_CALENDAR, TaskInput, WorkCalendar
from app.tools.compute_dates import compute_dates
from app.tools.scheduling_errors import (
    CycleError,
    DuplicateTaskError,
    InvalidInputError,
    MissingDurationError,
    SelfDependencyError,
    UnknownDependencyError,
)

SUN = date(2026, 11, 15)


def T(id, d, assignee=None, deps=(), **kw):
    return TaskInput(id=id, duration_days=d, assignee_id=assignee, depends_on=deps, **kw)


def by_id(res):
    return {t.id: t for t in res.tasks}


def test_sanity_calendar_assumption():
    assert SUN.weekday() == 6


def test_single_day_task():
    r = compute_dates([T("A", 1)], SUN)
    a = by_id(r)["A"]
    assert (a.start_date, a.end_date) == (SUN, SUN)
    assert r.total_duration_days == 1 and r.project_end == SUN


def test_three_day_task_spans_three_working_days():
    a = by_id(compute_dates([T("A", 3)], SUN))["A"]
    assert (a.start_date, a.end_date) == (date(2026, 11, 15), date(2026, 11, 17))


def test_dependency_chain_skips_fri_sat():
    r = compute_dates([T("A", 3), T("B", 4, deps=["A"])], SUN)
    t = by_id(r)
    assert (t["A"].start_date, t["A"].end_date) == (date(2026, 11, 15), date(2026, 11, 17))
    # B: Wed 18, Thu 19, (Fri 20, Sat 21 off), Sun 22, Mon 23
    assert (t["B"].start_date, t["B"].end_date) == (date(2026, 11, 18), date(2026, 11, 23))
    assert r.total_duration_days == 7 and r.project_end == date(2026, 11, 23)


def test_independent_tasks_start_together():
    t = by_id(compute_dates([T("A", 3), T("B", 4), T("C", 2)], SUN))
    assert {x.start_date for x in t.values()} == {SUN}
    assert t["C"].end_date == date(2026, 11, 16)


def test_join_waits_for_slowest_predecessor():
    t = by_id(compute_dates([T("A", 3), T("B", 4), T("D", 1, deps=["A", "B"])], SUN))
    assert t["D"].es == 4 and t["D"].start_date == date(2026, 11, 19)


def test_project_start_on_weekend_moves_to_next_working_day():
    r = compute_dates([T("A", 1)], date(2026, 11, 13))  # Friday
    assert r.project_start == SUN and by_id(r)["A"].start_date == SUN


def test_standard_calendar_mon_fri():
    mon = date(2026, 11, 16)
    five = by_id(compute_dates([T("A", 5)], mon, STANDARD_CALENDAR))["A"]
    six = by_id(compute_dates([T("A", 6)], mon, STANDARD_CALENDAR))["A"]
    assert five.end_date == date(2026, 11, 20)
    assert six.end_date == date(2026, 11, 23)  # skips Sat/Sun


def test_holiday_is_skipped():
    cal = WorkCalendar(holidays=frozenset({date(2026, 11, 16)}))
    a = by_id(compute_dates([T("A", 3)], SUN, cal))["A"]
    assert (a.start_date, a.end_date) == (date(2026, 11, 15), date(2026, 11, 18))
    assert a.end_date != date(2026, 11, 17)


def test_not_before_delays_start():
    t = by_id(compute_dates([T("A", 1), T("X", 2, not_before=date(2026, 11, 22))], SUN))["X"]
    assert t.es == 5 and t.start_date == date(2026, 11, 22) and t.end_date == date(2026, 11, 23)


def test_not_before_on_weekend_rolls_forward_and_past_date_ignored():
    r = compute_dates(
        [T("X", 1, not_before=date(2026, 11, 20)), T("Y", 1, not_before=date(2020, 1, 1))], SUN
    )
    t = by_id(r)
    assert t["X"].start_date == date(2026, 11, 22)  # Fri -> next working day
    assert t["Y"].start_date == SUN


def test_not_before_too_far_rejected():
    with pytest.raises(InvalidInputError):
        compute_dates([T("X", 1, not_before=date(2100, 1, 1))], SUN)


def test_zero_duration_milestone_sits_at_end_of_predecessor():
    r = compute_dates([T("A", 3), T("M", 0, deps=["A"]), T("B", 2, deps=["M"])], SUN)
    t = by_id(r)
    assert t["M"].start_date == t["M"].end_date == date(2026, 11, 17)
    assert t["B"].start_date == date(2026, 11, 18)
    assert r.total_duration_days == 5


def test_milestone_at_very_start():
    t = by_id(compute_dates([T("M", 0)], SUN))["M"]
    assert t.start_date == SUN


def test_input_order_does_not_matter_for_dates():
    fwd = compute_dates([T("A", 3), T("B", 4, deps=["A"])], SUN)
    rev = compute_dates([T("B", 4, deps=["A"]), T("A", 3)], SUN)
    f, r = by_id(fwd), by_id(rev)
    assert (f["A"].start_date, f["B"].end_date) == (r["A"].start_date, r["B"].end_date)
    assert [t.id for t in rev.tasks] == ["B", "A"]  # output keeps input order


def test_deterministic_repeat():
    tasks = [T("A", 3), T("B", 4, deps=["A"]), T("C", 2, deps=["A"])]
    assert compute_dates(tasks, SUN) == compute_dates(tasks, SUN)


def test_empty_task_list():
    r = compute_dates([], SUN)
    assert r.total_duration_days == 0 and r.tasks == () and r.critical_path == ()
    assert r.project_end == SUN


def test_int_ids_are_coerced_to_str():
    r = compute_dates([TaskInput(id=1, duration_days=2), TaskInput(id=2, duration_days=1, depends_on=[1])], SUN)
    assert [t.id for t in r.tasks] == ["1", "2"] and r.tasks[1].es == 2


# ------------------------------------------------------------ invalid input / edges
def test_duplicate_ids():
    with pytest.raises(DuplicateTaskError):
        compute_dates([T("A", 1), T("A", 2)], SUN)


def test_unknown_dependency():
    with pytest.raises(UnknownDependencyError, match="Z"):
        compute_dates([T("A", 1, deps=["Z"])], SUN)


def test_self_dependency():
    with pytest.raises(SelfDependencyError):
        compute_dates([T("A", 1, deps=["A"])], SUN)


def test_cycle_detected_and_reported():
    with pytest.raises(CycleError) as e:
        compute_dates([T("A", 1, deps=["C"]), T("B", 1, deps=["A"]), T("C", 1, deps=["B"])], SUN)
    cyc = e.value.cycle
    assert cyc[0] == cyc[-1] and set(cyc) == {"A", "B", "C"}


def test_cycle_among_valid_tasks_still_detected():
    with pytest.raises(CycleError) as e:
        compute_dates([T("OK", 1), T("A", 1, deps=["OK", "B"]), T("B", 1, deps=["A"])], SUN)
    assert set(e.value.cycle) == {"A", "B"}


def test_missing_duration():
    with pytest.raises(MissingDurationError):
        compute_dates([TaskInput(id="A")], SUN)


@pytest.mark.parametrize("bad", [-1, 2.5, 10_000])
def test_invalid_duration_rejected_by_schema(bad):
    with pytest.raises(ValidationError):
        TaskInput(id="A", duration_days=bad)


def test_empty_id_rejected():
    with pytest.raises(ValidationError):
        TaskInput(id="", duration_days=1)


def test_calendar_without_working_days_rejected():
    with pytest.raises(ValidationError):
        WorkCalendar(weekend_days=frozenset(range(7)))
    with pytest.raises(ValidationError):
        WorkCalendar(weekend_days=frozenset({9}))


def test_duplicate_dependency_entries_collapsed():
    assert TaskInput(id="B", duration_days=1, depends_on=["A", "A"]).depends_on == ("A",)


def test_long_chain_no_recursion_problem():
    tasks = [T("t0", 1)] + [T(f"t{i}", 1, deps=[f"t{i-1}"]) for i in range(1, 1500)]
    assert compute_dates(tasks, SUN).total_duration_days == 1500
