"""The single entry point agents use for LLM calls: budget -> call -> usage/cost -> persist -> trace -> audit."""
import hashlib
import json
import math
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable

from pydantic import BaseModel

from app.config import settings
from app.llm.factory import get_llm_model
from app.llm.pricing import compute_cost_usd
from app.llm.structured import structured_call
from app.llm.usage import TokenUsage, UsageUnavailable, extract_usage, unwrap_parsed
from app.observability.langfuse import Tracer, get_tracer
from app.observability.redact import redact
from app.services import agent_runs


class PricingNotConfigured(RuntimeError):
    pass


@dataclass(frozen=True)
class StructuredRun:
    parsed: BaseModel
    run_id: int
    usage: TokenUsage
    cost_usd: Decimal
    pricing_configured: bool
    attempts: int
    trace_id: str | None


def estimate_input_tokens(messages: list[dict]) -> int:
    """Deliberately pessimistic (2 chars/token) so mixed Arabic/English cannot slip past the budget."""
    return max(1, math.ceil(sum(len(str(m.get("content", ""))) for m in messages) / 2))


def run_structured(*, project_id: int, job_id: int | None, agent: str, prompt_version: str, messages: list[dict],
                   schema: type[BaseModel], user_role: str = "system", max_attempts: int | None = None,
                   model_factory: Callable[[], Any] = get_llm_model, call: Callable[..., Any] = structured_call,
                   tracer: Tracer | None = None) -> StructuredRun:
    limit = max_attempts or settings.llm_max_attempts
    model_name = settings.model_llm
    est_in = estimate_input_tokens(messages)
    reserve_tokens = limit * (est_in + settings.llm_max_output_tokens)
    reserve_cost, priced = compute_cost_usd(model_name, limit * est_in, limit * settings.llm_max_output_tokens)
    if not priced and settings.llm_require_pricing:
        raise PricingNotConfigured(f"no pricing configured for {model_name}")
    input_hash = hashlib.sha256(json.dumps(messages, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    run_id = agent_runs.reserve_run(project_id=project_id, job_id=job_id, agent=agent, model=model_name,
                                    prompt_version=prompt_version, input_hash=input_hash,
                                    reserve_tokens=reserve_tokens, reserve_cost=reserve_cost)
    tracer = tracer or get_tracer()
    started = time.monotonic()
    meta = {"project_id": project_id, "job_id": job_id, "agent": agent, "user_role": user_role,
            "prompt_version": prompt_version, "model": model_name}
    with tracer.generation(name=agent, model=model_name, metadata=meta, session_id=project_id) as gen:
        try:
            result = call(model=model_factory(), messages=messages, schema=schema, max_attempts=limit,
                          max_tokens=settings.llm_max_output_tokens)
            parsed = unwrap_parsed(result, schema)
            try:
                usage, note = extract_usage(result), None
            except UsageUnavailable:
                usage, note = TokenUsage(0, 0), "usage not reported by the provider"
        except Exception as exc:  # noqa: BLE001
            latency = int((time.monotonic() - started) * 1000)
            code = getattr(exc, "code", None)
            code = code if isinstance(code, str) and code else "llm_error"
            message = redact(f"{type(exc).__name__}: {exc}")
            gen.finish(error=message)
            agent_runs.fail_run(run_id, error_code=code, error=message, latency_ms=latency, trace_id=gen.trace_id)
            raise
        latency = int((time.monotonic() - started) * 1000)
        cost, priced = compute_cost_usd(model_name, usage.input_tokens, usage.output_tokens)
        gen.finish(usage=usage, cost=cost)
        attempts = getattr(result, "attempts", 1)
        agent_runs.complete_run(run_id, input_tokens=usage.input_tokens, output_tokens=usage.output_tokens, cost=cost,
                                latency_ms=latency, trace_id=gen.trace_id,
                                attempts=attempts if isinstance(attempts, int) else 1,
                                note=note or (None if priced else "pricing not configured"))
    return StructuredRun(parsed, run_id, usage, cost, priced, attempts if isinstance(attempts, int) else 1, gen.trace_id)
