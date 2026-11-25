"""Plan approval gate + API, end to end on the real database.

The rule under test: a plan becomes 'approved' ONLY after a real approval by an authenticated PM,
through the gate, with every precondition re-checked inside the write.
"""
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select, text

from app.agents.agent4_testing import ScriptedChatModel, happy_path_script
from app.db.models import Decision, PlanVersion, Project, Risk, Task
from app.db.session import SessionLocal
from app.orchestrator.checkpoints import postgres_checkpointer
from app.orchestrator.plan_gate import (
    InvalidPlanDecision, PlanDecision, PlanGate, PlanGateNotAllowed, PlanGateNotWaiting, make_plan_thread_id,
    parse_plan_decision,
)
from app.services import plan_approval, plan_versions as pvs
from app.services.jobs import process_one
from helpers import audit_rows, client, make_project, pm_headers, tl_headers, assign_tl
from plan_helpers import seed_plan_data


@pytest.fixture(autouse=True)
def no_llm(monkeypatch):
    monkeypatch.setattr("app.services.agent3_job.llm_available", lambda: False)


def base(pid):
    return f"/projects/{pid}/plan"


def new_project(status="planning", **kw):
    pm = pm_headers()
    pid = make_project(pm)["id"]
    data = seed_plan_data(pid, **kw)
    with SessionLocal() as s:
        s.get(Project, pid).status = status
        s.commit()
    return pm, pid, data


def generate(pm, pid, overrides=None, expect=202):
    r = client.post(f"{base(pid)}/generate", json={"overrides": overrides or []}, headers=pm)
    assert r.status_code == expect, r.text
    return r


def reviewed(monkeypatch, pm, pid, data, overrides=None, verdict="ready"):
    """agent3.schedule job -> draft; agent4.review job (scripted model) -> review + gate opened."""
    generate(pm, pid, overrides)
    assert process_one()
    t = data["task_ids"]
    risk = {"title": f"Long pole {len(overrides or [])}", "description": "API gates the tests.", "severity": "medium",
            "likelihood": "medium", "mitigation": "Start API first.",
            "evidence_refs": [{"type": "task", "id": str(t[1])}]}
    model = ScriptedChatModel(script=happy_path_script(risks=[risk], verdict=verdict, summary="Reviewed."))
    monkeypatch.setattr("app.llm.langchain_model.get_langchain_model", lambda *a, **k: model)
    assert process_one()
    with SessionLocal() as s:
        return pvs.latest_version(s, pid).version


def approve(pm, pid, version, **body):
    return client.post(f"{base(pid)}/versions/{version}/approve", json=body, headers=pm)


def version_row(pid, version):
    with SessionLocal() as s:
        pv = pvs.get_version(s, pid, version)
        s.expunge(pv)
        return pv


def tasks(pid):
    with SessionLocal() as s:
        return list(s.scalars(select(Task).where(Task.project_id == pid).order_by(Task.id)))


def gate_state(pid, version):
    with postgres_checkpointer() as saver:
        return PlanGate(saver).status(pid, version)["state"]


# ---------------------------------------------------------------- the happy path
def test_full_flow_draft_review_gate_then_pm_approval(monkeypatch):
    pm, pid, data = new_project()
    v = reviewed(monkeypatch, pm, pid, data)
    # after the agents: still a DRAFT, the gate is open, nothing was applied
    pv = version_row(pid, v)
    assert (pv.status, pv.approved_by, pv.approved_at) == ("draft", None, None)
    assert gate_state(pid, v) == "waiting_for_pm"
    with SessionLocal() as s:
        assert s.get(Project, pid).status == "plan_review"
        assert all(t.start is None and t.is_critical is False for t in tasks_in(s, pid))
        assert {r.status for r in pvs.risks_of(s, pv.id)} == {"draft"}

    detail = client.get(f"{base(pid)}/versions/{v}", headers=pm).json()
    assert detail["status"] == "draft" and detail["snapshot"]["review"]["verdict"] == "ready"
    assert detail["warnings"] == [] and detail["warnings_token"] is None and len(detail["risks"]) >= 1

    r = approve(pm, pid, v, comment="Looks good")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved" and r.json()["tasks_applied"] == 4

    pv = version_row(pid, v)
    assert pv.status == "approved" and pv.approved_by is not None and pv.approved_at is not None
    with SessionLocal() as s:
        assert s.get(Project, pid).status == "active"
        rows = tasks_in(s, pid)
        assert all(t.start is not None and t.end is not None for t in rows)
        assert [t.is_critical for t in rows] == [t["is_critical"] for t in pv.snapshot["tasks"]]
        assert {r.status for r in pvs.risks_of(s, pv.id)} == {"open"}
        d = s.scalar(select(Decision).where(Decision.subject_type == "plan_version"))
        assert (d.decision, d.subject_id, d.reason, d.decided_by) == ("approved", pv.id, "Looks good", pv.approved_by)
    assert gate_state(pid, v) == "completed"
    audit = audit_rows("plan.approved")[0]
    assert audit.actor.startswith("user:") and audit.after["version"] == v and audit.before["status"] == "draft"


def tasks_in(s, pid):
    return list(s.scalars(select(Task).where(Task.project_id == pid).order_by(Task.id)))


def test_nothing_is_approved_without_the_pm_acting(monkeypatch):
    pm, pid, data = new_project()
    v = reviewed(monkeypatch, pm, pid, data)
    for _ in range(3):
        process_one()  # no further job can approve anything
    assert version_row(pid, v).status == "draft"
    with SessionLocal() as s:
        assert s.scalar(select(PlanVersion.id).where(PlanVersion.status == "approved")) is None
    assert not audit_rows("plan.approved")


# ------------------------------------------------------------------ authorization
def test_only_the_pm_can_use_the_plan_endpoints(monkeypatch):
    pm, pid, data = new_project()
    v = reviewed(monkeypatch, pm, pid, data)
    assign_tl(pid)
    tl = tl_headers()
    calls = [("get", "/versions"), ("get", f"/versions/{v}"), ("get", "/diff?from_version=1&to_version=1"),
             ("get", "/risks"), ("get", f"/versions/{v}/health"), ("get", f"/versions/{v}/gate"),
             ("post", "/generate"), ("post", "/review"),
             ("post", f"/versions/{v}/open-gate"), ("post", f"/versions/{v}/approve"), ("post", f"/versions/{v}/reject")]
    for method, path in calls:
        kw = {"json": {"comment": "x"}} if method == "post" else {}
        assert getattr(client, method)(base(pid) + path, headers=tl, **kw).status_code == 403, path
        assert getattr(client, method)(base(pid) + path, **kw).status_code == 401, path
    assert version_row(pid, v).status == "draft"


def test_decided_by_can_not_be_supplied_in_the_body(monkeypatch):
    pm, pid, data = new_project()
    v = reviewed(monkeypatch, pm, pid, data)
    assert approve(pm, pid, v, decided_by=999).status_code == 422
    assert client.post(f"{base(pid)}/versions/{v}/reject", json={"comment": "no", "decided_by": 999}, headers=pm).status_code == 422
    assert version_row(pid, v).status == "draft"


def test_health_endpoint_returns_the_facts_behind_the_review(monkeypatch):
    pm, pid, data = new_project()
    v = reviewed(monkeypatch, pm, pid, data)
    r = client.get(f"{base(pid)}/versions/{v}/health", headers=pm)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["version"] == v
    assert body["health"]["schedule"]["tasks"] == len(data["task_ids"])
    # all tasks are assigned and the project has a target end date -> the evidence supports "high"
    assert body["confidence_cap"] == {"level": "high", "reasons": []}
    assert client.get(f"{base(pid)}/versions/99/health", headers=pm).status_code == 404


def test_unknown_project_or_version_is_404():
    pm = pm_headers()
    assert client.get(f"{base(9999)}/versions", headers=pm).status_code == 404
    _, pid, _ = new_project()
    assert client.get(f"{base(pid)}/versions/7", headers=pm).status_code == 404
    assert approve(pm, pid, 7).status_code == 404


# ---------------------------------------------------------------- preconditions
def test_an_unreviewed_draft_can_not_be_approved_or_opened(monkeypatch):
    pm, pid, data = new_project()
    generate(pm, pid)
    process_one()  # agent3 only; the queued agent4 job is not run
    with SessionLocal() as s:
        s.execute(text("UPDATE jobs SET status='cancelled' WHERE type='agent4.review'"))
        s.commit()
    r = client.post(f"{base(pid)}/versions/1/open-gate", headers=pm)
    assert r.status_code == 409 and "not reviewed" in r.json()["detail"]
    r = approve(pm, pid, 1)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "gate_not_open"
    assert version_row(pid, 1).status == "draft"


def test_approving_twice_is_refused(monkeypatch):
    pm, pid, data = new_project()
    v = reviewed(monkeypatch, pm, pid, data)
    assert approve(pm, pid, v).status_code == 200
    r = approve(pm, pid, v)
    assert r.status_code == 409 and r.json()["detail"]["code"] in ("gate_not_open", "not_a_draft")
    assert len(audit_rows("plan.approved")) == 1


def test_locked_plan_input_returns_a_retryable_conflict_and_keeps_the_gate_open(monkeypatch):
    pm, pid, data = new_project()
    version = reviewed(monkeypatch, pm, pid, data)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with SessionLocal() as writer:
            assert writer.scalar(select(Task.id).where(Task.id == data["task_ids"][0]).with_for_update())
            refused = pool.submit(approve, pm, pid, version).result(timeout=15)
            assert refused.status_code == 409, refused.text
            assert refused.json()["detail"]["code"] == "inputs_busy"

    assert gate_state(pid, version) == "waiting_for_pm"
    assert version_row(pid, version).status == "draft"
    assert all(t.start is None and t.end is None for t in tasks(pid))
    assert not audit_rows("plan.approved")
    approved = approve(pm, pid, version)
    assert approved.status_code == 200, approved.text
    assert len(audit_rows("plan.approved")) == 1


def test_warnings_must_be_acknowledged_and_a_refusal_does_not_close_the_gate(monkeypatch):
    pm, pid, data = new_project()
    t_ui, ahmed = data["task_ids"][2], data["member_ids"][0]
    v = reviewed(monkeypatch, pm, pid, data, overrides=[{"task_id": t_ui, "assignee_member_id": ahmed}])
    detail = client.get(f"{base(pid)}/versions/{v}", headers=pm).json()
    assert any("resource conflict" in w for w in detail["warnings"])
    assert detail["warnings_token"]
    for body, expected_code in (({}, "warnings_not_acknowledged"), ({"acknowledge_warnings": True}, "warnings_changed")):
        r = approve(pm, pid, v, **body)
        assert r.status_code == 409, r.text
        failure = r.json()["detail"]
        assert failure["code"] == expected_code
        assert (failure["warnings"], failure["warnings_token"]) == (detail["warnings"], detail["warnings_token"])
    assert gate_state(pid, v) == "waiting_for_pm" and version_row(pid, v).status == "draft"
    r = approve(pm, pid, v, acknowledge_warnings=True, warnings_token=detail["warnings_token"])
    assert r.status_code == 200, r.text
    assert audit_rows("plan.approved")[0].after["acknowledged_warnings"] == detail["warnings"]


def test_review_changes_require_a_fresh_warning_acknowledgement(monkeypatch):
    pm, pid, data = new_project()
    t_ui, ahmed = data["task_ids"][2], data["member_ids"][0]
    version = reviewed(monkeypatch, pm, pid, data, overrides=[{"task_id": t_ui, "assignee_member_id": ahmed}])
    url = f"{base(pid)}/versions/{version}"
    before = client.get(url, headers=pm).json()
    assert before["warnings"] and before["warnings_token"]

    model = ScriptedChatModel(script=happy_path_script(risks=[], verdict="ready", summary="Revised evidence summary."))
    monkeypatch.setattr("app.llm.langchain_model.get_langchain_model", lambda *a, **k: model)
    r = client.post(f"{base(pid)}/review", headers=pm)
    assert r.status_code == 202, r.text
    assert process_one()
    after = client.get(url, headers=pm).json()
    assert "Revised evidence summary." in after["snapshot"]["review"]["summary"]
    assert after["warnings"] == before["warnings"]
    assert after["warnings_token"] and after["warnings_token"] != before["warnings_token"]

    r = approve(pm, pid, version, acknowledge_warnings=True, warnings_token=before["warnings_token"])
    assert r.status_code == 409, r.text
    failure = r.json()["detail"]
    assert failure["code"] == "warnings_changed"
    assert (failure["warnings"], failure["warnings_token"]) == (after["warnings"], after["warnings_token"])
    assert gate_state(pid, version) == "waiting_for_pm" and version_row(pid, version).status == "draft"
    assert all(t.start is None and t.end is None for t in tasks(pid))

    with SessionLocal() as s:
        refused = plan_approval.approve_plan(
            s, project_id=pid, version=version, decided_by=1, comment="stale review", acknowledge_warnings=True,
            warnings_token=before["warnings_token"],
        )
        s.commit()
    assert refused["written"] is False and refused["code"] == "warnings_changed"

    r = approve(pm, pid, version, acknowledge_warnings=True, warnings_token=after["warnings_token"])
    assert r.status_code == 200, r.text
    assert len(audit_rows("plan.approved")) == 1


def test_changed_inputs_block_approval_of_a_stale_draft(monkeypatch):
    pm, pid, data = new_project()
    v = reviewed(monkeypatch, pm, pid, data)
    with SessionLocal() as s:
        s.get(Task, data["task_ids"][0]).estimate_hours = 80
        s.commit()
    r = approve(pm, pid, v)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "inputs_changed"
    assert version_row(pid, v).status == "draft" and all(t.start is None for t in tasks(pid))
    assert gate_state(pid, v) == "waiting_for_pm"


@pytest.mark.parametrize(("field", "value"), [("start_date", "2026-11-02"), ("end_date", "2026-12-05")])
def test_project_date_changes_require_rebuilding_the_draft(monkeypatch, field, value):
    pm, pid, data = new_project()
    version = reviewed(monkeypatch, pm, pid, data)
    updated = client.patch(f"/projects/{pid}", json={field: value}, headers=pm)
    assert updated.status_code == 200 and updated.json()[field] == value

    r = approve(pm, pid, version)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "inputs_changed"
    assert version_row(pid, version).status == "draft" and gate_state(pid, version) == "waiting_for_pm"
    assert all(t.start is None and t.end is None for t in tasks(pid))
    assert not audit_rows("plan.approved")

    rebuilt = reviewed(monkeypatch, pm, pid, data)
    assert rebuilt != version
    detail = client.get(f"{base(pid)}/versions/{rebuilt}", headers=pm).json()
    assert detail["snapshot"]["project"][field] == value
    assert approve(pm, pid, rebuilt).status_code == 200


@pytest.mark.parametrize("change_project_date", [False, True])
def test_legacy_draft_uses_its_stored_project_dates(monkeypatch, change_project_date):
    pm, pid, data = new_project()
    version = reviewed(monkeypatch, pm, pid, data)
    with SessionLocal() as db:
        draft = pvs.get_version(db, pid, version)
        snapshot = dict(draft.snapshot)
        snapshot.pop("inputs_fingerprint_version")
        snapshot["inputs_fingerprint"] = pvs.inputs_fingerprint(pvs.load_plan_inputs(db, pid), version=1)
        draft.snapshot = snapshot
        db.commit()
    if change_project_date:
        updated = client.patch(f"/projects/{pid}", json={"end_date": "2026-12-05"}, headers=pm)
        assert updated.status_code == 200

    response = approve(pm, pid, version)
    if change_project_date:
        assert response.status_code == 409 and response.json()["detail"]["code"] == "inputs_changed"
        assert all(task.start is None for task in tasks(pid))
    else:
        assert response.status_code == 200, response.text
        assert all(task.start is not None for task in tasks(pid))
    assert version_row(pid, version).snapshot == snapshot


def test_a_superseded_draft_can_not_be_approved(monkeypatch):
    pm, pid, data = new_project()
    v1 = reviewed(monkeypatch, pm, pid, data)
    v2 = reviewed(monkeypatch, pm, pid, data, overrides=[{"task_id": data["task_ids"][0], "duration_days": 4}])
    assert (v1, v2) == (1, 2) and version_row(pid, 1).status == "superseded"
    r = approve(pm, pid, v1)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "not_a_draft"
    assert approve(pm, pid, v2).status_code == 200
    assert [v.status for v in versions(pid)] == ["superseded", "approved"]


def versions(pid):
    with SessionLocal() as s:
        return pvs.list_versions(s, pid)


def test_the_writer_re_checks_everything_even_if_called_directly(monkeypatch):
    """Defence in depth: bypassing the API and the gate still can't approve a bad version."""
    pm, pid, data = new_project()
    v1 = reviewed(monkeypatch, pm, pid, data)
    reviewed(monkeypatch, pm, pid, data, overrides=[{"task_id": data["task_ids"][0], "duration_days": 4}])
    with SessionLocal() as s:
        out = plan_approval.approve_plan(s, project_id=pid, version=v1, decided_by=1, comment="", acknowledge_warnings=True)
        s.commit()
    assert out["written"] is False and out["code"] == "not_a_draft"
    assert version_row(pid, v1).status == "superseded" and audit_rows("plan.approval_blocked")


# ---------------------------------------------------------------------- reject
def test_reject_needs_a_comment_and_changes_nothing_else(monkeypatch):
    pm, pid, data = new_project()
    v = reviewed(monkeypatch, pm, pid, data)
    assert client.post(f"{base(pid)}/versions/{v}/reject", json={"comment": ""}, headers=pm).status_code == 422
    assert client.post(f"{base(pid)}/versions/{v}/reject", json={}, headers=pm).status_code == 422
    r = client.post(f"{base(pid)}/versions/{v}/reject", json={"comment": "Dates are too tight"}, headers=pm)
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    with SessionLocal() as s:
        pv = pvs.get_version(s, pid, v)
        assert pv.status == "rejected" and pv.approved_by is None
        assert s.get(Project, pid).status == "planning"
        assert {r.status for r in pvs.risks_of(s, pv.id, include_dismissed=True)} == {"dismissed"}
        assert all(t.start is None for t in tasks_in(s, pid))
        d = s.scalar(select(Decision).where(Decision.subject_type == "plan_version"))
        assert (d.decision, d.reason) == ("rejected", "Dates are too tight")
    assert approve(pm, pid, v).status_code == 409
    assert audit_rows("plan.rejected")


def test_rejected_then_rebuilt_then_approved(monkeypatch):
    pm, pid, data = new_project()
    v1 = reviewed(monkeypatch, pm, pid, data)
    client.post(f"{base(pid)}/versions/{v1}/reject", json={"comment": "Rework"}, headers=pm)
    v2 = reviewed(monkeypatch, pm, pid, data, overrides=[{"task_id": data["task_ids"][1], "duration_days": 5}])
    assert v2 == 2
    assert approve(pm, pid, v2).status_code == 200
    assert [v.status for v in versions(pid)] == ["rejected", "approved"]


def test_rejecting_a_new_draft_keeps_the_project_active_if_a_plan_is_approved(monkeypatch):
    pm, pid, data = new_project()
    v1 = reviewed(monkeypatch, pm, pid, data)
    approve(pm, pid, v1)
    v2 = reviewed(monkeypatch, pm, pid, data, overrides=[{"task_id": data["task_ids"][1], "duration_days": 6}])
    client.post(f"{base(pid)}/versions/{v2}/reject", json={"comment": "No"}, headers=pm)
    with SessionLocal() as s:
        assert s.get(Project, pid).status == "active"
        assert pvs.current_approved(s, pid).version == v1


# --------------------------------------------------- versions V1 -> V2 -> V3 + diff
def test_three_versions_snapshots_and_diff(monkeypatch):
    pm, pid, data = new_project()
    t_design, t_api, t_ui = data["task_ids"][0], data["task_ids"][1], data["task_ids"][2]
    ahmed, sara = data["member_ids"]
    v1 = reviewed(monkeypatch, pm, pid, data)  # the initial plan
    assert approve(pm, pid, v1).status_code == 200
    v2 = reviewed(monkeypatch, pm, pid, data, overrides=[{"task_id": t_design, "duration_days": 4}])  # dates change
    assert approve(pm, pid, v2).status_code == 200
    v3 = reviewed(monkeypatch, pm, pid, data, overrides=[  # a resource conflict appears, then is resolved by the PM
        {"task_id": t_design, "duration_days": 4}, {"task_id": t_ui, "assignee_member_id": sara}])
    assert (v1, v2, v3) == (1, 2, 3)

    d12 = client.get(f"{base(pid)}/diff", params={"from_version": 1, "to_version": 2}, headers=pm).json()
    design = [c for c in d12["changes"] if c["id"] == t_design and c["field"] == "duration_days"][0]
    assert (design["old"], design["new"]) == (2, 4)
    end = [c for c in d12["changes"] if c["field"] == "project_end"][0]
    assert end["label"] == "End date" and end["old"] < end["new"]

    # every version keeps its own snapshot, untouched by later versions
    snaps = {v.version: v.snapshot for v in versions(pid)}
    assert [t["duration_days"] for t in snaps[1]["tasks"]][0] == 2 and [t["duration_days"] for t in snaps[2]["tasks"]][0] == 4
    assert snaps[1]["schedule"]["project_end"] != snaps[2]["schedule"]["project_end"]

    detail3 = client.get(f"{base(pid)}/versions/{v3}", headers=pm).json()
    assert detail3["diff_from_prev"]["summary"]["changes"] >= 1 and detail3["base_version"] == 2
    assert detail3["diff_vs_approved"]["against_version"] == 2
    listing = client.get(f"{base(pid)}/versions", headers=pm).json()
    assert [(x["version"], x["status"]) for x in listing] == [(1, "superseded"), (2, "approved"), (3, "draft")]
    assert client.get(f"{base(pid)}/diff", params={"from_version": 1, "to_version": 9}, headers=pm).status_code == 404

    assert approve(pm, pid, v3).status_code == 200
    with SessionLocal() as s:
        assert [r.status for r in s.scalars(select(Risk).where(Risk.plan_version_id == pvs.get_version(s, pid, 1).id))] \
            and {r.status for r in s.scalars(select(Risk).where(Risk.plan_version_id == pvs.get_version(s, pid, 1).id))} == {"superseded"}
        assert sum(1 for v in pvs.list_versions(s, pid) if v.status == "approved") == 1


# ------------------------------------------------------------- generate / review
def test_generate_validates_project_state_overrides_and_duplicates():
    pm = pm_headers()
    pid = make_project(pm)["id"]  # status 'setup'
    r = client.post(f"{base(pid)}/generate", json={}, headers=pm)
    assert r.status_code == 409 and "approve the scope first" in r.json()["detail"]
    _, pid2, data = new_project()
    assert client.post(f"{base(pid2)}/generate", json={"overrides": [{"task_id": 1, "duration_days": 0}]}, headers=pm).status_code == 422
    assert client.post(f"{base(pid2)}/generate", json={"surprise": 1}, headers=pm).status_code == 422
    generate(pm, pid2)
    r = client.post(f"{base(pid2)}/generate", json={}, headers=pm)
    assert r.status_code == 409 and r.json()["detail"]["job_id"]


def test_review_needs_a_draft_and_rules_only_runs_without_a_model(monkeypatch):
    pm, pid, data = new_project()
    assert client.post(f"{base(pid)}/review", headers=pm).status_code == 409
    generate(pm, pid)
    process_one()
    with SessionLocal() as s:
        s.execute(text("UPDATE jobs SET status='cancelled' WHERE type='agent4.review'"))
        s.commit()
    monkeypatch.setattr("app.llm.langchain_model.get_langchain_model",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no model expected")))
    r = client.post(f"{base(pid)}/review", json={"rules_only": True}, headers=pm)
    assert r.status_code == 202
    process_one()
    assert version_row(pid, 1).snapshot["review"]["source"] == "rules"
    assert gate_state(pid, 1) == "waiting_for_pm"


def test_risks_endpoint_filters_by_status(monkeypatch):
    pm, pid, data = new_project()
    v = reviewed(monkeypatch, pm, pid, data)
    drafts = client.get(f"{base(pid)}/risks", params={"status": "draft"}, headers=pm).json()
    assert drafts and all(r["status"] == "draft" for r in drafts)
    assert client.get(f"{base(pid)}/risks", params={"status": "open"}, headers=pm).json() == []
    approve(pm, pid, v)
    assert client.get(f"{base(pid)}/risks", params={"status": "open"}, headers=pm).json()
    assert client.get(f"{base(pid)}/risks", params={"status": "bogus"}, headers=pm).status_code == 422


def test_open_gate_is_idempotent_and_gate_status_is_reported(monkeypatch):
    pm, pid, data = new_project()
    v = reviewed(monkeypatch, pm, pid, data)
    r1 = client.post(f"{base(pid)}/versions/{v}/open-gate", headers=pm)
    r2 = client.post(f"{base(pid)}/versions/{v}/open-gate", headers=pm)
    assert r1.status_code == r2.status_code == 200 and r1.json()["state"] == "waiting_for_pm"
    assert r1.json()["run_id"] == r2.json()["run_id"]
    assert client.get(f"{base(pid)}/versions/{v}/gate", headers=pm).json()["state"] == "waiting_for_pm"


# ------------------------------------------------------------- gate unit tests
def test_thread_ids_are_per_project_and_version():
    assert make_plan_thread_id(3, 2) == "project:3:plan:2"
    for bad in ((0, 1), (1, 0), (-1, 1), (True, 1), ("1", 1)):
        with pytest.raises(ValueError):
            make_plan_thread_id(*bad)


def test_plan_decision_parsing_is_strict():
    ok = parse_plan_decision({"decision": "approved", "decided_by": 4})
    assert isinstance(ok, PlanDecision) and ok.acknowledge_warnings is False
    for bad in ({"decision": "modified", "decided_by": 1}, {"decision": "rejected", "decided_by": 1},
                {"decision": "approved", "decided_by": "1"}, {"decision": "approved", "decided_by": 0},
                {"decision": "approved", "decided_by": 1, "extra": 1}, {"decided_by": 1}):
        with pytest.raises(InvalidPlanDecision):
            parse_plan_decision(bad)


def test_gate_refuses_to_open_for_non_drafts_and_resume_without_open(monkeypatch):
    pm, pid, data = new_project()
    v = reviewed(monkeypatch, pm, pid, data)
    approve(pm, pid, v)
    with postgres_checkpointer() as saver:
        gate = PlanGate(saver)
        with pytest.raises(PlanGateNotAllowed, match="only a draft"):
            gate.open(pid, v)
        with pytest.raises(PlanGateNotAllowed, match="not found"):
            gate.open(pid, 42)
        with pytest.raises(PlanGateNotWaiting):
            gate.approve(pid, v, 1)
        with pytest.raises(PlanGateNotWaiting):
            gate.reject(pid, 42, 1, "x")


def test_a_gate_resumed_with_a_bad_payload_writes_nothing(monkeypatch):
    pm, pid, data = new_project()
    v = reviewed(monkeypatch, pm, pid, data)
    with postgres_checkpointer() as saver:
        gate = PlanGate(saver)
        with pytest.raises(InvalidPlanDecision):
            gate._resume(pid, v, {"decision": "approved", "decided_by": "pm"})
    assert version_row(pid, v).status == "draft"


# ------------------------------------------------------------ agents can't approve
def _py_files(*folders):
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    return root, [f for folder in folders for f in (root / folder).rglob("*.py")]


def _imports_and_calls(path):
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            found.add(node.module or "")
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
    return found


def test_no_agent_or_tool_module_can_reach_the_approval_code():
    """Real imports/calls only (docstrings may mention the gate)."""
    root, files = _py_files("agents", "tools")
    forbidden = {"plan_approval", "plan_gate", "approve_plan", "reject_plan", "PlanGate", "DbPlanWriter",
                 "app.services.plan_approval", "app.orchestrator.plan_gate"}
    offenders = [str(f.relative_to(root)) for f in files if _imports_and_calls(f) & forbidden]
    assert offenders == []


def test_only_plan_approval_sets_a_plan_version_to_approved():
    import ast

    root, files = _py_files(".")
    setters = []
    for f in files:
        src = f.read_text(encoding="utf-8")
        if "PlanVersion" not in src:
            continue
        for node in ast.walk(ast.parse(src)):
            is_attr = (isinstance(node, ast.Assign) and any(isinstance(t, ast.Attribute) and t.attr == "status"
                                                           for t in node.targets))
            is_kw = isinstance(node, ast.keyword) and node.arg == "status"
            value = node.value if (is_attr or is_kw) else None
            if isinstance(value, ast.Constant) and value.value == "approved":
                setters.append(f.relative_to(root).as_posix())
    assert sorted(set(setters)) == ["services/plan_approval.py"]
