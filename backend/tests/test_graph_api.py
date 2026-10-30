import pytest

from app.orchestrator.checkpoints import postgres_checkpointer
from helpers import assign_tl, audit_rows, client, make_project, pm_headers, tl_headers


@pytest.fixture(autouse=True)
def _clean_threads():
    yield
    with postgres_checkpointer() as saver:
        delete = getattr(saver, "delete_thread", None)
        if delete:
            for i in range(1, 8):  # project ids restart from 1 after every test
                delete(f"project:{i}")


def start(h, pid, text="Build the booking page"):
    return client.post(f"/projects/{pid}/graph/start", json={"input_text": text}, headers=h)


def resume(h, pid, **body):
    return client.post(f"/projects/{pid}/graph/resume", json=body, headers=h)


def test_start_requires_pm():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    assign_tl(pid)
    assert client.post(f"/projects/{pid}/graph/start", json={"input_text": "x"}).status_code == 401
    assert start(tl_headers(), pid).status_code == 403


def test_start_reaches_gate_without_business_write():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    r = start(pm, pid)
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "waiting_for_pm"
    assert r.json()["interrupt"]["kind"] == "pm_approval"
    assert audit_rows("graph.finalized") == []
    assert client.get(f"/projects/{pid}/graph/status", headers=pm).json()["state"] == "waiting_for_pm"


def test_approve_completes_and_uses_the_jwt_user_as_decider():
    pm = pm_headers()
    me = client.get("/auth/me", headers=pm).json()
    pid = make_project(pm)["id"]
    start(pm, pid)
    r = resume(pm, pid, decision="approved", comment="ok")
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "completed"
    assert r.json()["values"]["decided_by"] == me["id"]
    assert len(audit_rows("graph.finalized")) == 1


def test_decided_by_in_body_is_rejected():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    start(pm, pid)
    assert resume(pm, pid, decision="approved", decided_by=999).status_code == 422


def test_reject_needs_comment_and_graph_keeps_waiting():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    start(pm, pid)
    assert resume(pm, pid, decision="rejected").status_code == 422
    assert client.get(f"/projects/{pid}/graph/status", headers=pm).json()["state"] == "waiting_for_pm"
    assert audit_rows("graph.finalized") == []


def test_resume_when_not_waiting_is_409():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    assert resume(pm, pid, decision="approved").status_code == 409


def test_second_start_while_waiting_is_409():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    assert start(pm, pid).status_code == 200
    assert start(pm, pid).status_code == 409


def test_team_leader_can_read_status_but_not_decide():
    pm, tl = pm_headers(), tl_headers()
    pid = make_project(pm)["id"]
    start(pm, pid)
    assert client.get(f"/projects/{pid}/graph/status", headers=tl).status_code == 403
    assign_tl(pid)
    assert client.get(f"/projects/{pid}/graph/status", headers=tl).status_code == 200
    assert resume(tl, pid, decision="approved").status_code == 403


def test_unknown_project_404():
    assert start(pm_headers(), 99999).status_code == 404
