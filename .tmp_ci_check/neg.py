import deepeval
from deepeval.metrics import ToolCorrectnessMetric
from deepeval.models import DeepEvalBaseLLM
from deepeval.test_case import LLMTestCase, ToolCall, ToolCallParams

print("deepeval", deepeval.__version__)


class NoJudge(DeepEvalBaseLLM):
    def __init__(self):
        self.model = None

    def load_model(self):
        return None

    def generate(self, *a, **k):
        raise RuntimeError("no llm")

    async def a_generate(self, *a, **k):
        raise RuntimeError("no llm")

    def get_model_name(self):
        return "none"


def score(called, expected, *, exact, check_output=True):
    params = [ToolCallParams.INPUT_PARAMETERS]
    if check_output:
        params.append(ToolCallParams.OUTPUT)
    m = ToolCorrectnessMetric(
        evaluation_params=params,
        should_exact_match=exact,
        threshold=1.0,
        include_reason=False,
        async_mode=False,
        model=NoJudge(),
    )
    case = LLMTestCase(
        input="x", actual_output="done", tools_called=called, expected_tools=expected
    )
    m.measure(case)
    return m.score, m.success


good = ToolCall(name="a", input_parameters={"x": 1}, output={"ok": True})
good2 = ToolCall(name="b", input_parameters={}, output={"ok": True})

cases = {
    "perfect": (score([good], [good], exact=True), (1.0, True)),
    "wrong_output_exact": (
        score([ToolCall(name="a", input_parameters={"x": 1}, output={"ok": False})], [good], exact=True),
        "score<1",
    ),
    "wrong_input_loose": (
        score([ToolCall(name="a", input_parameters={"x": 2}, output={"ok": True})], [good], exact=False),
        "score<1",
    ),
    "extra_tool_exact_no_output": (
        score([good, ToolCall(name="approve_plan", input_parameters={"version": 1})], [good],
              exact=True, check_output=False),
        (0.0, False),
    ),
    "missing_tool_loose": (score([good], [good, good2], exact=False), "score<1"),
}

for name, (got, want) in cases.items():
    print(f"{name}: got={got} want={want}")
