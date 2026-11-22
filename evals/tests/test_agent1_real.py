import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT, ROOT / "backend"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from app.agents import agent1_extract  # noqa: E402
from app.llm.usage import TokenUsage  # noqa: E402
from app.schemas.extraction import ExtractedRequirement, ExtractionOutput  # noqa: E402
from evals import agent1_real  # noqa: E402

TEXT = "PM: The clinic needs online booking for patients and nothing else."


def test_real_agent1_adapter(monkeypatch):
    items = [
        ExtractedRequirement(type="functional", text="Online booking", source_id=1,
                             exact_quote="needs online booking", confidence=0.9),
        ExtractedRequirement(type="feature", text="Invented thing", source_id=1,
                             exact_quote="this sentence is not in the source", confidence=0.95),
    ]
    monkeypatch.setattr(agent1_real, "get_llm_model", lambda: object())
    monkeypatch.setattr(agent1_real, "structured_call",
                        lambda **kw: SimpleNamespace(parsed=ExtractionOutput(requirements=items), attempts=1))
    monkeypatch.setattr(agent1_real, "unwrap_parsed", lambda result, schema: result.parsed)
    monkeypatch.setattr(agent1_real, "extract_usage", lambda result: TokenUsage(10, 5))
    out, meta = agent1_real.extract([{"source_id": 1, "kind": "transcript", "text": TEXT}])
    assert meta["prompt_version"] == agent1_extract.PROMPT_VERSION
    by_text = {i["text"]: i for i in out}
    assert by_text["Online booking"]["confidence"] == 0.9
    assert by_text["Invented thing"]["confidence"] <= 0.3
    assert meta["input_tokens"] == 10 and meta["output_tokens"] == 5