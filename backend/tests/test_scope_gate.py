from types import SimpleNamespace

import pytest

from app.agents import agent1_extract
from app.db.models import Requirement, Source
from app.db.session import SessionLocal
from app.orchestrator.checkpoints import postgres_checkpointer
from app.schemas.extraction import ExtractedRequirement
from app.services.jobs import process_one
from helpers import assign_tl, audit_rows, client, make_project, pm_headers, tl_headers

TRANSCRIPT = "PM: The clinic needs online booking and SMS reminders for patients."


@pytest.fixture(autouse=True)
def _clean_scope_threads():
    def wipe():
        with postgres_checkpointer() as saver:
            delete = getattr(saver, "delete_thread", None)
            if delete:
                for i in range(1, 12):  # project ids restart from 1 after every test
                    delete(f"project:{i}:scope")

    wipe()
    yield
    wipe()


def seed(project_id: int, n: int = 2) -> list[int]:
    with SessionLocal() as s:
        src = Source(project_id=project_id, kind="transcript", title="t", content_text=TRANSCRIPT, phase="initial")
        s.add(src)
        s.flush()
        rows = [
            Requirement(project_id=project_id, type="functional", text=f"Requirement {i}", source_id=src.id,
                        quote="needs online booking", quote_verified=True, confidence=0.9,
                        created_by_agent="agent1_extract", status="draft")
            for i in range(n)
        ]
        s.add_all(rows)
        s.commit()
        return [r.id for r in rows]


def scope(pid, suffix):
    return f"/projects/{pid}/scope/{suffix}"


def decide(pm, pid, rid, decision, comment=""):
    body = {"decision": decision, **({"comment": comment} if comment else {})}
    r = client.post(f"/projects/{pid}/requirements/{rid}/decision", json=body, headers=pm)
    assert r.status_code == 200, r.text


def project_status(pm, pid):
    return client.get(f"/projects/{pid}", headers=pm).json()["status"]


def test_scope_endpoints_are_pm_only():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    seed(pid)
    assign_tl(pid)
    tl = tl_headers()
    assert client.get(scope(pid, "status")).status_code == 401
    assert client.get(scope(pid, "status"), headers=tl).status_code == 403
    assert client.post(scope(pid, "open"), headers=tl).status_code == 403
    assert client.post(scope(pid, "approve"), json={}, headers=tl).status_code == 403


def test_open_needs_requirements_then_waits_and_is_idempotent():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    assert client.post(scope(pid, "open"), headers=pm).status_code == 409  # nothing extracted yet
    seed(pid)
    first = client.post(scope(pid, "open"), headers=pm)
    assert first.status_code == 200, first.text
    assert first.json()["gate"] == "waiting_for_pm" and first.json()["project_status"] == "scope_review"
    second = client.post(scope(pid, "open"), headers=pm).json()
    assert second["run_id"] == first.json()["run_id"]  # same run, not a new gate
    assert len(audit_rows("scope.gate_opened")) == 1


def test_cannot_approve_while_drafts_remain():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    a, _ = seed(pid, 2)
    client.post(scope(pid, "open"), headers=pm)
    decide(pm, pid, a, "approved")  # the other one is still a draft
    r = client.post(scope(pid, "approve"), json={}, headers=pm)
    assert r.status_code == 409 and r.json()["detail"]["drafts"] == 1
    st = client.get(scope(pid, "status"), headers=pm).json()
    assert st["gate"] == "waiting_for_pm" and st["project_status"] == "scope_review"
    assert audit_rows("scope.approved") == []


def test_cannot_approve_when_nothing_is_approved():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    (a,) = seed(pid, 1)
    client.post(scope(pid, "open"), headers=pm)
    decide(pm, pid, a, "rejected", "not needed")
    assert client.post(scope(pid, "approve"), json={}, headers=pm).status_code == 409
    assert project_status(pm, pid) == "scope_review"


def test_approve_without_an_open_gate_is_409():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    (a,) = seed(pid, 1)
    decide(pm, pid, a, "approved")
    assert client.post(scope(pid, "approve"), json={}, headers=pm).status_code == 409
    assert project_status(pm, pid) == "setup"


def test_decided_by_in_body_is_rejected():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    seed(pid)
    client.post(scope(pid, "open"), headers=pm)
    assert client.post(scope(pid, "approve"), json={"decided_by": 1}, headers=pm).status_code == 422


def test_approve_completes_the_graph_and_moves_the_project_to_planning():
    pm = pm_headers()
    me = client.get("/auth/me", headers=pm).json()
    pid = make_project(pm)["id"]
    a, b = seed(pid, 2)
    client.post(scope(pid, "open"), headers=pm)
    decide(pm, pid, a, "approved")
    decide(pm, pid, b, "rejected", "duplicate")
    r = client.post(scope(pid, "approve"), json={"comment": "scope ok"}, headers=pm)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["gate"] == "completed" and data["project_status"] == "planning"
    assert data["result"] == {"outcome": "approved", "written": True}
    rows = audit_rows("scope.approved")
    assert len(rows) == 1 and rows[0].actor == f"user:{me['id']}"
    assert rows[0].before["status"] == "scope_review" and rows[0].after["approved"] == 1
    # a second approve has nothing to resume
    assert client.post(scope(pid, "approve"), json={}, headers=pm).status_code == 409


def test_gate_can_be_reopened_after_approval():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    (a,) = seed(pid, 1)
    first = client.post(scope(pid, "open"), headers=pm).json()
    decide(pm, pid, a, "approved")
    assert client.post(scope(pid, "approve"), json={}, headers=pm).status_code == 200
    again = client.post(scope(pid, "open"), headers=pm)
    assert again.status_code == 200, again.text
    assert again.json()["gate"] == "waiting_for_pm" and again.json()["project_status"] == "scope_review"
    assert again.json()["run_id"] != first["run_id"]


def test_extraction_job_opens_the_gate(monkeypatch):
    def fake(*, project_id, sources, job_id=None, on_batch=None, **kwargs):
        transcript = next(s for s in sources if s["kind"] == "transcript")
        item = ExtractedRequirement(type="functional", text="Online booking", source_id=transcript["id"],
                                    exact_quote="needs online booking", confidence=0.9)
        return agent1_extract.ChunkedExtraction([item], [SimpleNamespace(run_id=None)], 1)

    monkeypatch.setattr(agent1_extract, "extract_requirements_chunked", fake)
    pm = pm_headers()
    pid = make_project(pm, transcript=TRANSCRIPT)["id"]
    assert client.post(f"/projects/{pid}/jobs", json={"type": "agent1.extract"}, headers=pm).status_code == 202
    assert process_one() is True
    st = client.get(scope(pid, "status"), headers=pm).json()
    assert st["gate"] == "waiting_for_pm" and st["project_status"] == "scope_review"
    assert st["counts"]["draft"] == 1