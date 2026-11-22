"""Evaluator adapter for the REAL Agent 1 (M4): same prompt, chunking, de-duplication and quote
verification as production, but no database writes. It returns what the PM would actually see
(confidence already capped for unverified quotes), so the golden metrics measure the product,
not the old v0 baseline."""
from app.agents import agent1_extract
from app.config import settings
from app.llm.factory import get_llm_model
from app.llm.pricing import compute_cost_usd
from app.llm.structured import structured_call
from app.llm.usage import TokenUsage, UsageUnavailable, extract_usage, unwrap_parsed
from app.schemas.extraction import ExtractionOutput


def extract(sources, max_attempts=None):
    """sources: [{"source_id": int, "kind": str, "text": str}] (same shape as the baseline extractor)."""
    agent_sources = [
        {"id": s["source_id"], "kind": s["kind"], "title": f"source {s['source_id']}", "content_text": s["text"]}
        for s in sources
    ]
    flags = agent1_extract.injection_flags(agent_sources)
    model = get_llm_model()
    collected = []
    attempts = tokens_in = tokens_out = 0
    batches = agent1_extract.build_batches(agent_sources)
    for batch in batches:
        result = structured_call(
            model=model,
            messages=agent1_extract.build_messages(batch),
            schema=ExtractionOutput,
            max_attempts=max_attempts or settings.llm_max_attempts,
            max_tokens=settings.llm_max_output_tokens,
        )
        collected.extend(unwrap_parsed(result, ExtractionOutput).requirements)
        used = getattr(result, "attempts", 1)
        attempts += used if isinstance(used, int) else 1
        try:
            usage = extract_usage(result)
        except UsageUnavailable:
            usage = TokenUsage(0, 0)
        tokens_in += usage.input_tokens
        tokens_out += usage.output_tokens
    unique = agent1_extract.dedupe_requirements(collected)
    verified = agent1_extract.verify_extraction(unique, agent_sources)
    items = [{**v.requirement.model_dump(), "confidence": v.confidence} for v in verified]
    cost, priced = compute_cost_usd(settings.model_llm, tokens_in, tokens_out)
    meta = {
        "prompt_version": agent1_extract.PROMPT_VERSION,
        "attempts": attempts,
        "batches": len(batches),
        "input_tokens": tokens_in,
        "output_tokens": tokens_out,
        "cost_usd": str(cost),
        "pricing_configured": priced,
        "injection_flags": {str(k): v for k, v in flags.items()},
    }
    return items, meta