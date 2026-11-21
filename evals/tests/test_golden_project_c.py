"""Golden Project C: one deterministic plan whose schedule, conflict and Agent 4 rules are known.

No LLM and no database. The fixture (evals/golden/project-c-expected.json) states the expected
end date, critical path, per-task dates, the single resource conflict and the rule risks that
must fire, so a regression in Agent 3's CPM/conflict detection or in Agent 4's rules fails here.
"""
from __future__ import annotations

import math
from datetime import date

from evals.run_agent2 import load_golden
from app.agents.agent3_schedule import resolve_durations
from app.schemas.scheduling import STANDARD_CALENDAR, TaskInput, TeamMemberInput
from app.services.plan_rules import run_rules, verdict_floor
from app.services.plan_versions import conflict_key
from app.tools.compute_dates import compute_dates
from app.tools.resource_conflicts import detect_resource_conflicts
from app.tools.team_capacity import get_team_capacity

EXPECTED = load_golden("project-c")
SCHED = EXPECTED["scheduling"]
EXPECT = SCHED["expected"]
START = date.fromisoformat(SCHED["project_start"])
TARGET_END = SCHED["target_end"]

MEMBER_BY_REF = {m["ref"]: m["id"] for m in SCHED["team"]}
TEAM = [
    TeamMemberInput(id=m["id"], name=m["name"], capacity_hours_per_week=m["capacity_hours_per_week"])
    for m in SCHED["team"]
]


def golden_task_inputs() -> list[TaskInput]:
    return [
        TaskInput(
            id=t["ref"],
            title=t["title"],
            estimate_hours=float(t["estimate_hours"]),
            assignee_id=MEMBER_BY_REF.get(t.get("assignee_ref")),
            depends_on=tuple(t.get("depends_on", [])),
        )
        for t in EXPECTED["tasks"]
    ]


def scheduled():
    resolved, records = resolve_durations(golden_task_inputs(), llm=None)
    schedule = compute_dates(resolved, START, STANDARD_CALENDAR)
    conflicts = detect_resource_conflicts(schedule, TEAM, STANDARD_CALENDAR)
    return resolved, records, schedule, conflicts


def snapshot_for(schedule, conflicts) -> dict:
    """The same snapshot shape Agent 4 receives, built straight from the tool outputs."""
    capacity = get_team_capacity(TEAM, schedule.project_start, schedule.project_end, STANDARD_CALENDAR, schedule)
    members = []
    for member in capacity.members:
        row = member.model_dump(mode="json")
        row["member_id"] = int(row["member_id"])
        members.append(row)
    conflict_rows = []
    for conflict in conflicts:
        row = conflict.model_dump(mode="json")
        row["assignee_id"] = int(row["assignee_id"])
        row["task_ids"] = list(row["task_ids"])
        row["key"] = conflict_key(row)
        conflict_rows.append(row)
    return {
        "project": {"id": 0, "end_date": TARGET_END},
        "schedule": {
            "project_start": schedule.project_start.isoformat(),
            "project_end": schedule.project_end.isoformat(),
            "total_duration_days": schedule.total_duration_days,
            "critical_path": list(schedule.critical_path),
            "critical_task_ids": list(schedule.critical_task_ids),
        },
        "tasks": [
            {
                "id": t.id,
                "title": t.title,
                "assignee_member_id": int(t.assignee_id) if t.assignee_id else None,
                "duration_days": t.duration_days,
                "estimate_hours": t.estimate_hours,
                "start": t.start_date.isoformat(),
                "end": t.end_date.isoformat(),
                "is_critical": t.is_critical,
                "total_float": t.total_float,
                "depends_on": [],
            }
            for t in schedule.tasks
        ],
        "conflicts": conflict_rows,
        "capacity": {"members": members, "unknown_assignees": []},
    }


def test_durations_come_from_hours():
    _, records, _, _ = scheduled()
    by_ref = {r.task_id: r for r in records}
    for task in EXPECTED["tasks"]:
        record = by_ref[task["ref"]]
        assert record.source == "hours"
        assert record.duration_days == math.ceil(task["estimate_hours"] / 8)


def test_dates_critical_path_and_end_date_match_the_golden():
    _, _, schedule, _ = scheduled()
    assert schedule.project_start.isoformat() == START.isoformat()
    assert schedule.total_duration_days == EXPECT["total_duration_days"]
    assert schedule.project_end.isoformat() == EXPECT["project_end"]
    assert list(schedule.critical_path) == EXPECT["critical_path"]
    assert set(schedule.critical_task_ids) == set(EXPECT["critical_path"])
    dates = {t.id: [t.start_date.isoformat(), t.end_date.isoformat()] for t in schedule.tasks}
    assert dates == EXPECT["task_dates"]
    working = set(schedule.working_days)
    assert all(t.start_date in working and t.end_date in working for t in schedule.tasks)
    assert all(t.total_float >= 0 for t in schedule.tasks)


def test_exactly_one_resource_conflict_on_the_overlapping_tasks():
    _, _, _, conflicts = scheduled()
    assert len(conflicts) == len(EXPECT["conflicts"]) == 1
    got, want = conflicts[0], EXPECT["conflicts"][0]
    assert got.kind == want["kind"]
    assert got.assignee_id == want["assignee_id"]
    assert list(got.task_ids) == want["task_ids"]
    assert got.start_date.isoformat() == want["start_date"]
    assert got.end_date.isoformat() == want["end_date"]
    assert got.overload_ratio == want["overload_ratio"]
    # the same person owns both tasks, so the plan is over capacity on those days only
    assert got.peak_load_hours_per_day == 16
    assert got.capacity_hours_per_day == 8


def test_agent4_rules_and_verdict_match_the_golden():
    _, _, schedule, conflicts = scheduled()
    snapshot = snapshot_for(schedule, conflicts)
    findings = run_rules(snapshot)
    assert sorted(f.rule for f in findings) == sorted(EXPECT["rule_risks"])
    by_rule = {f.rule: f for f in findings}

    deadline = by_rule["deadline_exceeded"].risk
    assert deadline.severity == "high"  # 3 calendar days late, so not "critical"
    assert "2026-12-04" in deadline.description and TARGET_END in deadline.description

    resource = by_rule["resource_conflict"].risk
    assert "Sara Fathy" in resource.title
    assert any(e.type == "member" and e.id == "3" for e in resource.evidence_refs)
    assert any(e.type == "task" for e in resource.evidence_refs)

    assert verdict_floor(findings, snapshot) == EXPECT["verdict"]
