"""Agent 4 tool use, scored with DeepEval's ToolCorrectnessMetric (deterministic, no LLM, no API key).

Positive cases must score 1.0; the negative cases prove the metric really catches wrong tools,
wrong inputs, wrong outputs, a missing step and a forbidden `approve` attempt.
"""
import pytest
from deepeval.test_case import ToolCall

from app.agents.agent4_testing import final_msg, happy_path_script, sample_snapshot, tool_msg
from evals.agent4_harness import evaluate, expected_reads, run_scenario

RISK = {"title": "API is the bottleneck", "description": "Both backend tasks sit on one person.",
        "severity": "medium", "likelihood": "medium", "mitigation": "Pair up.",
        "evidence_refs": [{"type": "task", "id": "12"}, {"type": "member", "id": "101"}]}
REVIEW = {"verdict": "needs_attention", "summary": "Ahmed is double booked.", "findings": ["conflict on Ahmed"]}


def expected_happy(snapshot, store):
    return expected_reads(snapshot, store) + [
        ToolCall(name="save_draft_risks", input_parameters={"risks": [RISK]},
                 output={"saved": 1, "skipped_duplicates": 0, "risk_ids": [1]}),
        ToolCall(name="save_plan_review", input_parameters=REVIEW, output={"saved": True, "verdict": "needs_attention"}),
    ]


@pytest.fixture
def snap():
    return sample_snapshot()


def happy(snap, **kw):
    script = happy_path_script(risks=[RISK], verdict=REVIEW["verdict"], summary=REVIEW["summary"],
                               findings=REVIEW["findings"])
    return run_scenario(snap, script, **kw)


def test_correct_tools_inputs_and_outputs_score_perfectly(snap):
    result, store = happy(snap)
    score, ok = evaluate(result, expected_happy(snap, store))
    assert (score, ok) == (1.0, True)


def test_read_outputs_match_what_the_snapshot_says(snap):
    result, store = happy(snap)
    reads_only = [c for c in expected_reads(snap, store)]
    score, _ = evaluate(result, reads_only + expected_happy(snap, store)[5:])
    assert score == 1.0


def test_a_retry_after_invalid_evidence_is_scored_as_the_expected_two_step_flow(snap):
    bad = {**RISK, "evidence_refs": [{"type": "task", "id": "999"}]}
    script = [tool_msg(("save_draft_risks", {"risks": [bad]})), tool_msg(("save_draft_risks", {"risks": [RISK]})),
              tool_msg(("save_plan_review", REVIEW)), final_msg()]
    result, store = run_scenario(snap, script)
    expected = [
        ToolCall(name="save_draft_risks", input_parameters={"risks": [bad]}, output={
            "saved": 0, "errors": ["risk 'API is the bottleneck': evidence task:999 does not exist in this plan/project"]}),
        ToolCall(name="save_draft_risks", input_parameters={"risks": [RISK]},
                 output={"saved": 1, "skipped_duplicates": 0, "risk_ids": [1]}),
        ToolCall(name="save_plan_review", input_parameters=REVIEW, output={"saved": True, "verdict": "needs_attention"}),
    ]
    assert evaluate(result, expected)[0] == 1.0


def test_wrong_tool_is_penalised(snap):
    script = [tool_msg(("get_plan_diff", {"against": "previous"})), tool_msg(("save_plan_review", REVIEW)), final_msg()]
    result, store = run_scenario(snap, script)
    score, ok = evaluate(result, expected_happy(snap, store))
    assert score < 1.0 and not ok


def test_missing_review_step_is_penalised(snap):
    script = [tool_msg(*[(n, {}) for n in ("get_project_brief", "get_agent3_results")]),
              tool_msg(("save_draft_risks", {"risks": [RISK]})), final_msg()]
    result, store = run_scenario(snap, script)
    assert evaluate(result, expected_happy(snap, store), exact=False)[0] < 1.0


def test_wrong_input_parameters_are_penalised(snap):
    other = {**RISK, "evidence_refs": [{"type": "task", "id": "11"}]}
    script = happy_path_script(risks=[other], verdict=REVIEW["verdict"], summary=REVIEW["summary"],
                               findings=REVIEW["findings"])
    result, store = run_scenario(snap, script)
    score, ok = evaluate(result, expected_happy(snap, store), exact=False)
    assert score < 1.0 and not ok


def test_wrong_tool_output_is_penalised(snap):
    result, store = happy(snap)
    expected = expected_happy(snap, store)
    expected[1] = ToolCall(name="get_tasks_and_dependencies", input_parameters={},
                           output={"items": [], "truncated": False, "total": 0})
    assert evaluate(result, expected, exact=False)[0] < 1.0


def test_an_attempt_to_approve_the_plan_is_caught_by_exact_matching(snap):
    script = happy_path_script(risks=[RISK], verdict=REVIEW["verdict"], summary=REVIEW["summary"],
                               findings=REVIEW["findings"])
    script.insert(3, tool_msg(("approve_plan", {"version": 1})))
    result, store = run_scenario(snap, script)
    score, ok = evaluate(result, expected_happy(snap, store), exact=True, check_output=False)
    assert score == 0.0 and not ok
    assert any(c.name == "approve_plan" for c in result.tool_calls)  # visible in the trace, and harmless
    assert [src for _r, src in store.reviews] == ["agent4"]  # nothing but the one review was written


def test_clean_plan_flow_also_scores_perfectly():
    snap = sample_snapshot(overlap=False)
    snap["tasks"][2]["total_float"] = 1
    snap["schedule"]["critical_task_ids"], snap["schedule"]["critical_path"] = [11], [11]
    review = {"verdict": "ready", "summary": "Looks fine.", "findings": []}
    script = [tool_msg(*[(n, {}) for n in ("get_project_brief", "get_agent3_results", "get_tasks_and_dependencies",
                                           "get_team_capacity", "get_open_risks")]),
              tool_msg(("save_plan_review", review)), final_msg()]
    result, store = run_scenario(snap, script)
    expected = expected_reads(snap, store) + [
        ToolCall(name="save_plan_review", input_parameters=review, output={"saved": True, "verdict": "ready"})]
    assert evaluate(result, expected)[0] == 1.0
