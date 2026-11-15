"""Deterministic scheduling pipeline check on Golden Project B (no LLM).

Agent 3's core is pure code; this proves the whole chain (resolve_durations -> compute_dates ->
detect_resource_conflicts -> get_team_capacity) behaves sensibly on the golden plan: the CPM is
cycle-free, floats are non-negative, the critical path exists, dates land on working days, and
loading one person with the whole plan is detected as an overallocation.
"""
import math
from datetime import date, timedelta

from evals.run_agent2 import load_golden
from app.schemas.scheduling import STANDARD_CALENDAR, TaskInput, TeamMemberInput
from app.agents.agent3_schedule import build_facts, resolve_durations, template_explanation
from app.tools.compute_dates import compute_dates
from app.tools.resource_conflicts import detect_resource_conflicts
from app.tools.team_capacity import get_team_capacity

MONDAY = next(
    d for d in (date(2026, 11, 16) + timedelta(days=i) for i in range(7))
    if STANDARD_CALENDAR.is_working_day(d)
)


def golden_task_inputs() -> list[TaskInput]:
    expected = load_golden("project-b")
    return [
        TaskInput(
            id=t["ref"],
            title=t["title"],
            estimate_hours=float(t["estimate_hours"]),
            depends_on=tuple(t.get("depends_on", [])),
        )
        for t in expected["tasks"]
    ]


def test_resolve_durations_converts_hours_to_working_days():
    resolved, records = resolve_durations(golden_task_inputs(), llm=None)
    assert all(r.source == "hours" for r in records)
    expected = load_golden("project-b")
    for record, task in zip(records, expected["tasks"]):
        assert record.duration_days == math.ceil(task["estimate_hours"] / 8)


def test_cpm_schedules_the_golden_plan_without_cycles():
    resolved, _ = resolve_durations(golden_task_inputs(), llm=None)
    schedule = compute_dates(resolved, MONDAY, STANDARD_CALENDAR)
    assert schedule.total_duration_days > 0
    assert schedule.critical_path and schedule.critical_task_ids
    assert all(t.total_float >= 0 for t in schedule.tasks)
    # every task starts and ends on a working day
    working = set(schedule.working_days)
    assert all(t.start_date in working and t.end_date in working for t in schedule.tasks)
    # dependencies are respected: a dependent never starts before its dependency ends
    dates = {t.id: t for t in schedule.tasks}
    for task in resolved:
        for dep in task.depends_on:
            assert dates[task.id].es >= dates[dep].ef


def test_one_person_doing_everything_is_flagged_as_overallocated():
    resolved, _ = resolve_durations(golden_task_inputs(), llm=None)
    schedule = compute_dates(resolved, MONDAY, STANDARD_CALENDAR)
    member = TeamMemberInput(id="1", name="Lone Wolf", capacity_hours_per_week=40)
    tasks = [t.model_copy(update={"assignee_id": "1"}) for t in schedule.tasks]
    schedule = schedule.model_copy(update={"tasks": tuple(tasks)})
    conflicts = detect_resource_conflicts(schedule, [member], STANDARD_CALENDAR)
    overallocations = [c for c in conflicts if c.kind == "overallocation"]
    assert overallocations, "independent tasks run in parallel: one person must be overloaded"
    capacity = get_team_capacity([member], schedule.project_start, schedule.project_end, STANDARD_CALENDAR, schedule)
    assert capacity.members[0].is_overloaded and (capacity.members[0].utilization or 0) > 1.0


def test_template_explanation_reflects_the_facts():
    resolved, _ = resolve_durations(golden_task_inputs(), llm=None)
    schedule = compute_dates(resolved, MONDAY, STANDARD_CALENDAR)
    conflicts = detect_resource_conflicts(schedule, [], STANDARD_CALENDAR)
    capacity = get_team_capacity([], schedule.project_start, schedule.project_end, STANDARD_CALENDAR, schedule)
    facts = build_facts(schedule, conflicts, capacity, [])
    out = template_explanation(facts)
    assert out.source == "template"
    assert str(schedule.project_end) in out.summary
    assert "المسار الحرج" in out.critical_path
