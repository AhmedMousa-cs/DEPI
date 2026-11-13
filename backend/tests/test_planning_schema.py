"""Deterministic Agent 2 contract tests. No LLM, no database."""
import pytest
from pydantic import ValidationError

from app.schemas.planning import AgentStory, AgentTask, PlanningOutput, find_cycle


def story(ref="S1", requirement_id=1, text="As a patient I can book an appointment online"):
    return {
        "ref": ref,
        "requirement_id": requirement_id,
        "text": text,
        "acceptance_criteria": ["A booking is saved and visible in the list."],
        "confidence": 0.9,
    }


def task(ref="T1", story_ref="S1", title="Build the booking API", depends_on=None, estimate=8):
    return {
        "ref": ref,
        "story_ref": story_ref,
        "title": title,
        "estimate_hours": estimate,
        "depends_on": depends_on or [],
        "confidence": 0.9,
    }


def test_valid_plan_parses():
    out = PlanningOutput(stories=[story()], tasks=[task()])
    assert out.stories[0].ref == "S1" and out.tasks[0].story_ref == "S1"


def test_output_has_no_status_or_id_fields():
    with pytest.raises(ValidationError):
        PlanningOutput(stories=[{**story(), "status": "approved"}], tasks=[task()])
    with pytest.raises(ValidationError):
        PlanningOutput(stories=[story()], tasks=[{**task(), "id": 4}])


def test_unknown_story_reference_is_rejected():
    with pytest.raises(ValidationError, match="unknown stories"):
        PlanningOutput(stories=[story()], tasks=[task(story_ref="S9")])


def test_unknown_dependency_is_rejected():
    with pytest.raises(ValidationError, match="unknown task dependencies"):
        PlanningOutput(stories=[story()], tasks=[task(depends_on=["T9"])])


def test_self_dependency_is_rejected():
    with pytest.raises(ValidationError, match="cannot depend on itself"):
        PlanningOutput(stories=[story()], tasks=[task(depends_on=["T1"])])


@pytest.mark.parametrize(
    "edges",
    [
        {"T1": ["T2"], "T2": ["T1"]},
        {"T1": ["T2"], "T2": ["T3"], "T3": ["T1"]},
        {"T1": ["T1"]},
    ],
)
def test_cycle_is_rejected(edges):
    tasks = [
        task(ref=ref, title=f"Task {ref}", depends_on=deps, estimate=4)
        for ref, deps in edges.items()
    ]
    with pytest.raises(ValidationError, match="cycle|cannot depend on itself"):
        PlanningOutput(stories=[story()], tasks=tasks)


def test_find_cycle_is_none_for_a_dag():
    assert find_cycle({"T1": ["T2"], "T2": []}) is None
    assert find_cycle({"T1": [], "T2": ["T1"]}) is None


def test_duplicate_refs_and_tasks_are_rejected():
    with pytest.raises(ValidationError, match="story refs must be unique"):
        PlanningOutput(stories=[story(), story()], tasks=[task()])
    with pytest.raises(ValidationError, match="task refs must be unique"):
        PlanningOutput(stories=[story()], tasks=[task(), task()])


@pytest.mark.parametrize("estimate", [0, 0.1, 80.5, 200])
def test_illogical_estimates_are_rejected(estimate):
    with pytest.raises(ValidationError):
        PlanningOutput(stories=[story()], tasks=[task(estimate=estimate)])


@pytest.mark.parametrize("estimate", [0.5, 1, 16, 80])
def test_estimate_bounds_are_inclusive(estimate):
    assert PlanningOutput(stories=[story()], tasks=[task(estimate=estimate)])


def test_stories_need_testable_acceptance_criteria():
    with pytest.raises(ValidationError):
        AgentStory(**{**story(), "acceptance_criteria": []})
    with pytest.raises(ValidationError):
        AgentStory(**{**story(), "acceptance_criteria": ["ok"]})
    with pytest.raises(ValidationError):
        AgentStory(**{**story(), "acceptance_criteria": ["x" * 301]})


def test_duplicate_acceptance_criteria_are_rejected():
    with pytest.raises(ValidationError, match="unique"):
        AgentStory(**{**story(), "acceptance_criteria": ["Same criterion", "Same criterion"]})


def test_refs_must_follow_the_contract():
    with pytest.raises(ValidationError):
        AgentStory(**{**story(), "ref": "story-1"})
    with pytest.raises(ValidationError):
        AgentTask(**{**task(), "ref": "1"})
