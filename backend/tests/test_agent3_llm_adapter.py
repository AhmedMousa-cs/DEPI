"""Adapter wires repo's structured_call into Agent 3 (fake ChatModel, no network)."""
import json
from datetime import date

from app.agents.agent3_llm import make_structured_llm
from app.agents.agent3_schedule import run_agent3
from app.llm.contracts import LLMUsage, ModelResponse
from app.schemas.scheduling import TaskInput


class FakeModel:
    def __init__(self):
        self.seen = []

    def invoke(self, *, messages, response_schema=None, max_tokens=2000):
        self.seen.append(messages)
        props = (response_schema or {}).get("properties", {})
        if "estimates" in props:
            ids = [t["task_id"] for t in json.loads(messages[1]["content"])["tasks"]]
            body = {"estimates": [{"task_id": i, "duration_days": 2, "rationale": "x"} for i in ids]}
        else:
            body = {"summary": "s", "critical_path": "c"}
        return ModelResponse(content=json.dumps(body), usage=LLMUsage(1, 1, 2, 0.0), model="fake")


def test_run_agent3_through_structured_call():
    m = FakeModel()
    out = run_agent3([TaskInput(id="A", title="t")], [], date(2026, 11, 15), llm=make_structured_llm(m))
    assert out.schedule.total_duration_days == 2 and out.explanation.source == "llm"
    assert m.seen[0][0]["role"] == "system" and m.seen[0][1]["role"] == "user"
