"""Offline harness for evaluating Agent 4's TOOL USE with DeepEval's ToolCorrectnessMetric.

ToolCorrectnessMetric answers: did the agent pick the right tools, with the right input
parameters, and did the calls return the right outputs? Here it runs deterministically (no judge
LLM is ever called), so it can gate every PR:
  * scripted model  -> proves the pipeline, tools and metric wiring (evals/tests)
  * real model      -> evals/run_agent4.py scores the same scenarios with a live LLM

Canonical order: independent reads may run in parallel in any order, so reads are compared sorted
by name; the WRITE tools keep their real order (risks first, then the review).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "backend"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import os  # noqa: E402

os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")

from deepeval.metrics import ToolCorrectnessMetric  # noqa: E402
from deepeval.models import DeepEvalBaseLLM  # noqa: E402
from deepeval.test_case import LLMTestCase, ToolCall, ToolCallParams  # noqa: E402

from app.agents import agent4_plan  # noqa: E402
from app.agents.agent4_testing import InMemoryPlanStore, NullLedger, ScriptedChatModel  # noqa: E402
from app.services.plan_health import plan_health  # noqa: E402
from app.services.plan_rules import run_rules  # noqa: E402
from app.tools.plan_tools import READ_TOOL_NAMES  # noqa: E402


class NoJudge(DeepEvalBaseLLM):
    """ToolCorrectness is deterministic here; this stub guarantees no judge call (and needs no API key)."""

    def __init__(self):
        self.model = None

    def load_model(self):
        return None

    def generate(self, *a, **k):  # pragma: no cover
        raise RuntimeError("ToolCorrectness must not call an LLM in deterministic mode")

    async def a_generate(self, *a, **k):  # pragma: no cover
        raise RuntimeError("ToolCorrectness must not call an LLM in deterministic mode")

    def get_model_name(self):
        return "none"


PARAMS_AND_OUTPUT = [ToolCallParams.INPUT_PARAMETERS, ToolCallParams.OUTPUT]


def make_metric(*, exact: bool = True, check_output: bool = True, threshold: float = 1.0) -> ToolCorrectnessMetric:
    return ToolCorrectnessMetric(
        evaluation_params=PARAMS_AND_OUTPUT if check_output else [ToolCallParams.INPUT_PARAMETERS],
        should_exact_match=exact, threshold=threshold, include_reason=False, async_mode=False, model=NoJudge())


def canonical(calls: list[ToolCall]) -> list[ToolCall]:
    reads = sorted((c for c in calls if c.name in READ_TOOL_NAMES), key=lambda c: c.name)
    writes = [c for c in calls if c.name not in READ_TOOL_NAMES]
    return reads + writes


def _parse(output: str):
    try:
        return json.loads(output)
    except ValueError:
        return output


def called_tools(records) -> list[ToolCall]:
    return canonical([ToolCall(name=r.name, input_parameters=r.args, output=_parse(r.output)) for r in records])


# ---- expected outputs of the read tools, computed straight from the snapshot (independent of the tools)
def expected_reads(snapshot: dict, store: InMemoryPlanStore) -> list[ToolCall]:
    keep = ("id", "title", "assignee_member_id", "duration_days", "estimate_hours", "start", "end",
            "total_float", "is_critical", "depends_on")
    tasks = [{k: t[k] for k in keep} for t in snapshot["tasks"]]
    members = []
    team = {m["id"]: m for m in store.team(1)}
    for m in snapshot["capacity"]["members"]:
        extra = team[m["member_id"]]
        members.append({**m, "skills": extra["skills"], "available_from": extra["available_from"],
                        "available_to": extra["available_to"]})
    return [
        ToolCall(name="get_project_brief", input_parameters={}, output=store.brief(1)),
        ToolCall(name="get_plan_health", input_parameters={}, output=plan_health(snapshot)),
        ToolCall(name="get_tasks_and_dependencies", input_parameters={},
                 output={"items": tasks, "truncated": False, "total": len(tasks)}),
        ToolCall(name="get_agent3_results", input_parameters={}, output={
            "schedule": snapshot["schedule"], "target_end_date": snapshot["project"].get("end_date"),
            "conflicts": snapshot["conflicts"],
            "estimates": [{"task_id": e["task_id"], "source": e["source"], "duration_days": e["duration_days"]}
                          for e in snapshot["estimates"]],
            "explanation": snapshot["explanation"], "pm_overrides": snapshot["overrides"],
            "automatic_checks": [{"rule": f.rule, "title": f.risk.title, "severity": f.risk.severity}
                                 for f in run_rules(snapshot)]}),
        ToolCall(name="get_team_capacity", input_parameters={},
                 output={"members": members, "unknown_assignees": snapshot["capacity"]["unknown_assignees"]}),
        ToolCall(name="get_open_risks", input_parameters={}, output={"approved_plan_risks": [], "draft_risks": []}),
    ]


def run_scenario(snapshot: dict, script=None, *, model=None, **store_kw):
    """Run the real Agent 4 pipeline (tools, middleware, rules) on an in-memory store."""
    store = InMemoryPlanStore(snapshot, **store_kw)
    model = model or ScriptedChatModel(script=script or [])
    result = agent4_plan.run_agent4(project_id=snapshot["project"]["id"], plan_version_id=snapshot["_version_id"],
                                    store=store, model=model, ledger=NullLedger(), audit_sink=None)
    return result, store


def evaluate(result, expected: list[ToolCall], *, exact=True, check_output=True, name="agent4") -> tuple[float, bool]:
    case = LLMTestCase(input=f"Review the draft plan ({name})", actual_output="done",
                       tools_called=called_tools(result.tool_calls), expected_tools=canonical(expected))
    metric = make_metric(exact=exact, check_output=check_output)
    metric.measure(case)
    return metric.score, metric.success
