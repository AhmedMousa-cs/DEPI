"""The deterministic review brief Agent 4 reads first (pure function, no DB, no LLM)."""
import copy

from app.agents.agent4_testing import sample_snapshot
from app.services.plan_health import longest_chain, plan_health


def test_health_pack_computes_the_numbers_a_reviewer_needs():
    snap = sample_snapshot()  # Ahmed is double booked; target end is 9 days after the plan ends
    h = plan_health(snap)
    sched = h["schedule"]
    assert (sched["start"], sched["end"], sched["target_end"]) == ("2026-11-15", "2026-11-22", "2026-12-01")
    assert sched["days_late"] == -9 and sched["days_early"] == 9
    assert sched["tasks"] == 4 and sched["critical_tasks"] == 3
    assert sched["critical_share"] == 0.75 and sched["zero_float_tasks"] == 3
    assert sched["total_estimate_hours"] == 72.0
    # critical share alone is enough to call this plan at risk, before the conflict is even read
    assert h["delivery_signal"] == "at_risk"


def test_health_pack_reports_workload_and_the_longest_chain():
    h = plan_health(sample_snapshot())
    ahmed, sara = h["workload"]
    assert (ahmed["name"], ahmed["is_overloaded"], ahmed["tasks"]) == ("Ahmed", True, 3)
    assert ahmed["critical_tasks"] == [11, 12] and round(ahmed["utilization"], 3) == 1.333
    assert (sara["name"], sara["is_overloaded"], sara["tasks"]) == ("Sara", False, 1)
    assert [t["id"] for t in h["critical_chain"]] == [11, 12, 14]
    assert [t["title"] for t in h["critical_chain"]][0] == "Design booking flow"


def test_a_late_plan_is_flagged_as_late_with_the_shift_in_days():
    h = plan_health(sample_snapshot(target_end="2026-11-18"))
    assert h["schedule"]["days_late"] == 4 and h["schedule"]["days_early"] is None
    assert h["delivery_signal"] == "late"
    top = h["attention"][0]
    assert top["signal"] == "deadline_exceeded" and top["severity"] == "high"
    assert "4 calendar day" in top["detail"]


def test_more_than_two_weeks_late_escalates_to_critical():
    h = plan_health(sample_snapshot(target_end="2026-10-01"))
    deadline = next(a for a in h["attention"] if a["signal"] == "deadline_exceeded")
    assert deadline["severity"] == "critical"


def test_dependency_timing_and_missing_dependencies_are_detected():
    snap = sample_snapshot()
    snap["tasks"][2]["start"] = "2026-11-15"  # task 13 now starts before task 11 (its dependency) ends
    snap["tasks"][3]["depends_on"] = [12, 13, 999]
    h = plan_health(snap)
    issues = {(i["task_id"], i["depends_on"]): i["issue"] for i in h["dependency_issues"]}
    assert "starts 2026-11-15 before its dependency ends 2026-11-16" == issues[(13, 11)]
    assert issues[(14, 999)] == "missing dependency (not in this plan)"
    assert any(a["signal"] == "dependency_timing" for a in h["attention"])


def test_an_estimate_that_does_not_fit_the_scheduled_days_is_flagged():
    snap = sample_snapshot()
    snap["tasks"][0]["estimate_hours"] = 40  # 2 working days at 8 h/day
    h = plan_health(snap)
    assert h["under_allocated_tasks"] == [{"task_id": 11, "estimate_hours": 40.0,
                                           "scheduled_capacity_hours": 16.0}]
    assert any(a["signal"] == "under_allocated_task" for a in h["attention"])


def test_a_clean_plan_has_no_attention_points():
    snap = sample_snapshot(overlap=False)
    snap["tasks"][2]["total_float"] = 1
    snap["schedule"]["critical_task_ids"] = [11]
    snap["schedule"]["critical_path"] = [11]
    snap["project"]["end_date"] = "2026-12-31"
    h = plan_health(snap)
    assert h["delivery_signal"] == "on_track"
    assert h["attention"] == [] and h["unassigned_task_ids"] == []


def test_a_dependency_cycle_does_not_hang_or_crash():
    snap = sample_snapshot()
    snap["tasks"][0]["depends_on"] = [14]
    h = plan_health(snap)
    assert [t["id"] for t in h["critical_chain"]][-1] in {t["id"] for t in snap["tasks"]}
    assert longest_chain(snap["tasks"], {t["id"]: t for t in snap["tasks"]})


def test_the_brief_is_deterministic_for_the_same_snapshot():
    snap = copy.deepcopy(sample_snapshot())
    assert plan_health(snap) == plan_health(copy.deepcopy(snap))
