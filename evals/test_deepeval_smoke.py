"""DeepEval smoke test (M3 DoD). Run: make eval. Real LLM call, small cost. Not a quality measurement."""
import json
import re
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
from deepeval.models import DeepEvalBaseLLM  # noqa: E402
from deepeval.test_case import LLMTestCase, LLMTestCaseParams  # noqa: E402

from app.llm.factory import get_llm_model  # noqa: E402
from evals.run_agent1 import load_golden, oracle_items  # noqa: E402


class GroqJudge(DeepEvalBaseLLM):
    def __init__(self):
        self.model = get_llm_model(model_name=settings.model_judge_eval)

    def load_model(self):
        return self.model

    def generate(self, prompt: str, schema=None):
        kwargs = {"messages": [{"role": "user", "content": prompt}], "max_tokens": settings.llm_max_output_tokens}
        if schema is not None:
            kwargs["response_schema"] = schema.model_json_schema()
        text = self.model.invoke(**kwargs).content
        if schema is None:
            return text
        body = text[text.find("{"): text.rfind("}") + 1]
        return schema.model_validate(json.loads(re.sub(r"^```(json)?|```$", "", body.strip())))

    async def a_generate(self, prompt: str, schema=None):
        return self.generate(prompt, schema)

    def get_model_name(self):
        return settings.model_judge_eval


def test_geval_requirements_are_supported_by_their_quotes():
    expected, _, _ = load_golden("project-a")
    items = oracle_items(expected)[:5]
    actual = "\n".join(f"- {i['text']} | quote: {i['exact_quote']}" for i in items)
    metric = GEval(
        name="Requirement faithfulness",
        criteria=("Each requirement must be directly supported by the quote written after it. "
                  "Penalize any requirement that adds information not present in its quote."),
        evaluation_params=[LLMTestCaseParams.ACTUAL_OUTPUT],
        model=GroqJudge(),
        threshold=0.7,
    )
    assert_test(LLMTestCase(input="Extract requirements with supporting quotes", actual_output=actual), [metric])
