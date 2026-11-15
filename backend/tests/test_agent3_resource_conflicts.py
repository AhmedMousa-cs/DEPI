"""Deterministic tests: detect_resource_conflicts."""
from datetime import date

from app.schemas.scheduling import TaskInput, TeamMemberInput
from app.tools.compute_dates import compute_dates
from app.tools.resource_conflicts import detect_resource_conflicts

SUN = date(2026, 11, 15)


def T(id, d, a=None, deps=(), **kw):
    return TaskInput(id=id, duration_days=d, assignee_id=a, depends_on=deps, **kw)


def M(id, hours=40, **kw):
    return TeamMemberInput(id=id, capacity_hours_per_week=hours, **kw)


def conflicts(tasks, members=()):
    return detect_resource_conflicts(compute_dates(tasks, SUN), members)


def test_spec_example_ahmed_has_conflict_mohamed_does_not():
    c = conflicts([T("A", 3, "ahmed"), T("B", 4, "ahmed"), T("C", 2, "mohamed")])
    assert len(c) == 1
    x = c[0]
    assert x.kind == "overallocation" and x.assignee_id == "ahmed"
    assert x.task_ids == ("A", "B")
    assert (x.start_date, x.end_date, x.working_days) == (date(2026, 11, 15), date(2026, 11, 17), 3)
    assert x.overload_ratio == 2.0


def test_sequential_tasks_same_person_no_conflict():
    assert conflicts([T("A", 3, "ahmed"), T("B", 4, "ahmed", ["A"])]) == []


def test_parallel_tasks_different_people_no_conflict():
    assert conflicts([T("A", 3, "ahmed"), T("B", 3, "mohamed")]) == []


def test_unassigned_and_zero_duration_tasks_ignored():
    assert conflicts([T("A", 3), T("B", 3), T("M", 0, "ahmed"), T("C", 3, "ahmed")]) == []


def test_partial_overlap_single_day():
    # A: Sun-Tue ; B forced to start Tue (offset 2) -> overlap only on Tue 17
    c = conflicts([T("A", 3, "ahmed"), T("B", 3, "ahmed", not_before=date(2026, 11, 17))])
    assert len(c) == 1 and c[0].start_date == c[0].end_date == date(2026, 11, 17)
    assert c[0].working_days == 1


def test_hours_based_load_below_capacity_is_ok():
    # 4h/day + 4h/day = 8h/day <= 8h capacity (40h/week, 5 working days)
    c = conflicts(
        [T("A", 3, "ahmed", estimate_hours=12), T("B", 4, "ahmed", estimate_hours=16)],
        [M("ahmed", 40)],
    )
    assert c == []


def test_hours_based_overload_splits_into_windows_by_task_set():
    c = conflicts(
        [T("A", 3, "ahmed", estimate_hours=12), T("B", 4, "ahmed", estimate_hours=40)],
        [M("ahmed", 40)],
    )
    # days 0-2: A(4)+B(10)=14 > 8 ; day 3: B alone 10 > 8
    assert [(x.task_ids, x.working_days) for x in c] == [(("A", "B"), 3), (("B",), 1)]
    assert c[0].peak_load_hours_per_day == 14.0 and c[0].capacity_hours_per_day == 8.0


def test_single_unrealistic_task_is_flagged():
    c = conflicts([T("A", 2, "ahmed", estimate_hours=80)], [M("ahmed", 40)])
    assert len(c) == 1 and c[0].task_ids == ("A",) and c[0].peak_load_hours_per_day == 40.0


def test_part_time_member_single_task_without_hours_is_fine():
    assert conflicts([T("A", 3, "sara")], [M("sara", 20)]) == []


def test_part_time_member_overlap_flagged_with_member_capacity():
    c = conflicts([T("A", 2, "sara"), T("B", 2, "sara")], [M("sara", 20)])
    assert c[0].capacity_hours_per_day == 4.0 and c[0].peak_load_hours_per_day == 8.0


def test_zero_capacity_member_always_conflicts():
    c = conflicts([T("A", 2, "sara")], [M("sara", 0)])
    assert len(c) == 1 and c[0].overload_ratio is None


def test_unknown_assignee_uses_default_8h_day():
    assert len(conflicts([T("A", 2, "ghost"), T("B", 2, "ghost")])) == 1
    assert conflicts([T("A", 2, "ghost")]) == []


def test_task_before_availability_start():
    c = conflicts([T("A", 3, "ahmed")], [M("ahmed", available_from=date(2026, 11, 17))])
    assert len(c) == 1 and c[0].kind == "outside_availability"
    assert (c[0].start_date, c[0].end_date) == (date(2026, 11, 15), date(2026, 11, 16))


def test_task_after_availability_end():
    c = conflicts([T("A", 4, "ahmed")], [M("ahmed", available_to=date(2026, 11, 17))])
    assert len(c) == 1 and c[0].start_date == c[0].end_date == date(2026, 11, 18)


def test_task_fully_inside_availability_ok():
    assert conflicts(
        [T("A", 3, "ahmed")], [M("ahmed", available_from=SUN, available_to=date(2026, 11, 17))]
    ) == []


def test_results_sorted_and_deterministic():
    tasks = [T("A", 2, "zed"), T("B", 2, "zed"), T("C", 2, "amr"), T("D", 2, "amr")]
    a, b = conflicts(tasks), conflicts(tasks)
    assert a == b and [x.assignee_id for x in a] == ["amr", "zed"]
