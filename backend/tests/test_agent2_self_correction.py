"""Agent 2 self-correction: no database and no LLM calls (structured_call is replaced by a fake).

Note: PlanningOutput requires at least one story AND one task with valid links, so a plan that is
"broken" for the quality checks must still be schema-valid (e.g. two stories but a task under only
one of them, or a requirement left without any story).
"""
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.agents import agent2_plan
from app.agents.plan_checks import plan_issues
from app.schemas.planning import PlanningOutput


def plan(requirement_id: int = 1, *, ref: str = "S1", order: int | None = None,
         critical: bool = False, rationale: str | None = None, estimate: float = 8.0) -> dict:
    """A minimal schema-valid plan for one requirement."""
    story = {
        "ref": ref,
        "requirement_id": requirement_id,
        "text": "As a patient I can book an appointment online",
        "acceptance_criteria": ["Booking finishes in two taps."],
        "confidence": 0.9,
        "order": order,
    }
    task = {
        "ref": "T1",
        "story_ref": ref,
        "title": "Build the booking form",
        "estimate_hours": estimate,
        "depends_on": [],
        "confidence": 0.8,
        "order": order,
        "is_critical": critical,
        "rationale": rationale,
    }
    return {"stories": [story], "tasks": [task]}


def merged(*plans: dict) -> dict:
    stories = [s for p in plans for s in p["stories"]]
    tasks = []
    for i, p in enumerate(plans, start=1):
        for t in p["tasks"]:
            tasks.append({**t, "ref": f"T{i}"})
    return {"stories": stories, "tasks": tasks}


def incomplete_plan(requirement_ids: list[int]) -> dict:
    """Schema-valid, but requirement_ids[-1] has no story and one story has no task."""
    first = plan(requirement_ids[0])
    second_story = {**plan(requirement_ids[0], ref="S2"), "tasks": []}
    second_story["stories"][0]["text"] = "As a patient I can reschedule an appointment online"
    return {
        "stories": [first["stories"][0], second_story["stories"][0]],
        "tasks": [first["tasks"][0]],
    }


def good_plan(requirement_ids: list[int]) -> dict:
    """A clean plan covering every given requirement: ordered, critical-marked, with rationale."""
    plans = [
        plan(rid, ref=f"S{i}", order=i, critical=(i == 1),
             rationale="Unblocks the booking UI and every flow that follows.")
        for i, rid in enumerate(requirement_ids, start=1)
    ]
    return merged(*plans)


def reqs(*pairs: tuple[int, str]) -> list[dict]:
    """Approved-requirement shape for the checks: id + priority."""
    return [{"id": rid, "priority": prio} for rid, prio in pairs]


def fake_result(value: dict, raw: str) -> SimpleNamespace:
    return SimpleNamespace(
        value=PlanningOutput.model_validate(value), raw_text=raw, attempts=1,
        usage=SimpleNamespace(input_tokens=10, output_tokens=10, estimated_cost_usd=0.0),
    )


# ---- plan_issues ---------------------------------------------------------------------------------


def test_clean_plan_has_no_issues():
    hard, soft = plan_issues(PlanningOutput.model_validate(good_plan([1])), reqs((1, "high")))
    assert hard == [] and soft == []


def test_hard_issues_block_a_broken_plan():
    output = PlanningOutput.model_validate(incomplete_plan([1, 2]))
    hard, soft = plan_issues(output, reqs((1, "high"), (2, "high"), (3, "high")))
    assert any("requirements without any story" in i for i in hard)
    assert any("stories without any task" in i for i in hard)


def test_duplicate_story_is_hard():
    story = plan(1)["stories"][0]
    duplicate = {**story, "ref": "S2", "order": 2}
    output = PlanningOutput.model_validate({"stories": [story, duplicate], "tasks": plan(1)["tasks"]})
    hard, soft = plan_issues(output, reqs((1, "high")))
    assert any("duplicate story" in i for i in hard)


def test_duplicate_task_is_hard():
    base = plan(1)
    task = base["tasks"][0]
    duplicate = {**task, "ref": "T2", "order": 2}
    output = PlanningOutput.model_validate({"stories": base["stories"], "tasks": [task, duplicate]})
    hard, soft = plan_issues(output, reqs((1, "high")))
    assert any("duplicate task" in i for i in hard)


def test_uniform_estimates_and_missing_polish_are_soft_only():
    output = PlanningOutput.model_validate(good_plan([1, 2]))
    for task in output.tasks:
        task.estimate_hours = 8.0
        task.is_critical = False
        task.rationale = None
    for story in output.stories:
        story.order = None
    for task in output.tasks:
        task.order = None
    hard, soft = plan_issues(output, reqs((1, "high"), (2, "medium")))
    assert hard == []
    assert any("same estimate" in i for i in soft)
    assert any("is_critical" in i for i in soft)
    assert any("without a rationale" in i for i in soft)
    assert any("missing their order" in i for i in soft)


def test_task_ordered_not_after_its_dependency_is_soft():
    # both tasks inside one story, so the task order decides the execution sequence
    two = {
        "stories": [plan(1, order=1, critical=True, rationale="Unblocks the booking UI.")["stories"][0]],
        "tasks": [
            plan(1)["tasks"][0],
            {**plan(1)["tasks"][0], "ref": "T2", "title": "Test the booking flow",
             "order": 1, "depends_on": ["T1"]},
        ],
    }
    two["tasks"][0]["order"] = 2  # T2 (order 1) depends on T1 (order 2): wrong sequence
    output = PlanningOutput.model_validate(two)
    hard, soft = plan_issues(output, reqs((1, "high")))
    assert hard == []
    assert any("not ordered after its dependency" in i for i in soft)


def test_overloaded_story_is_hard():
    # estimate range is enforced by the schema itself (0.5..80), so here we only exercise granularity
    two = merged(plan(1, ref="S1"), plan(2, ref="S2"))
    for i in range(1, 10):  # 9 extra tasks in one story: over the granularity limit
        two["tasks"].append({**two["tasks"][0], "ref": f"T{i + 2}", "order": i + 2})
    output = PlanningOutput.model_validate(two)
    hard, soft = plan_issues(output, reqs((1, "high"), (2, "medium")))
    assert any("more than" in i for i in hard)


# ---- self_correcting_call ------------------------------------------------------------------------


def test_self_correction_feeds_hard_issues_back_and_fixes_the_plan(monkeypatch):
    calls: list[dict] = []

    def fake_structured_call(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return fake_result(incomplete_plan([1, 2]), "incomplete plan")
        return fake_result(good_plan([1, 2]), "fixed plan")

    monkeypatch.setattr(agent2_plan, "structured_call", fake_structured_call)
    loop = agent2_plan.self_correcting_call(reqs((1, "high"), (2, "medium")))
    result = loop(
        model=object(),
        messages=[{"role": "user", "content": "requirements"}],
        schema=PlanningOutput,
        max_attempts=agent2_plan.MAX_PASSES * agent2_plan.ATTEMPTS_PER_PASS,
        max_tokens=1000,
    )
    assert len(calls) == 2
    feedback = calls[1]["messages"][-1]["content"]
    assert "requirements without any story" in feedback and "stories without any task" in feedback
    assert calls[1]["messages"][-2] == {"role": "assistant", "content": "incomplete plan"}
    assert loop.state["hard"] == []
    assert result.value.tasks[0].is_critical is True


def test_self_correction_gives_up_with_a_clear_error_after_max_passes(monkeypatch):
    monkeypatch.setattr(
        agent2_plan, "structured_call",
        lambda **kwargs: fake_result(incomplete_plan([1, 2]), "still broken"),
    )
    loop = agent2_plan.self_correcting_call(reqs((1, "high"), (2, "medium")))
    with pytest.raises(agent2_plan.PlanQualityError) as excinfo:
        loop(model=object(), messages=[{"role": "user", "content": "r"}], schema=PlanningOutput,
             max_attempts=agent2_plan.MAX_PASSES * agent2_plan.ATTEMPTS_PER_PASS, max_tokens=1000)
    assert "requirements without any story" in str(excinfo.value)


def test_self_correction_stops_early_when_the_plan_is_clean(monkeypatch):
    calls: list[dict] = []

    def fake_structured_call(**kwargs):
        calls.append(kwargs)
        return fake_result(good_plan([1]), "clean")

    monkeypatch.setattr(agent2_plan, "structured_call", fake_structured_call)
    loop = agent2_plan.self_correcting_call(reqs((1, "high")))
    loop(model=object(), messages=[{"role": "user", "content": "r"}], schema=PlanningOutput,
         max_attempts=agent2_plan.MAX_PASSES * agent2_plan.ATTEMPTS_PER_PASS, max_tokens=1000)
    assert len(calls) == 1 and loop.state["soft"] == []


def test_medium_priority_story_ordered_before_high_is_soft():
    two = merged(
        plan(1, ref="S1", order=2, critical=True, rationale="Unblocks the booking UI."),
        plan(2, ref="S2", order=1, rationale="Delivers the reminders the flow promises."),
    )
    output = PlanningOutput.model_validate(two)
    hard, soft = plan_issues(output, reqs((1, "high"), (2, "medium")))
    assert hard == []
    assert any("not all planned before the others" in i for i in soft)


def test_v2_schema_fields_and_order_rules():
    output = PlanningOutput.model_validate(plan(1, order=1, critical=True, rationale="Unblocks the booking UI."))
    assert output.stories[0].order == 1
    assert output.tasks[0].is_critical is True
    assert output.tasks[0].rationale == "Unblocks the booking UI."
    with pytest.raises(ValidationError):
        PlanningOutput.model_validate(plan(1, order=1, critical=True, rationale="fix"))
    with pytest.raises(ValidationError):
        PlanningOutput.model_validate(
            {
                "stories": [plan(1)["stories"][0]],
                "tasks": [
                    {**plan(1)["tasks"][0], "order": 1},
                    {**plan(1)["tasks"][0], "ref": "T2", "title": "Second booking task", "order": 1},
                ],
            }
        )
