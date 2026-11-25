import deepeval

print("deepeval", deepeval.__version__)

from deepeval.test_case import LLMTestCase, ToolCall, ToolCallParams
from deepeval.metrics import ToolCorrectnessMetric
from deepeval.models import DeepEvalBaseLLM

print("imports ok")


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


m = ToolCorrectnessMetric(
    evaluation_params=[ToolCallParams.INPUT_PARAMETERS, ToolCallParams.OUTPUT],
    should_exact_match=True,
    threshold=1.0,
    include_reason=False,
    async_mode=False,
    model=NoJudge(),
)
case = LLMTestCase(
    input="x",
    actual_output="done",
    tools_called=[ToolCall(name="a", input_parameters={"x": 1}, output={"ok": True})],
    expected_tools=[ToolCall(name="a", input_parameters={"x": 1}, output={"ok": True})],
)
m.measure(case)
print("metric score", m.score, m.success)
