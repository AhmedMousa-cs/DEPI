import json
import random
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import BaseModel
from sqlalchemy import select

from app.config import settings
from app.db.models import AgentRun, AuditLog, Job
from app.db.session import SessionLocal
from app.llm.pricing import compute_cost_usd, load_pricing
from app.llm.runner import run_structured
from app.observability.langfuse import Tracer, safe_metadata
from app.orchestrator.audit_hook import db_graph_audit
from app.orchestrator.checkpoints import memory_checkpointer
from app.orchestrator.graph import GraphRunner
from app.services import agent_runs
from app.services.agent_runs import BudgetExceeded
from helpers import make_project, pm_headers

MODEL = "test/model"


class Out(BaseModel):
    ok: bool


class FakeObs:
    def __init__(self, store):
        self.store = store

    def update(self, **kw):
        self.store["updates"].append(kw)

    def update_trace(self, **kw):
        self.store["trace"].append(kw)


class FakeCM:
    def __init__(self, store):
        self.store = store

    def __enter__(self):
        return FakeObs(self.store)

    def __exit__(self, *a):
        return False


class FakeLangfuse:
    def __init__(self):
        self.store = {"gen": [], "updates": [], "trace": []}

    def start_as_current_generation(self, **kw):
        self.store["gen"].append(kw)
        return FakeCM(self.store)

    def get_current_trace_id(self):
        return "trace-123"


class BrokenLangfuse:
    def start_as_current_generation(self, **kw):
        raise ConnectionError("langfuse is down")


class BrokenUpdateLangfuse(FakeLangfuse):
    def start_as_current_generation(self, **kw):
        class CM(FakeCM):
            def __enter__(s):
                class Obs:
                    def update(self, **k):
                        raise RuntimeError("boom")

                    def update_trace(self, **k):
                        raise RuntimeError("boom")

                return Obs()

        return CM(self.store)


@pytest.fixture(autouse=True)
def cfg(monkeypatch):
    monkeypatch.setattr(settings, "model_llm", MODEL)
    monkeypatch.setattr(settings, "llm_pricing_json",
                        json.dumps({MODEL: {"input_per_1m": 1.0, "output_per_1m": 2.0}}))
    monkeypatch.setattr(settings, "llm_max_output_tokens", 100)
    monkeypatch.setattr(settings, "llm_max_attempts", 1)
    monkeypatch.setattr(settings, "daily_budget_usd", 5.0)
    monkeypatch.setattr(settings, "max_tokens_per_job", 12000)


def ok_call(**kw):
    return SimpleNamespace(parsed=Out(ok=True), attempts=1,
                           usage=SimpleNamespace(input_tokens=100, output_tokens=50))


def boom_call(**kw):
    raise RuntimeError("provider down")


def pid():
    return make_project(pm_headers())["id"]


def go(project_id, job_id=None, call=ok_call, tracer=None, **kw):
    return run_structured(project_id=project_id, job_id=job_id, agent="agent1", prompt_version="v0",
                          messages=[{"role": "user", "content": "hi"}], schema=Out, user_role="PM",
                          model_factory=lambda: object(), call=call,
                          tracer=tracer or Tracer(client=FakeLangfuse()), **kw)


def runs():
    with SessionLocal() as s:
        return list(s.scalars(select(AgentRun).order_by(AgentRun.id)))


def audit_actions():
    with SessionLocal() as s:
        return [r.action for r in s.scalars(select(AuditLog).order_by(AuditLog.id))]


def add_job(project_id):
    with SessionLocal() as s:
        j = Job(project_id=project_id, type="demo.echo", params={})
        s.add(j)
        s.commit()
        return j.id


# ---------- cost ----------
def test_cost_calculation():
    assert compute_cost_usd(MODEL, 100, 50) == (Decimal("0.0002"), True)
    assert compute_cost_usd("unknown/model", 100, 50) == (Decimal(0), False)
    with pytest.raises(ValueError):
        compute_cost_usd(MODEL, -1, 0)


def test_decimal_arithmetic_has_no_float_drift():
    p = load_pricing(json.dumps({"m": {"input_per_1m": 0.1, "output_per_1m": 0.2}}))
    assert compute_cost_usd("m", 1_000_000, 1_000_000, p)[0] == Decimal("0.3")


# ---------- persistence ----------
def test_successful_run_persists_usage_cost_and_audit():
    p = pid()
    res = go(p)
    assert res.parsed.ok is True and res.usage.total_tokens == 150
    [row] = runs()
    assert row.status == "completed" and row.project_id == p
    assert (row.input_tokens, row.output_tokens, row.total_tokens) == (100, 50, 150)
    assert row.cost_usd == Decimal("0.0002") and row.trace_id == "trace-123"
    assert row.latency_ms is not None and row.finished_at is not None
    assert row.reserved_tokens == 0 and row.reserved_cost_usd == 0
    acts = audit_actions()
    assert "agent.run.started" in acts and "agent.run.completed" in acts


def test_failed_run_is_persisted_and_audited():
    p = pid()
    with pytest.raises(RuntimeError):
        go(p, call=boom_call)
    [row] = runs()
    assert row.status == "failed" and row.error_code == "llm_error" and "provider down" in row.error
    assert "agent.run.failed" in audit_actions()


# ---------- budgets ----------
def test_per_job_token_budget(monkeypatch):
    p = pid()
    jid = add_job(p)
    monkeypatch.setattr(settings, "max_tokens_per_job", 100)  # reserve = 1*(est+100) > 100
    with pytest.raises(BudgetExceeded) as e:
        go(p, job_id=jid)
    assert e.value.kind == "job_token_budget"
    [row] = runs()
    assert row.status == "failed" and row.error_code == "budget_exceeded"
    assert "budget.exceeded" in audit_actions()


def test_per_job_budget_counts_previous_runs(monkeypatch):
    p = pid()
    jid = add_job(p)
    with SessionLocal() as s:
        s.add(AgentRun(project_id=p, job_id=jid, agent="agent1", model=MODEL, prompt_version="v0",
                       status="completed", total_tokens=11900))
        s.commit()
    with pytest.raises(BudgetExceeded):
        go(p, job_id=jid)  # 11900 + reserve(~101) > 12000


def test_daily_budget_rejects_before_calling_the_model(monkeypatch):
    p = pid()
    monkeypatch.setattr(settings, "daily_budget_usd", 0.0001)  # reserve is ~0.0002
    called = []
    with pytest.raises(BudgetExceeded) as e:
        go(p, call=lambda **kw: called.append(1) or ok_call())
    assert e.value.kind == "daily_budget" and called == []


def test_daily_budget_counts_todays_spend(monkeypatch):
    p = pid()
    with SessionLocal() as s:
        s.add(AgentRun(project_id=p, agent="agent1", model=MODEL, prompt_version="v0",
                       status="completed", cost_usd=Decimal("4.9999")))
        s.commit()
    monkeypatch.setattr(settings, "daily_budget_usd", 5.0)
    with pytest.raises(BudgetExceeded):
        go(p)


def test_missing_pricing_is_flagged_not_invented(monkeypatch):
    p = pid()
    monkeypatch.setattr(settings, "llm_pricing_json", "{}")
    res = go(p)
    assert res.cost_usd == 0 and res.pricing_configured is False
    assert runs()[0].error == "pricing not configured"


# ---------- transaction safety ----------
def test_audit_failure_rolls_back_the_run_update(monkeypatch):
    p = pid()
    run_id = agent_runs.reserve_run(project_id=p, job_id=None, agent="agent1", model=MODEL, prompt_version="v0",
                                    input_hash=None, reserve_tokens=10, reserve_cost=Decimal("0.1"))

    def broken_audit(*a, **k):
        raise RuntimeError("audit down")

    monkeypatch.setattr(agent_runs, "write_audit", broken_audit)
    with pytest.raises(RuntimeError):
        agent_runs.complete_run(run_id, input_tokens=1, output_tokens=1, cost=Decimal("0.5"),
                                latency_ms=5, trace_id=None, attempts=1)
    with SessionLocal() as s:
        row = s.get(AgentRun, run_id)
    assert row.status == "running" and row.cost_usd == 0


# ---------- Langfuse ----------
def test_langfuse_metadata_is_complete():
    p = pid()
    fake = FakeLangfuse()
    go(p, job_id=None, tracer=Tracer(client=fake))
    meta = fake.store["gen"][0]["metadata"]
    for key in ("project_id", "job_id", "agent", "user_role", "prompt_version", "model"):
        assert key in meta
    assert meta["agent"] == "agent1" and meta["model"] == MODEL and meta["user_role"] == "PM"
    assert fake.store["updates"][-1]["usage_details"] == {"input": 100, "output": 50}


def test_no_secrets_in_trace_metadata(monkeypatch):
    secret = "gsk_test_secret_value_123456"
    monkeypatch.setattr(settings, "groq_api_key", secret)
    monkeypatch.setattr(settings, "langfuse_secret_key", "lf_secret_value_987654")
    meta = safe_metadata({"agent": f"x-{secret}", "groq_api_key": secret, "password": "p", "model": MODEL,
                          "langfuse_secret_key": "lf_secret_value_987654"})
    dumped = json.dumps(meta)
    assert secret not in dumped and "lf_secret_value_987654" not in dumped
    assert "groq_api_key" not in meta and "password" not in meta and "[REDACTED]" in dumped


@pytest.mark.parametrize("client", [BrokenLangfuse(), BrokenUpdateLangfuse()])
def test_langfuse_outage_does_not_fail_the_business_operation(client):
    p = pid()
    res = go(p, tracer=Tracer(client=client))
    assert res.parsed.ok is True
    assert runs()[0].status == "completed"


# ---------- graph audit events ----------
def test_graph_audit_events_are_written():
    class W:
        def write(self, **kw):
            return True

    r = GraphRunner(memory_checkpointer(), W(), audit=db_graph_audit)
    pidx = random.randint(10**6, 10**7)
    r.start(pidx, "x")
    r.resume(pidx, {"decision": "approved", "comment": "", "decided_by": 7})
    r.start(pidx, "y")
    r.resume(pidx, {"decision": "rejected", "comment": "no", "decided_by": 7})
    acts = audit_actions()
    for a in ("graph.interrupted", "graph.resumed", "pm.approval", "pm.rejection"):
        assert a in acts
