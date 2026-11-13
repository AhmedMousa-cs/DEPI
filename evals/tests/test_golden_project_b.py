"""Deterministic Golden Project B tests for Agent 2 (stories & tasks).

These tests never call an LLM: they exercise the same validators and metrics the pipeline uses, so
a regression in coverage, dependency-cycle detection or estimate sanity fails fast and for free.
"""
import copy

import pytest

from evals.planning_metrics import evaluate_plan, oracle_plan
from evals.run_agent2 import load_golden


@pytest.fixture(scope="module")
def expected():
    return load_golden("project-b")


def run(expected, plan):
    return evaluate_plan(expected, plan)


def story(expected, ref):
    return next(s for s in expected["stories"] if s["ref"] == ref)


def task(expected, ref):
    return next(t for t in expected["tasks"] if t["ref"] == ref)


def test_golden_shape(expected):
    req_ids = {r["requirement_id"] for r in expected["requirements"]}
    story_refs = {s["ref"] for s in expected["stories"]}
    task_refs = {t["ref"] for t in expected["tasks"]}
    assert len(req_ids) == 8 and len(story_refs) == 8 and len(task_refs) == 12
    assert all(s["requirement_id"] in req_ids for s in expected["stories"])
    assert all(t["story_ref"] in story_refs for t in expected["tasks"])
    assert all(set(t.get("depends_on", [])) <= task_refs for t in expected["tasks"])
    assert all(s.get("match_terms") for s in expected["stories"])
    assert all(s.get("acceptance_criteria") for s in expected["stories"])


def test_oracle_plan_is_perfect(expected):
    r = run(expected, oracle_plan(expected))
    assert r["schema_valid"] is True
    assert r["story_coverage"] == 1.0 and r["story_recall"] == 1.0 and r["task_recall"] == 1.0
    assert r["link_integrity"] and r["cycle_free"] and r["estimate_sanity"]
    assert r["acceptance_criteria"] and r["task_granularity"]
    assert r["total_hours_ratio"] == 0.0
    assert all(r["passed"].values())


def test_every_story_links_to_an_approved_requirement(expected):
    plan = oracle_plan(expected)
    plan["stories"][0]["requirement_id"] = 999
    r = run(expected, plan)
    assert r["schema_valid"] is True
    assert r["link_integrity"] is False
    assert "not approved" in " ".join(r["details"]["link_errors"])
    assert r["passed"]["link_integrity"] is False


def test_missing_story_lowers_coverage(expected):
    plan = oracle_plan(expected)
    plan["stories"] = [s for s in plan["stories"] if s["ref"] != "S8"]
    plan["tasks"] = [t for t in plan["tasks"] if t["story_ref"] != "S8"]
    r = run(expected, plan)
    assert r["story_coverage"] < 1 and 8 in r["details"]["requirements_without_story"]
    assert r["story_recall"] < 1 and r["passed"]["story_coverage"] is False


def test_story_without_task_breaks_link_integrity(expected):
    plan = oracle_plan(expected)
    # T8 is the only task of S5 and nothing depends on it, so dropping it leaves a story with no task.
    plan["tasks"] = [t for t in plan["tasks"] if t["ref"] != "T8"]
    r = run(expected, plan)
    assert r["link_integrity"] is False
    assert "has no task" in " ".join(r["details"]["link_errors"])
    assert r["task_recall"] < 1


def test_duplicate_story_for_a_requirement_is_flagged(expected):
    plan = oracle_plan(expected)
    first = copy.deepcopy(plan["stories"][0])
    first["ref"] = "S9"
    plan["stories"].append(first)
    r = run(expected, plan)
    assert r["link_integrity"] is False
    assert "duplicate story" in " ".join(r["details"]["link_errors"])


def test_task_pointing_at_unknown_story_is_rejected(expected):
    plan = oracle_plan(expected)
    plan["tasks"][0]["story_ref"] = "S99"
    r = run(expected, plan)
    assert r["schema_valid"] is False and r["passed"]["schema_valid"] is False
    assert "unknown stories" in " ".join(r["errors"])


def test_unknown_dependency_is_rejected(expected):
    plan = oracle_plan(expected)
    plan["tasks"][0]["depends_on"] = ["T99"]
    r = run(expected, plan)
    assert r["schema_valid"] is False
    assert "unknown task dependencies" in " ".join(r["errors"])


def test_self_dependency_is_rejected(expected):
    plan = oracle_plan(expected)
    plan["tasks"][0]["depends_on"] = [plan["tasks"][0]["ref"]]
    r = run(expected, plan)
    assert r["schema_valid"] is False
    assert "cannot depend on itself" in " ".join(r["errors"])


def test_dependency_cycle_is_rejected(expected):
    plan = oracle_plan(expected)
    plan["tasks"][0]["depends_on"] = [plan["tasks"][1]["ref"]]
    plan["tasks"][1]["depends_on"] = [plan["tasks"][0]["ref"]]
    r = run(expected, plan)
    assert r["schema_valid"] is False and r["cycle_free"] is False
    assert "cycle" in " ".join(r["errors"])


def test_illogical_identical_estimates_lower_sanity(expected):
    plan = oracle_plan(expected)
    for t in plan["tasks"]:
        t["estimate_hours"] = 10
    r = run(expected, plan)
    assert r["schema_valid"] is True
    assert r["estimate_sanity"] is False and r["passed"]["estimate_sanity"] is False


def test_out_of_range_estimate_is_rejected(expected):
    plan = oracle_plan(expected)
    plan["tasks"][0]["estimate_hours"] = 200
    r = run(expected, plan)
    assert r["schema_valid"] is False and r["passed"]["schema_valid"] is False


def test_missing_acceptance_criteria_is_rejected(expected):
    plan = oracle_plan(expected)
    plan["stories"][0]["acceptance_criteria"] = []
    r = run(expected, plan)
    assert r["schema_valid"] is False and r["acceptance_criteria"] is False


def test_overloaded_story_breaks_task_granularity(expected):
    plan = oracle_plan(expected)
    next_ref = 13
    for _ in range(8):
        plan["tasks"].append(
            {
                "ref": f"T{next_ref}",
                "story_ref": "S1",
                "title": f"Extra task {next_ref}",
                "estimate_hours": 4,
                "depends_on": [],
                "confidence": 0.8,
            }
        )
        next_ref += 1
    r = run(expected, plan)
    assert r["schema_valid"] is True
    assert r["task_granularity"] is False and r["passed"]["task_granularity"] is False


def test_total_hours_ratio_flags_a_large_overrun(expected):
    plan = oracle_plan(expected)
    for t in plan["tasks"]:
        t["estimate_hours"] = t["estimate_hours"] * 2
    r = run(expected, plan)
    assert r["schema_valid"] is True
    assert r["total_hours_ratio"] >= 0.5 and r["passed"]["total_hours_ratio"] is False


def test_empty_plan_is_safe(expected):
    r = run(expected, {})
    assert r["schema_valid"] is False and r["story_coverage"] == 0.0
    assert r["link_integrity"] is False and r["cycle_free"] is False
