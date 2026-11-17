"""M4.1 (part 2): the LLM-judged coverage-gap report — sanitising, persistence, endpoints."""
import pytest
from sqlalchemy import select

from app.agents.agent3_schedule import ScheduleExplanation  # noqa: F401 (import graph sanity)
from app.db.models import CoverageReport, Requirement
from app.db.session import SessionLocal
from app.schemas.extraction import GapAnalysisOutput
from app.services.coverage_gaps import run_llm_gap_analysis
from helpers import audit_rows, client, make_project, pm_headers, tl_headers

from test_agent1_smart_review import seed_gap_requirements

FAKE_RUN = {
    "usage": None, "cost_usd": 0, "pricing_configured": False,
    "attempts": 1, "trace_id": None,
}


def seed_run(project_id: int) -> int:
    """A real agent_runs row: run_structured always creates one, the fake must too."""
    from app.db.models import AgentRun

    with SessionLocal() as s:
        row = AgentRun(project_id=project_id, agent="agent1_gaps",
                       model="fake", prompt_version="agent1_gaps_v1", status="done")
        s.add(row)
        s.commit()
        return row.id


def fake_llm(assessments, run_id: int):
    def fake(**kwargs):
        parsed = GapAnalysisOutput(assessments=assessments)
        from app.llm.runner import StructuredRun
        return StructuredRun(parsed=parsed, run_id=run_id, **FAKE_RUN)
    return fake


def patch_llm(monkeypatch, assessments, run_id: int):
    monkeypatch.setattr("app.services.coverage_gaps.run_structured", fake_llm(assessments, run_id))


def good_assessments():
    return [
        {"area": "Authentication & accounts", "covered": True, "matched_ids": [1], "note": "Registration requirement #1."},
        {"area": "Payments & billing", "covered": True, "matched_ids": [2, 3], "note": "Card + cash requirements."},
        {"area": "Data & reporting", "covered": False, "matched_ids": [], "note": "Ask the client: what reports does the manager need?"},
        {"area": "Notifications & communication", "covered": False, "matched_ids": [], "note": "Ask about reminders."},
    ]


def test_llm_analysis_is_sanitised_and_persisted(monkeypatch):
    pm = pm_headers()
    pid = make_project(pm)["id"]
    seed_gap_requirements(pid)
    run_id = seed_run(pid)

    # the LLM hallucinates: an invented area + ids that do not exist + a foreign id
    patched = good_assessments() + [
        {"area": "Blockchain synergy", "covered": True, "matched_ids": [1], "note": "invented"},
        {"area": "Payments & billing", "covered": True, "matched_ids": [2, 3, 424242], "note": "x"},
    ]
    patch_llm(monkeypatch, patched, run_id)

    with SessionLocal() as db:
        payload = run_llm_gap_analysis(db, pid)
        db.commit()

    areas = {a["area"]: a for a in payload["areas"]}
    assert "Blockchain synergy" not in areas  # invented area dropped
    assert areas["Payments & billing"]["matched_ids"] == [2, 3]  # hallucinated id filtered
    assert areas["Payments & billing"]["source"] == "llm"
    assert areas["Data & reporting"]["covered"] is False and areas["Data & reporting"]["note"]
    assert payload["llm"]["run_id"] == run_id and payload["llm"]["analyzed_requirements"] == 3

    with SessionLocal() as s:
        rows = list(s.scalars(select(CoverageReport).where(CoverageReport.project_id == pid)))
        assert len(rows) == 1 and rows[0].run_id == run_id
    assert audit_rows("agent1.gaps_analyzed")[0].after["run_id"] == run_id


def test_get_returns_the_saved_llm_report_and_analyze_endpoint_works(monkeypatch):
    pm = pm_headers()
    pid = make_project(pm)["id"]
    seed_gap_requirements(pid)
    patch_llm(monkeypatch, good_assessments(), seed_run(pid))

    # before analysis: keywords only, no llm block
    before = client.get(f"/projects/{pid}/requirements/gaps", headers=pm).json()
    assert before["llm"] is None and before["empty"] is False

    # analyze (the real LLM call is patched away)
    r = client.post(f"/projects/{pid}/requirements/gaps/analyze", json={}, headers=pm)
    assert r.status_code == 200, r.text
    body = r.json()
    stored_run = body["llm"]["run_id"]
    assert isinstance(stored_run, int) and stored_run > 0

    # GET now returns the stored report: a page load never re-runs the LLM
    after = client.get(f"/projects/{pid}/requirements/gaps", headers=pm).json()
    assert after["llm"]["run_id"] == stored_run and after["llm"]["prompt_version"] == "agent1_gaps_v1"
    areas = {a["area"]: a for a in after["areas"]}
    assert areas["Authentication & accounts"]["source"] == "llm"


def test_analyze_needs_requirements_and_the_pm_role():
    pm = pm_headers()
    pid = make_project(pm)["id"]  # no extraction ran

    r = client.post(f"/projects/{pid}/requirements/gaps/analyze", json={}, headers=pm)
    assert r.status_code == 409 and "extraction" in r.json()["detail"]

    pid2 = make_project(pm)["id"]
    seed_gap_requirements(pid2)
    assert client.post(f"/projects/{pid2}/requirements/gaps/analyze", json={}, headers=tl_headers()).status_code == 403
