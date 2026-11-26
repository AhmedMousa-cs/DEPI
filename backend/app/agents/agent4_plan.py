"""Agent 4 - plan review. Built with LangChain v1 ``create_agent`` (a real tool loop).

Pipeline (run_agent4):
  1. deterministic rules over the draft snapshot          (always, code)
  2. agent loop: read tools -> save_draft_risks -> save_plan_review   (LLM, draft-only writes)
  3. rule risks saved as drafts; the verdict can only be RAISED by the rules, never lowered
  4. run accounting: agent_runs (+budget), Langfuse generation, audit rows per tool call
The agent has no approve/reject tool. Approval happens only in app/orchestrator/plan_gate.py.
"""
from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Protocol

from langchain.agents import create_agent
from langchain.agents.middleware import (
    ModelCallLimitMiddleware,
    SummarizationMiddleware,
    ToolCallLimitMiddleware,
)
from langchain_core.messages import HumanMessage
from langgraph.errors import GraphRecursionError

from app.agents.agent4_middleware import (
    AgentBudgetStop, BudgetMiddleware, FinishAfterReviewMiddleware, ToolAuditMiddleware,
)
from app.audit import write_audit
from app.config import settings
from app.db.session import SessionLocal
from app.llm.pricing import compute_cost_usd
from app.llm.usage import TokenUsage
from app.observability.langfuse import Tracer, get_tracer
from app.schemas.plan import RECOMMENDATION_FOR_VERDICT, PlanReviewDraft
from app.services.plan_health import cap_confidence, confidence_cap, plan_health
from app.services.plan_rules import (
    VERDICT_ORDER, RuleFinding, risk_verdict_floor, rules_review, run_rules, verdict_floor,
)
from app.tools.plan_tools import Agent4Context, PlanStore, ToolCallRecord, build_plan_tools

log = logging.getLogger("nexus.agent4")

AGENT = "agent4_plan"
PROMPT_VERSION = "agent4_v3"
TASK_MESSAGE = "Review the current draft plan now, following your instructions."
_PROMPT = Path(__file__).parent / "prompts" / "agent4_review_v3.md"


class Agent4ReviewIncomplete(RuntimeError):
    """The live review ended without a valid structured verdict."""


def load_prompt() -> str:
    return _PROMPT.read_text(encoding="utf-8")


# ------------------------------------------------------------------------- run ledger
class RunLedger(Protocol):
    def start(self, *, project_id: int, job_id: int | None, model: str, input_hash: str, reserve_tokens: int) -> int | None: ...
    def complete(self, run_id: int | None, *, tokens_in: int, tokens_out: int, cost: Decimal, latency_ms: int,
                 trace_id: str | None, note: str | None) -> None: ...
    def fail(self, run_id: int | None, *, error: str, latency_ms: int, trace_id: str | None,
             tokens_in: int, tokens_out: int, cost: Decimal) -> None: ...


class DbRunLedger:
    """agent_runs persistence + token/daily budget (same service the structured agents use)."""

    def start(self, *, project_id, job_id, model, input_hash, reserve_tokens):
        from app.services import agent_runs

        reserve_cost, _ = compute_cost_usd(model, int(reserve_tokens * 0.8), int(reserve_tokens * 0.2))
        return agent_runs.reserve_run(project_id=project_id, job_id=job_id, agent=AGENT, model=model,
                                      prompt_version=PROMPT_VERSION, input_hash=input_hash,
                                      reserve_tokens=reserve_tokens, reserve_cost=reserve_cost)

    def complete(self, run_id, *, tokens_in, tokens_out, cost, latency_ms, trace_id, note):
        from app.services import agent_runs

        agent_runs.complete_run(run_id, input_tokens=tokens_in, output_tokens=tokens_out, cost=cost,
                                latency_ms=latency_ms, trace_id=trace_id, attempts=1, note=note)

    def fail(self, run_id, *, error, latency_ms, trace_id, tokens_in, tokens_out, cost):
        from app.services import agent_runs

        agent_runs.fail_run(run_id, error_code="agent_error", error=error, latency_ms=latency_ms, trace_id=trace_id,
                            input_tokens=tokens_in, output_tokens=tokens_out, cost=cost)


def db_audit_sink(ctx: Agent4Context, rec: ToolCallRecord) -> None:
    import json

    details = {"tool": rec.name, "args": json.dumps(rec.args, default=str, ensure_ascii=False)[:1000],
               "ok": rec.error is None, "run_id": ctx.run_id, "project_id": ctx.project_id}
    if rec.name == "get_review_evidence" and rec.error is None and _is_json(rec.output):
        evidence = json.loads(rec.output)
        if "get_plan_diff" in evidence:
            details["evidence"] = {"sections": sorted(evidence),
                                   "against_version": evidence["get_plan_diff"].get("against_version")}
    with SessionLocal() as db:
        write_audit(db, f"agent:{AGENT}", "agent4.tool_call", "plan_version", ctx.plan_version_id,
                    after=details)
        db.commit()


# ------------------------------------------------------------------------------ agent
def build_agent4(model, store: PlanStore, *, summarizer=None, audit_sink: Callable | None = None,
                 budget_tokens: int | None = None, bundled_reads: bool = False):
    middleware = [ToolAuditMiddleware(audit_sink)]
    if bundled_reads:
        middleware.append(FinishAfterReviewMiddleware())
    middleware += [
        SummarizationMiddleware(
            model=summarizer or model,
            trigger=("tokens", settings.agent4_summary_trigger_tokens),
            keep=("messages", settings.agent4_summary_keep_messages),
        ),
        BudgetMiddleware(budget_tokens or settings.max_tokens_per_job),
        ModelCallLimitMiddleware(run_limit=settings.agent4_max_model_calls, exit_behavior="end"),
        ToolCallLimitMiddleware(run_limit=settings.agent4_max_tool_calls, exit_behavior="continue"),
    ]
    return create_agent(model, tools=build_plan_tools(store, bundled_reads=bundled_reads), system_prompt=load_prompt(),
                        middleware=middleware, context_schema=Agent4Context, name=AGENT)


@dataclass
class Agent4Result:
    plan_version_id: int
    verdict: str
    review_source: str  # "agent4" | "rules"
    run_id: int | None
    agent_risks_saved: int
    rule_risks_saved: int
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    stopped_reason: str | None = None  # budget | recursion_limit | None
    tokens_in: int = 0
    tokens_out: int = 0


def run_agent4(
    *,
    project_id: int,
    plan_version_id: int,
    job_id: int | None = None,
    store: PlanStore | None = None,
    model: Any = None,
    summarizer: Any = None,
    ledger: RunLedger | None = None,
    tracer: Tracer | None = None,
    audit_sink: Callable | None = db_audit_sink,
    rules_only: bool = False,
    require_live: bool = False,
) -> Agent4Result:
    from app.services.plan_store import DbPlanStore

    store = store or DbPlanStore()
    snapshot = store.snapshot(project_id, plan_version_id)  # also proves the version belongs to the project
    findings: list[RuleFinding] = run_rules(snapshot)
    ctx = Agent4Context(project_id=project_id, plan_version_id=plan_version_id, job_id=job_id)
    stopped: str | None = None
    run_id: int | None = None

    if not rules_only:
        from langchain_openai import ChatOpenAI
        from app.llm.langchain_model import GROQ_GPT_OSS_MODELS, get_langchain_model

        model = model or get_langchain_model()
        bundled_reads = (settings.llm_provider.lower() == "groq" and isinstance(model, ChatOpenAI)
                         and model.model_name in GROQ_GPT_OSS_MODELS)
        ledger = ledger or DbRunLedger()
        budget = settings.max_tokens_per_job
        input_hash = hashlib.sha256(f"{plan_version_id}:{snapshot.get('inputs_fingerprint')}".encode()).hexdigest()
        run_id = ledger.start(project_id=project_id, job_id=job_id, model=settings.model_llm,
                              input_hash=input_hash, reserve_tokens=budget)
        ctx.run_id = run_id
        tracer = tracer or get_tracer()
        meta = {"project_id": project_id, "job_id": job_id, "agent": AGENT, "user_role": "system",
                "prompt_version": PROMPT_VERSION, "model": settings.model_llm, "plan_version_id": plan_version_id}
        started = time.monotonic()
        with tracer.generation(name=AGENT, model=settings.model_llm, metadata=meta, session_id=project_id) as gen:
            try:
                agent = build_agent4(model, store, summarizer=summarizer, audit_sink=audit_sink,
                                     budget_tokens=budget, bundled_reads=bundled_reads)
                try:
                    agent.invoke({"messages": [HumanMessage(TASK_MESSAGE)]}, context=ctx,
                                 config={"recursion_limit": settings.agent4_max_graph_steps})
                except AgentBudgetStop as exc:
                    stopped = "budget"
                    log.warning("agent4 stopped: %s", exc)
                except GraphRecursionError:
                    stopped = "recursion_limit"
                if require_live and ctx.pending_review is None:
                    review = store.snapshot(project_id, plan_version_id).get("review") or {}
                    if review.get("source") != "agent4" or review.get("run_id") != run_id:
                        reason = f" ({stopped})" if stopped else ""
                        raise Agent4ReviewIncomplete(f"Agent 4 did not complete its live review{reason}.")
            except Exception as exc:  # noqa: BLE001
                latency = int((time.monotonic() - started) * 1000)
                cost, _priced = compute_cost_usd(settings.model_llm, ctx.tokens_in, ctx.tokens_out)
                gen.finish(usage=TokenUsage(ctx.tokens_in, ctx.tokens_out), cost=cost,
                           error=f"{type(exc).__name__}: {exc}"[:300])
                ledger.fail(run_id, error=f"{type(exc).__name__}: {exc}"[:500], latency_ms=latency,
                            trace_id=gen.trace_id, tokens_in=ctx.tokens_in, tokens_out=ctx.tokens_out, cost=cost)
                raise
            latency = int((time.monotonic() - started) * 1000)
            cost, _priced = compute_cost_usd(settings.model_llm, ctx.tokens_in, ctx.tokens_out)
            gen.finish(usage=TokenUsage(ctx.tokens_in, ctx.tokens_out), cost=cost)
            ledger.complete(run_id, tokens_in=ctx.tokens_in, tokens_out=ctx.tokens_out, cost=cost,
                            latency_ms=latency, trace_id=gen.trace_id,
                            note=f"stopped: {stopped}" if stopped else None)

    # rule risks are always saved (drafts), whatever the agent did
    rule_saved = _save_rule_risks(store, project_id, plan_version_id, findings, run_id)

    after = store.snapshot(project_id, plan_version_id)
    review = {"source": "agent4", **ctx.pending_review.model_dump()} if ctx.pending_review else after.get("review")
    auto_floor = verdict_floor(findings, after)
    # The agent's own risk register is a second floor: recording a critical risk and then calling
    # the plan ready is a contradiction the PM should never have to resolve.
    register_floor = risk_verdict_floor(after.get("risks") or [])
    floor = max((f for f in (auto_floor, register_floor) if f), key=VERDICT_ORDER.get)
    if rules_only or review is None or review.get("source") != "agent4":
        draft = PlanReviewDraft(**rules_review(findings, after))
        store.save_review(project_id, plan_version_id, draft, "rules", run_id)
        source, verdict = "rules", draft.verdict
    else:
        source, verdict = "agent4", review["verdict"]
        if VERDICT_ORDER[floor] > VERDICT_ORDER[verdict]:  # the agent may not be softer than the evidence
            top = rules_review(findings, after)["findings"]
            register_drives = bool(register_floor) and VERDICT_ORDER[register_floor] >= VERDICT_ORDER[auto_floor]
            why = "your own saved risks" if register_drives else "the automatic checks"
            cap = confidence_cap(plan_health(after))
            confidence, cap_notes = cap_confidence(review.get("confidence") or "medium", cap)
            limitations = list(review.get("limitations") or [])
            limitations += [n for n in cap_notes if n not in limitations]
            raised = PlanReviewDraft(
                verdict=floor, summary=(review["summary"] + f" [Verdict raised by {why}.]")[:2000],
                findings=(review.get("findings") or []) + top,
                confidence=confidence,
                recommendation=RECOMMENDATION_FOR_VERDICT[floor],
                open_questions=review.get("open_questions") or [],
                limitations=limitations[:8])
            store.save_review(project_id, plan_version_id, raised, "agent4", run_id)
            verdict = floor
        elif ctx.pending_review is not None:
            store.save_review(project_id, plan_version_id, ctx.pending_review, "agent4", run_id)

    agent_saved = sum(
        int(__import__("json").loads(c.output).get("saved", 0) or 0)
        for c in ctx.calls if c.name == "save_draft_risks" and c.error is None and _is_json(c.output)
    )
    return Agent4Result(plan_version_id=plan_version_id, verdict=verdict, review_source=source, run_id=run_id,
                        agent_risks_saved=agent_saved, rule_risks_saved=rule_saved, tool_calls=ctx.calls,
                        stopped_reason=stopped, tokens_in=ctx.tokens_in, tokens_out=ctx.tokens_out)


def _is_json(text: str) -> bool:
    import json

    try:
        return isinstance(json.loads(text), dict)
    except ValueError:
        return False


def _save_rule_risks(store, project_id, plan_version_id, findings, run_id) -> int:
    saver = getattr(store, "save_rule_risks", None)
    return saver(project_id, plan_version_id, findings, run_id) if saver else 0
