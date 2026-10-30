"""agent_runs persistence + budget enforcement. Row changes and audit rows share one transaction."""
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import case, func, select, text

from app.audit import write_audit
from app.config import settings
from app.db.models import AgentRun
from app.db.session import SessionLocal
from app.services.jobs import utcnow

try:  # a budget failure must not be retried by the job queue
    from app.services.jobs import PermanentJobError as _Base
except ImportError:  # pragma: no cover
    _Base = Exception

BUDGET_LOCK_KEY = 7_340_001


class BudgetExceeded(_Base):
    code = "budget_exceeded"

    def __init__(self, kind: str, message: str, run_id: int):
        super().__init__(message)
        self.kind, self.run_id = kind, run_id


def _actor(agent: str) -> str:
    return f"agent:{agent}"


def reserve_run(*, project_id: int, job_id: int | None, agent: str, model: str, prompt_version: str,
                input_hash: str | None, reserve_tokens: int, reserve_cost: Decimal) -> int:
    now = utcnow()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    fresh = now - timedelta(seconds=settings.job_timeout_seconds)
    rejection: tuple[str, str] | None = None
    with SessionLocal() as db:
        db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": BUDGET_LOCK_KEY})
        if job_id is not None:
            used = db.scalar(
                select(func.coalesce(func.sum(case((AgentRun.status == "running", AgentRun.reserved_tokens),
                                                   else_=AgentRun.total_tokens)), 0))
                .where(AgentRun.job_id == job_id)
            )
            if used + reserve_tokens > settings.max_tokens_per_job:
                rejection = ("job_token_budget",
                             f"job token budget exceeded ({used}+{reserve_tokens} > {settings.max_tokens_per_job})")
        if rejection is None:
            spent = db.scalar(select(func.coalesce(func.sum(AgentRun.cost_usd), 0))
                              .where(AgentRun.created_at >= day_start, AgentRun.status.in_(("completed", "failed"))))
            inflight = db.scalar(select(func.coalesce(func.sum(AgentRun.reserved_cost_usd), 0))
                                 .where(AgentRun.status == "running", AgentRun.created_at >= fresh))
            budget = Decimal(str(settings.daily_budget_usd))
            if Decimal(spent) + Decimal(inflight) + reserve_cost > budget:
                rejection = ("daily_budget", f"daily budget exceeded (limit {budget} USD)")
        run = AgentRun(project_id=project_id, job_id=job_id, agent=agent, model=model, prompt_version=prompt_version,
                       input_hash=input_hash, status="running", reserved_tokens=reserve_tokens, reserved_cost_usd=reserve_cost)
        if rejection:
            run.status, run.error_code, run.error = "failed", "budget_exceeded", rejection[1]
            run.reserved_tokens, run.reserved_cost_usd, run.finished_at = 0, Decimal(0), now
        db.add(run)
        db.flush()
        if rejection:
            write_audit(db, _actor(agent), "budget.exceeded", "agent_run", run.id,
                        after={"project_id": project_id, "job_id": job_id, "kind": rejection[0], "message": rejection[1]})
        else:
            write_audit(db, _actor(agent), "agent.run.started", "agent_run", run.id,
                        after={"project_id": project_id, "job_id": job_id, "model": model, "prompt_version": prompt_version})
        db.commit()
        run_id = run.id
    if rejection:
        raise BudgetExceeded(rejection[0], rejection[1], run_id)
    return run_id


def complete_run(run_id: int, *, input_tokens: int, output_tokens: int, cost: Decimal, latency_ms: int,
                 trace_id: str | None, attempts: int, note: str | None = None) -> bool:
    with SessionLocal() as db:
        run = db.get(AgentRun, run_id, with_for_update=True)
        if run is None or run.status != "running":
            return False
        run.status, run.attempts = "completed", attempts
        run.input_tokens, run.output_tokens = input_tokens, output_tokens
        run.total_tokens = input_tokens + output_tokens
        run.cost_usd, run.latency_ms, run.trace_id, run.error = cost, latency_ms, trace_id, note
        run.reserved_tokens, run.reserved_cost_usd, run.finished_at = 0, Decimal(0), utcnow()
        write_audit(db, _actor(run.agent), "agent.run.completed", "agent_run", run.id,
                    after={"project_id": run.project_id, "total_tokens": run.total_tokens,
                           "cost_usd": str(cost), "latency_ms": latency_ms, "trace_id": trace_id})
        db.commit()
    return True


def fail_run(run_id: int, *, error_code: str, error: str, latency_ms: int | None, trace_id: str | None) -> bool:
    with SessionLocal() as db:
        run = db.get(AgentRun, run_id, with_for_update=True)
        if run is None or run.status != "running":
            return False
        run.status, run.error_code, run.error = "failed", error_code, error[:500]
        run.latency_ms, run.trace_id = latency_ms, trace_id
        run.reserved_tokens, run.reserved_cost_usd, run.finished_at = 0, Decimal(0), utcnow()
        write_audit(db, _actor(run.agent), "agent.run.failed", "agent_run", run.id,
                    after={"project_id": run.project_id, "error_code": error_code, "error": error[:200]})
        db.commit()
    return True
