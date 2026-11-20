"""Score Agent 4's tool use with a REAL model (needs the LLM key). Run: make eval-agent4

Runs the same scenarios as evals/tests/test_agent4_tool_correctness.py but with the live LLM, and
scores them with ToolCorrectnessMetric. A real model words its risks differently every run, so the
score here checks the TOOL NAMES and the (deterministic) READ outputs, not the free-text risk
arguments; those are validated by the tools themselves (evidence must exist) and counted below.
Results go to evals/results/agent4_tool_correctness.json.
"""
import json
import sys
from pathlib import Path

from evals.agent4_harness import called_tools, evaluate, expected_reads, make_metric, run_scenario  # noqa: F401
from app.agents.agent4_testing import InMemoryPlanStore, sample_snapshot
from app.config import settings
from app.llm.langchain_model import get_langchain_model
from deepeval.test_case import ToolCall

RESULTS = Path(__file__).resolve().parent / "results" / "agent4_tool_correctness.json"
WRITES = ("save_draft_risks", "save_plan_review")


def scenarios():
    clean = sample_snapshot(overlap=False)
    clean["tasks"][2]["total_float"] = 1
    clean["schedule"]["critical_task_ids"], clean["schedule"]["critical_path"] = [11], [11]
    return {
        "double_booked": sample_snapshot(),
        "deadline_blown": sample_snapshot(target_end="2026-11-18"),
        "clean_plan": clean,
    }


def main() -> int:
    if not settings.groq_api_key and not settings.anthropic_api_key and settings.llm_provider != "ollama":
        print("No LLM configured (GROQ_API_KEY / ANTHROPIC_API_KEY / ollama).")
        return 2
    model = get_langchain_model()
    rows = []
    for name, snap in scenarios().items():
        result, store = run_scenario(snap, model=model)
        reads = expected_reads(snap, InMemoryPlanStore(snap))
        expected = reads + [ToolCall(name=n, input_parameters={}, output=None) for n in WRITES]
        got = called_tools(result.tool_calls)
        called = {c.name for c in got}
        # name-level score: every expected tool was used; reads must also return the right data
        from deepeval.test_case import LLMTestCase, ToolCallParams
        from evals.agent4_harness import NoJudge
        from deepeval.metrics import ToolCorrectnessMetric

        metric = ToolCorrectnessMetric(evaluation_params=[], should_exact_match=False, threshold=0.8,
                                       include_reason=False, async_mode=False, model=NoJudge())
        metric.measure(LLMTestCase(input=name, actual_output="done", tools_called=got, expected_tools=expected))
        read_out_ok = all(g.output == e.output for g in got for e in reads if g.name == e.name)
        saved_ok = [c for c in result.tool_calls if c.name == "save_draft_risks" and '"saved":' in c.output.replace(" ", "")
                    and '"errors"' not in c.output]
        rows.append({"scenario": name, "tool_correctness": metric.score, "success": metric.success,
                     "tools_called": sorted(called), "reads_return_expected_data": read_out_ok,
                     "risk_saves_accepted": len(saved_ok), "verdict": result.verdict,
                     "review_source": result.review_source, "stopped": result.stopped_reason,
                     "tokens": result.tokens_in + result.tokens_out})
        print(rows[-1])
    RESULTS.parent.mkdir(exist_ok=True)
    RESULTS.write_text(json.dumps({"model": settings.model_llm, "prompt_version": "agent4_v1", "rows": rows}, indent=2))
    print("saved", RESULTS)
    return 0 if all(r["success"] for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
