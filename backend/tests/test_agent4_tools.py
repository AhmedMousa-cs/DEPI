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


def _review_tool_steps(store, steps, *, bundled_reads):
    from langchain.agents import create_agent
    from langchain_core.messages import HumanMessage

    from app.agents.agent4_testing import ScriptedChatModel, final_msg, tool_msg
    from app.tools.plan_tools import Agent4Context

    context = Agent4Context(project_id=1, plan_version_id=1)
    model = ScriptedChatModel(script=[tool_msg(*step) for step in steps] + [final_msg()])
    agent = create_agent(model, tools=build_plan_tools(store, bundled_reads=bundled_reads),
                         context_schema=Agent4Context)
    result = agent.invoke({"messages": [HumanMessage("Review the change.")]}, context=context)
    replies = [(message.name, json.loads(message.content)) for message in result["messages"] if message.type == "tool"]
    return context, replies


def test_bundled_evidence_carries_open_risks_and_the_approved_diff_within_payload_budget():
    approved = sample_snapshot()
    successor = sample_snapshot()
    successor["change_request_id"] = 3
    successor["tasks"][0]["estimate_hours"] = 20
    successor["tasks"].extend({
        **successor["tasks"][0], "id": task_id, "title": "مهمة مراجعة " * 30,
    } for task_id in range(100, 150))
    source = {"project": {"name": "Clinic Booking", "goal": "تطوير المشروع " * 70},
              "brief_untrusted": ["المواصفات المعتمدة " * 500], "injection_flags": []}
    open_risk = {"id": 99, "title": "Downstream handoff", "severity": "high", "likelihood": "medium"}
    store = InMemoryPlanStore(successor, approved_snapshot=approved, brief=source, approved_risks=[open_risk])
    _, [(_, payload)] = _review_tool_steps(store, [(("get_review_evidence", {}),)], bundled_reads=True)
    assert set(payload) == set(READ_TOOL_NAMES)
    assert payload["get_open_risks"]["approved_plan_risks"]["items"] == [[99, "Downstream handoff", "high", "medium"]]
    diff = payload["get_plan_diff"]
    assert diff["against_version"] == 1
    assert ["task", "changed", 11, "estimate_hours", 16, 20, None] in diff["changes"]["items"]
    tasks = payload["get_tasks_and_dependencies"]
    assert tasks["total"] == 54 and tasks["truncated"] and tasks["items"][0][0] == 11
    assert len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) < 9000


def test_bundled_review_waits_for_evidence_then_stages_the_verdict_without_writing():
    store = InMemoryPlanStore(sample_snapshot())
    save = ("save_plan_review", {"verdict": "ready", "summary": "Evidence reviewed."})
    context, replies = _review_tool_steps(store, [
        (("get_review_evidence", {}), save),
        (save,),
    ], bundled_reads=True)
    first, second = [reply for name, reply in replies if name == "save_plan_review"]
    assert not first["saved"] and "prior tool turn" in first["errors"][0]
    assert second["saved"] is True and context.pending_review.verdict == "ready"
    assert store.reviews == []


def test_separate_change_tools_require_open_risks_and_the_approved_diff():
    approved = sample_snapshot()
    successor = sample_snapshot()
    successor["change_request_id"] = 3
    store = InMemoryPlanStore(successor, approved_snapshot=approved)
    save = ("save_draft_risks", {"risks": []})
    _, replies = _review_tool_steps(store, [
        (("get_plan_diff", {"against": "previous"}),),
        (save,),
        (("get_open_risks", {}), ("get_plan_diff", {"against": "approved"})),
        (save,),
    ], bundled_reads=False)
    first, second = [reply for name, reply in replies if name == "save_draft_risks"]
    assert "get_open_risks" in first["errors"][0] and 'get_plan_diff(against="approved")' in first["errors"][0]
    assert second["risk_ids"] == [] and "errors" not in second
