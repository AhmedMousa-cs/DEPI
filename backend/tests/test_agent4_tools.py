"""Agent 4 tools: no ids in the schema, read-only vs draft-only writes, isolation (in-memory store)."""
import json

from langchain.agents.middleware import AgentMiddleware  # noqa: F401  (import check)

from app.agents.agent4_testing import InMemoryPlanStore, sample_snapshot
from app.tools.plan_tools import READ_TOOL_NAMES, WRITE_TOOL_NAMES, build_plan_tools


def tools_by_name():
    return {t.name: t for t in build_plan_tools(InMemoryPlanStore(sample_snapshot()))}


def test_tool_inventory_has_no_way_to_approve_or_change_a_plan():
    names = set(tools_by_name())
    assert names == set(READ_TOOL_NAMES) | set(WRITE_TOOL_NAMES)
    for forbidden in ("approve", "reject", "decide", "delete", "update", "publish", "apply"):
        assert not any(forbidden in n for n in names), forbidden


def test_the_model_never_sees_project_or_version_ids():
    for name, t in tools_by_name().items():
        props = set(t.tool_call_schema.model_json_schema().get("properties", {}))
        assert not props & {"project_id", "plan_version_id", "version", "runtime", "run_id"}, (name, props)


def test_every_tool_has_a_description_for_the_model():
    assert all(len(t.description) > 30 for t in tools_by_name().values())


def run_tool(name, args, store=None, project_id=1, version=1):
    """Execute a tool the way the agent does (ToolNode) with the given context."""
    from langchain.agents import create_agent
    from langchain_core.messages import HumanMessage

    from app.agents.agent4_testing import ScriptedChatModel, final_msg, tool_msg
    from app.tools.plan_tools import Agent4Context

    store = store or InMemoryPlanStore(sample_snapshot())
    model = ScriptedChatModel(script=[tool_msg((name, args)), final_msg()])
    agent = create_agent(model, tools=build_plan_tools(store), context_schema=Agent4Context)
    out = agent.invoke({"messages": [HumanMessage("go")]},
                       context=Agent4Context(project_id=project_id, plan_version_id=version))
    tool_messages = [m for m in out["messages"] if m.type == "tool"]
    content = tool_messages[0].content
    try:
        return json.loads(content)
    except ValueError:  # the tool node reports schema errors as plain text
        return {"error": content}


def test_read_tools_return_the_plan():
    tasks = run_tool("get_tasks_and_dependencies", {})
    assert [t["id"] for t in tasks["items"]] == [11, 12, 13, 14] and tasks["items"][3]["depends_on"] == [12, 13]
    a3 = run_tool("get_agent3_results", {})
    assert a3["schedule"]["project_end"] == "2026-11-22" and a3["conflicts"][0]["assignee_id"] == 101
    assert {c["rule"] for c in a3["automatic_checks"]} >= {"resource_conflict", "overloaded_member"}
    cap = run_tool("get_team_capacity", {})
    assert [m["name"] for m in cap["members"]] == ["Ahmed", "Sara"] and cap["members"][0]["is_overloaded"] is True


def test_wrong_project_in_context_reads_nothing():
    out = run_tool("get_tasks_and_dependencies", {}, project_id=2)
    assert "error" in out and "items" not in out


def test_save_draft_risks_validates_evidence_and_reports_errors_to_the_model():
    bad = {"risks": [{"title": "Ghost evidence", "description": "no such task", "severity": "low",
                      "likelihood": "low", "evidence_refs": [{"type": "task", "id": "999"}]}]}
    out = run_tool("save_draft_risks", bad)
    assert out["saved"] == 0 and "does not exist" in out["errors"][0]


def test_save_draft_risks_accepts_real_evidence():
    store = InMemoryPlanStore(sample_snapshot())
    good = {"risks": [{"title": "API bottleneck", "description": "One person builds it.", "severity": "medium",
                       "likelihood": "medium", "evidence_refs": [{"type": "task", "id": 12}, {"type": "member", "id": "101"}]}]}
    out = run_tool("save_draft_risks", good, store=store)
    assert out["saved"] == 1 and store.agent_risks[0].title == "API bottleneck"


def test_save_draft_risks_schema_has_no_status_field():
    schema = tools_by_name()["save_draft_risks"].tool_call_schema.model_json_schema()
    risk = schema["$defs"]["RiskDraft"]["properties"]
    assert "status" not in risk and {"severity", "likelihood", "evidence_refs"} <= set(risk)


def test_get_plan_health_returns_the_deterministic_brief():
    from app.services.plan_health import plan_health

    snap = sample_snapshot()
    out = run_tool("get_plan_health", {}, store=InMemoryPlanStore(snap))
    assert out == plan_health(snap)
    assert out["delivery_signal"] == "at_risk" and out["workload"][0]["name"] == "Ahmed"


def test_save_plan_review_stores_confidence_recommendation_and_questions():
    store = InMemoryPlanStore(sample_snapshot())
    out = run_tool("save_plan_review", {"verdict": "needs_attention", "summary": "Ahmed is double booked.",
                                        "findings": ["Ahmed owns 3 of 4 tasks"],
                                        "confidence": "high", "recommendation": "approve_with_changes",
                                        "open_questions": ["Can Sara take the UI task?"]}, store=store)
    assert out["saved"] is True
    review = store.snapshot(1, 1)["review"]
    assert (review["confidence"], review["recommendation"]) == ("high", "approve_with_changes")
    assert review["open_questions"] == ["Can Sara take the UI task?"]


def test_a_review_without_a_recommendation_gets_the_one_matching_its_verdict():
    store = InMemoryPlanStore(sample_snapshot())
    run_tool("save_plan_review", {"verdict": "blocked", "summary": "Unreachable deadline."}, store=store)
    assert store.snapshot(1, 1)["review"]["recommendation"] == "revise"


def test_recommending_approve_with_a_non_ready_verdict_is_rejected():
    store = InMemoryPlanStore(sample_snapshot())
    out = run_tool("save_plan_review", {"verdict": "needs_attention", "summary": "Two serious issues.",
                                        "recommendation": "approve"}, store=store)
    assert out["saved"] is False and "requires verdict 'ready'" in out["errors"][0]
    assert store.reviews == []  # nothing contradictory was written


def test_more_than_eight_risks_is_refused_instead_of_truncated_silently():
    store = InMemoryPlanStore(sample_snapshot())
    risks = [{"title": f"Risk number {i}", "description": "A real description.", "severity": "low",
              "likelihood": "low", "evidence_refs": [{"type": "task", "id": 11}]} for i in range(9)]
    out = run_tool("save_draft_risks", {"risks": risks}, store=store)
    assert out["saved"] == 0 and "at most 8" in out["errors"][0] and store.agent_risks == []


def test_save_plan_review_does_not_approve_anything():
    store = InMemoryPlanStore(sample_snapshot())
    out = run_tool("save_plan_review", {"verdict": "ready", "summary": "All good.", "findings": []}, store=store)
    assert out == {"saved": True, "verdict": "ready", "notes": []}
    assert store.snapshot(1, 1)["review"]["source"] == "agent4"


def test_confidence_is_capped_by_the_evidence_the_plan_provides():
    snap = sample_snapshot(target_end=None)
    snap["tasks"][0]["assignee_member_id"] = None
    store = InMemoryPlanStore(snap)
    out = run_tool("save_plan_review", {"verdict": "needs_attention", "summary": "One task has no owner.",
                                        "confidence": "high"}, store=store)
    assert out["saved"] is True
    review = store.snapshot(1, 1)["review"]
    assert review["confidence"] == "medium"
    limitations = " | ".join(review["limitations"])
    assert "confidence lowered from high to medium" in limitations
    assert "no target end date" in limitations and "1 task(s) have no assignee" in limitations
    assert limitations in " | ".join(out["notes"])


def test_a_blocked_verdict_forces_a_revise_recommendation():
    store = InMemoryPlanStore(sample_snapshot())
    out = run_tool("save_plan_review", {"verdict": "blocked", "summary": "The deadline is unreachable.",
                                        "recommendation": "approve_with_changes"}, store=store)
    assert out["saved"] is True
    review = store.snapshot(1, 1)["review"]
    assert review["recommendation"] == "revise"
    assert any("forced to 'revise'" in x for x in review["limitations"])


def test_invalid_verdict_is_rejected_before_saving():
    store = InMemoryPlanStore(sample_snapshot())
    out = run_tool("save_plan_review", {"verdict": "approved", "summary": "sneaky"}, store=store)
    assert store.reviews == [] and "error" in out and "ready" in out["error"]
