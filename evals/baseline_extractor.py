"""Baseline Agent-1 extractor used only by the evaluator (the real Agent 1 is built in M4)."""
from pathlib import Path

from app.config import settings
from app.llm.factory import get_llm_model
from app.llm.pricing import compute_cost_usd
from app.llm.structured import structured_call
from app.llm.usage import TokenUsage, UsageUnavailable, extract_usage, unwrap_parsed
from app.schemas.extraction import ExtractionOutput
from app.services.injection_guard import detect_injection, wrap_untrusted

PROMPT_VERSION = "agent1_extract_v0"
PROMPT_PATH = Path(__file__).resolve().parents[1] / "backend" / "app" / "agents" / "prompts" / f"{PROMPT_VERSION}.md"


def extract(sources: list[dict], max_attempts: int | None = None) -> tuple[list[dict], dict]:
    """sources: [{"source_id": int, "kind": str, "text": str}]"""
    system = PROMPT_PATH.read_text(encoding="utf-8")
    user = "\n\n".join(wrap_untrusted(source_id=s["source_id"], kind=s["kind"], text=s["text"]) for s in sources)
    flags = {s["source_id"]: detect_injection(s["text"]) for s in sources}
    result = structured_call(
        model=get_llm_model(),
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        schema=ExtractionOutput,
        max_attempts=max_attempts or settings.llm_max_attempts,
        max_tokens=settings.llm_max_output_tokens,
    )
    parsed = unwrap_parsed(result, ExtractionOutput)
    try:
        usage = extract_usage(result)
    except UsageUnavailable:
        usage = TokenUsage(0, 0)
    cost, priced = compute_cost_usd(settings.model_llm, usage.input_tokens, usage.output_tokens)
    meta = {
        "prompt_version": PROMPT_VERSION,
        "attempts": getattr(result, "attempts", None),
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cost_usd": str(cost),
        "pricing_configured": priced,
        "injection_flags": {str(k): v for k, v in flags.items() if v},
    }
    return [r.model_dump() for r in parsed.requirements], meta
