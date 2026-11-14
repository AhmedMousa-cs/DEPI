"""Deterministic plan rules (no DB, no LLM)."""
import copy

from app.services.plan_rules import rules_review, run_rules, verdict_floor
from app.agents.agent4_testing import sample_snapshot


def rules(snap):
    return {f.rule: f for f in run_rules(snap)}


def test_sample_plan_triggers_the_expected_rules():
    snap = sample_snapshot()
    got = rules(snap)
    assert set(got) == {"resource_conflict", "overloaded_member", "no_slack", "single_point_of_failure"}
    assert verdict_floor(list(got.values()), snap) == "needs_attention"


def test_clean_plan_has_no_findings_and_is_ready():
    snap = sample_snapshot(overlap=False)
    snap["tasks"][2]["total_float"] = 1
    snap["schedule"]["critical_task_ids"] = [11]
    snap["schedule"]["critical_path"] = [11]
    found = run_rules(snap)
    assert [f.rule for f in found] == []
    assert verdict_floor(found, snap) == "ready"
    assert rules_review(found, snap)["verdict"] == "ready"


def test_deadline_exceeded_severity_depends_on_overrun():
    snap = sample_snapshot(target_end="2026-11-20")  # ends 22nd -> 2 days late
    assert rules(snap)["deadline_exceeded"].risk.severity == "high"
    snap = sample_snapshot(target_end="2026-10-01")  # ~7 weeks late
    f = rules(snap)["deadline_exceeded"]
    assert f.risk.severity == "critical" and verdict_floor([f], snap) == "blocked"
    assert [e.id for e in f.risk.evidence_refs] == ["11", "12", "14"]  # the critical path


def test_no_deadline_no_rule():
    assert "deadline_exceeded" not in rules(sample_snapshot(target_end=None))


def test_unassigned_tasks_rule_and_severity():
    snap = sample_snapshot(overlap=False)
    snap["tasks"][0]["assignee_member_id"] = None
    assert rules(snap)["unassigned_tasks"].risk.severity == "medium"
    for t in snap["tasks"]:
        t["assignee_member_id"] = None
    assert rules(snap)["unassigned_tasks"].risk.severity == "high"


def test_availability_conflict_gets_its_own_rule():
    snap = sample_snapshot()
    snap["conflicts"] = [{"kind": "outside_availability", "assignee_id": 101, "task_ids": [12],
                          "start_date": "2026-11-17", "end_date": "2026-11-18", "working_days": 2,
                          "key": "101|outside_availability|2026-11-17|12"}]
    assert "unavailable_assignee" in rules(snap)


def test_unknown_assignee_rule():
    snap = sample_snapshot(overlap=False)
    snap["capacity"]["unknown_assignees"] = [999]
    snap["tasks"][3]["assignee_member_id"] = 999
    f = rules(snap)["unknown_assignee"]
    assert [e.id for e in f.risk.evidence_refs] == ["14"]


def test_every_finding_has_evidence_that_exists_in_the_snapshot():
    snap = sample_snapshot(target_end="2026-11-01")
    task_ids = {str(t["id"]) for t in snap["tasks"]}
    member_ids = {str(m["member_id"]) for m in snap["capacity"]["members"]}
    keys = {c["key"] for c in snap["conflicts"]}
    for f in run_rules(snap):
        assert f.risk.evidence_refs
        for e in f.risk.evidence_refs:
            assert e.id in {"task": task_ids, "member": member_ids, "conflict": keys}[e.type]


def test_rules_are_deterministic_and_do_not_mutate_the_snapshot():
    snap = sample_snapshot()
    before = copy.deepcopy(snap)
    assert [f.risk.title for f in run_rules(snap)] == [f.risk.title for f in run_rules(snap)]
    assert snap == before


def test_conflicts_alone_force_needs_attention():
    snap = sample_snapshot()
    assert verdict_floor([], snap) == "needs_attention"
