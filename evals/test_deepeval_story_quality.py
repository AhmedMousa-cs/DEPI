"""DeepEval "Story Quality" test for Agent 2 (M5 DoD). Run: make eval-agent2-quality.

A real LLM judge call with a small cost; it is a quality smoke check, not a deterministic measurement
(the deterministic Golden Project B checks live in evals/tests/test_golden_project_b.py).
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "backend"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import pytest

from app.config import settings

pytestmark = pytest.mark.skipif(not settings.groq_api_key, reason="GROQ_API_KEY not set")

from deepeval import assert_test  # noqa: E402
from deepeval.metrics import GEval  # noqa: E402
from deepeval.test_case import LLMTestCase, LLMTestCaseParams  # noqa: E402

from evals.planning_metrics import oracle_plan  # noqa: E402
from evals.run_agent2 import load_golden  # noqa: E402
from evals.test_deepeval_smoke import GroqJudge  # noqa: E402


def _render(expected: dict, plan: dict) -> str:
    requirements = {r["requirement_id"]: r for r in expected["requirements"]}
    tasks_by_story: dict[str, list[dict]] = {}
    for task in plan["tasks"]:
        tasks_by_story.setdefault(task["story_ref"], []).append(task)
    lines = []
    for story in plan["stories"]:
        req = requirements[story["requirement_id"]]
        lines.append(f"Requirement R{story['requirement_id']} ({req['type']}): {req['text']}")
        lines.append(f"  Story {story['ref']}: {story['text']}")
        for criterion in story["acceptance_criteria"]:
            lines.append(f"    - AC: {criterion}")
        for task in tasks_by_story.get(story["ref"], []):
            lines.append(f"    - Task {task['ref']}: {task['title']} ({task['estimate_hours']}h)")
    return "\n".join(lines)


def test_geval_story_quality_is_well_formed_and_covered():
    expected = load_golden("project-b")
    plan = oracle_plan(expected)
    actual = _render(expected, plan)
    metric = GEval(
        name="Story Quality",
        criteria=(
            "Each story must be a well-formed user story that clearly covers the requirement listed "
            "immediately before it, must have concrete and testable acceptance criteria, and must have "
            "at least one task under it. Penalize stories that miss their requirement, vague or "
            "untestable criteria, stories with no tasks, and tasks that belong to a different story."
        ),
        evaluation_params=[LLMTestCaseParams.ACTUAL_OUTPUT],
        model=GroqJudge(),
        threshold=0.7,
    )
    assert_test(
        LLMTestCase(input="Turn approved requirements into user stories with tasks", actual_output=actual),
        [metric],
    )
