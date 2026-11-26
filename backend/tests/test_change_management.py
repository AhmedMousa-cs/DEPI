"""Manual changes are accepted as drafts and published through the plan gate."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from io import BytesIO
from threading import Barrier

import pytest
from pypdf import PdfReader
from sqlalchemy import select

from app.api import plan as plan_api
from app.db.models import ChangeRequest, Risk, ScheduleDraft, Task
from app.db.session import SessionLocal
from app.services import changes, plan_approval
from app.services.jobs import process_one
from helpers import assign_tl, audit_rows, client, make_project, pm_headers, tl_headers
from test_plan_gate_api import approve, new_project, reviewed, version_row
from test_portfolio_api import _seed_member, _seed_story_task

TODAY = date.today()
START = date(2026, 11, 29)  # Sunday in the default working calendar


def _propose(pid, tid, **fields):
    body = {"task_id": tid, "reason": "stakeholder asked to move the deadline", **fields}
    return client.post(f"/projects/{pid}/changes", json=body, headers=pm_headers())


def _decide(pid, cid, decision="approved"):
    return client.post(f"/projects/{pid}/changes/{cid}/decision",
                       json={"decision": decision, "comment": "ok"}, headers=pm_headers())


def test_propose_computes_deterministic_impact():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    tid = _seed_story_task(pid, "Build the login page", estimate=16,
                           start=START, end=START + timedelta(days=3), critical=True)
    dep_tid = _seed_story_task(pid, "Test the login flow", estimate=8,
                               start=START + timedelta(days=4), end=START + timedelta(days=8))
    with SessionLocal() as s:
        s.execute(__import__("sqlalchemy").text(
            "INSERT INTO task_dependencies (task_id, depends_on_task_id) VALUES (:a, :b)"),
            {"a": dep_tid, "b": tid})
        s.commit()

    r = _propose(pid, tid, end=str(START + timedelta(days=10)))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "draft" and body["kind"] == "task_update"
    assert body["payload"]["before"]["end"] == str(START + timedelta(days=3))
    assert body["payload"]["after"]["end"] == str(START + timedelta(days=10))
    # critical delay + dependent shift, both computed by code
    assert body["impact"]["severity"] == "high"
    msgs = " ".join(i["message"] for i in body["impact"]["impacts"])
    assert "critical-path task delayed by 7 day(s)" in msgs
    assert f"dependent task #{dep_tid}" in msgs

    rows = [a for a in audit_rows("change.proposed") if a.entity_id and int(a.entity_id) == body["id"]]
    assert rows and rows[-1].after["severity"] == "high"


def _published_baseline(monkeypatch):
    pm, pid, data = new_project(tasks=[("Build reports page", 8, 0, []), ("Test reports page", 8, 1, [0])])
    assert client.patch(f"/projects/{pid}", json={"start_date": START.isoformat()}, headers=pm).status_code == 200
    version = reviewed(monkeypatch, pm, pid, data)
    assert approve(pm, pid, version).status_code == 200
    return pm, pid, data, version


def _review_change_plan(pm, pid, version):
    response = client.post(f"/projects/{pid}/plan/review", json={"rules_only": True}, headers=pm)
    assert response.status_code == 202, response.text
    assert process_one()
    detail = client.get(f"/projects/{pid}/plan/versions/{version}", headers=pm).json()
    assert detail["snapshot"]["review"]["source"] == "rules"
    return detail


def test_accepted_change_publishes_only_through_plan_review(monkeypatch):
    pm, pid, data, first_version = _published_baseline(monkeypatch)
    tid, dependent = data["task_ids"]
    member = data["member_ids"][1]
    requested_end = START + timedelta(days=3)
    r = _propose(pid, tid, title="Build revised reports page", estimate_hours=32.0,
                 end=requested_end.isoformat(), assignee_member_id=member)
    assert r.status_code == 201, r.text
    cid = r.json()["id"]

    r = _decide(pid, cid)
    assert r.status_code == 200, r.text
    accepted = r.json()
    assert accepted["status"] == "approved" and accepted["publication_status"] == "pending"
    version = accepted["plan_version"]
    assert version > first_version and version_row(pid, first_version).status == "approved"
    with SessionLocal() as s:
        t = s.get(Task, tid)
        assert (t.title, float(t.estimate_hours), t.start, t.end, t.assignee_member_id) == (
            "Build reports page", 8.0, START, START, data["member_ids"][0])
        assert s.get(Task, dependent).start == START + timedelta(days=1)

    assert not audit_rows("change.published")
    assert _decide(pid, cid).status_code == 409
    assert client.post(f"/projects/{pid}/plan/generate", json={}, headers=pm).status_code == 409
    detail = _review_change_plan(pm, pid, version)
    assert detail["snapshot"]["change_request_id"] == cid
    assert {d["field"] for d in detail["diff_vs_approved"]["changes"]} >= {"title", "estimate_hours", "end", "assignee_member_id"}
    response = approve(pm, pid, version, acknowledge_warnings=bool(detail["warnings"]),
                       warnings_token=detail["warnings_token"])
    assert response.status_code == 200, response.text
    preview = client.get(f"/projects/{pid}/scheduling", headers=pm).json()
    assert preview["plan_version"] == version
    assert preview["review_status"] == "approved"
    assert preview["reviewed_by"] is not None and preview["reviewed_at"] is not None
    with SessionLocal() as s:
        t = s.get(Task, tid)
        assert (t.title, float(t.estimate_hours), t.end, t.assignee_member_id) == (
            "Build revised reports page", 32.0, requested_end, member)
        assert s.get(Task, dependent).start == requested_end + timedelta(days=1)
    assert len(audit_rows("change.published")) == 1
    assert client.get(f"/projects/{pid}/changes", headers=pm).json()[0]["publication_status"] == "published"

    later_version = reviewed(monkeypatch, pm, pid, data)
    later = version_row(pid, later_version)
    assert next(task for task in later.snapshot["tasks"] if task["id"] == tid)["end"] == requested_end.isoformat()
    assert approve(pm, pid, later_version).status_code == 200
    assert version_row(pid, version).status == "superseded"
    assert client.get(f"/projects/{pid}/changes", headers=pm).json()[0]["publication_status"] == "published"


@pytest.mark.parametrize("legacy_preview", [False, True])
def test_rejecting_change_or_plan_keeps_published_tasks(monkeypatch, legacy_preview):
    pm, pid, data, first_version = _published_baseline(monkeypatch)
    tid = data["task_ids"][0]
    schedule_url = f"/projects/{pid}/scheduling"
    baseline_preview = client.get(schedule_url, headers=pm).json()
    assert baseline_preview["plan_version"] == first_version
    assert baseline_preview["review_status"] == "approved"
    if legacy_preview:
        # Agent 3 previews stored before the marker was added can be resolved via their audit.
        with SessionLocal() as s:
            preview = s.get(ScheduleDraft, baseline_preview["id"])
            preview.payload = {key: value for key, value in preview.payload.items() if key != "plan_version"}
            s.commit()

    declined = _propose(pid, tid, estimate_hours=14.0).json()["id"]
    assert _decide(pid, declined, "rejected").json()["status"] == "rejected"
    pending = _propose(pid, tid, estimate_hours=16.0).json()["id"]
    version = _decide(pid, pending).json()["plan_version"]
    pending_preview = client.get(schedule_url, headers=pm).json()
    assert pending_preview["plan_version"] == version
    assert pending_preview["review_status"] == "draft"
    assert pending_preview["id"] != baseline_preview["id"]
    _review_change_plan(pm, pid, version)
    r = client.post(f"/projects/{pid}/plan/versions/{version}/reject", json={"comment": "Keep plan"}, headers=pm)
    assert r.status_code == 200, r.text
    assert version_row(pid, first_version).status == "approved"
    restored = client.get(schedule_url, headers=pm).json()
    assert restored["id"] == baseline_preview["id"]
    assert restored["plan_version"] == first_version
    assert restored["review_status"] == "approved"
    project = next(card for card in client.get("/portfolio/overview", headers=pm).json()["projects"]
                   if card["project_id"] == pid)
    assert project["schedule"]["latest_draft_id"] == restored["id"]
    with SessionLocal() as s:
        assert float(s.get(Task, tid).estimate_hours) == 8.0
        assert s.get(ScheduleDraft, pending_preview["id"]) is not None
    assert not audit_rows("change.published")
    history = client.get(f"/projects/{pid}/changes", headers=pm).json()
    assert history[0]["publication_status"] == "rejected"


def test_unreviewed_change_draft_can_be_rejected_but_cannot_publish(monkeypatch):
    pm, pid, data, first_version = _published_baseline(monkeypatch)
    cid = _propose(pid, data["task_ids"][0], title="Candidate task revision").json()["id"]
    version = _decide(pid, cid).json()["plan_version"]
    assert version_row(pid, version).snapshot["review"] is None
    assert approve(pm, pid, version).status_code == 409

    url = f"/projects/{pid}/plan/versions/{version}/reject"
    rejected = client.post(url, json={"comment": "Discard the unreviewed change"}, headers=pm)
    assert rejected.status_code == 200, rejected.text
    assert version_row(pid, first_version).status == "approved"
    assert version_row(pid, version).status == "rejected"
    assert client.get(f"/projects/{pid}/changes", headers=pm).json()[0]["publication_status"] == "rejected"
    assert not audit_rows("change.published")
    assert client.post(f"/projects/{pid}/plan/generate", json={}, headers=pm).status_code == 202


def test_review_in_progress_preserves_the_waiting_gate_and_previous_plan(monkeypatch):
    pm, pid, data, previous = _published_baseline(monkeypatch)
    cid = _propose(pid, data["task_ids"][0], title="Change waiting for a second review").json()["id"]
    version = _decide(pid, cid).json()["plan_version"]
    detail = _review_change_plan(pm, pid, version)
    warning_args = {"acknowledge_warnings": bool(detail["warnings"]), "warnings_token": detail["warnings_token"]}
    queued = client.post(f"/projects/{pid}/plan/review", json={"rules_only": True}, headers=pm)
    assert queued.status_code == 202, queued.text
    refused = approve(pm, pid, version, **warning_args)
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "review_in_progress"
    assert version_row(pid, previous).status == "approved" and version_row(pid, version).status == "draft"
    assert not audit_rows("change.published")
    assert process_one()
    detail = client.get(f"/projects/{pid}/plan/versions/{version}", headers=pm).json()
    assert approve(pm, pid, version, acknowledge_warnings=bool(detail["warnings"]),
                   warnings_token=detail["warnings_token"]).status_code == 200


def test_published_change_risks_follow_approval_in_portfolio_and_pdf(monkeypatch):
    pm, pid, data, _ = _published_baseline(monkeypatch)
    assign_tl(pid)
    cid = _propose(pid, data["task_ids"][0], title="Candidate task revision").json()["id"]
    version = _decide(pid, cid).json()["plan_version"]
    detail = _review_change_plan(pm, pid, version)
    distinct_risk = "Pilot <deployment> & handoff"
    with SessionLocal() as s:
        s.add(Risk(project_id=pid, plan_version_id=detail["id"], title=distinct_risk,
                   description="Resources overlap with a release window", severity="critical", likelihood="medium",
                   evidence_refs=[{"type": "task", "id": str(data["task_ids"][0])}], status="draft", created_by="test"))
        s.commit()

    def risk_items(headers):
        return client.get("/portfolio/risks", headers=headers).json()["risks"]

    def report_text():
        report = client.get(f"/projects/{pid}/report.pdf", headers=pm)
        assert report.status_code == 200, report.text
        return " ".join(" ".join(page.extract_text() or "" for page in PdfReader(BytesIO(report.content)).pages).split())

    assert any(r["message"] == "Long pole 0" for r in risk_items(pm))
    assert all(r["message"] != distinct_risk for r in risk_items(pm))
    assert f"{START} to {START + timedelta(days=1)}" in report_text()
    assert distinct_risk not in report_text()
    assert approve(pm, pid, version, acknowledge_warnings=bool(detail["warnings"]),
                   warnings_token=detail["warnings_token"]).status_code == 200
    after = risk_items(pm)
    assert any(r["message"] == distinct_risk and r["severity"] == "critical" for r in after)
    assert all(r["message"] != "Long pole 0" for r in after)
    assert distinct_risk in report_text()
    assert all(r["message"] != distinct_risk for r in risk_items(tl_headers()))


def test_duplicate_proposals_cannot_stage_or_publish_the_same_change_twice(monkeypatch):
    pm, pid, data, first_version = _published_baseline(monkeypatch)
    tid = data["task_ids"][0]
    proposals = [_propose(pid, tid, title="Revised reports task") for _ in range(2)]
    assert [proposal.status_code for proposal in proposals] == [201, 201]
    request_ids = [proposal.json()["id"] for proposal in proposals]
    assert len(set(request_ids)) == 2

    ready_to_accept = Barrier(2, timeout=15)

    def accept(cid):
        ready_to_accept.wait()
        return client.post(f"/projects/{pid}/changes/{cid}/decision", json={"decision": "approved"}, headers=pm)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(accept, request_ids))
    assert sorted(r.status_code for r in responses) == [200, 409], [r.text for r in responses]
    accepted = next(r.json() for r in responses if r.status_code == 200)
    pending = next(r.json()["detail"] for r in responses if r.status_code == 409)
    version = accepted["plan_version"]
    assert pending["code"] == "change_plan_pending" and pending["plan_version"] == version
    assert [p["version"] for p in client.get(f"/projects/{pid}/plan/versions", headers=pm).json()] == [first_version, version]

    detail = _review_change_plan(pm, pid, version)
    ready_to_publish = Barrier(2, timeout=15)
    original_precheck = plan_api._precheck

    def simultaneous_precheck(*args, **kwargs):
        original_precheck(*args, **kwargs)
        ready_to_publish.wait()

    monkeypatch.setattr(plan_api, "_precheck", simultaneous_precheck)
    with ThreadPoolExecutor(max_workers=2) as pool:
        publications = [pool.submit(approve, pm, pid, version, acknowledge_warnings=bool(detail["warnings"]),
                                    warnings_token=detail["warnings_token"]) for _ in range(2)]
        responses = [result.result(timeout=30) for result in publications]
    assert sorted(r.status_code for r in responses) == [200, 409], [r.text for r in responses]
    assert version_row(pid, version).status == "approved"
    assert len(audit_rows("change.published")) == 1

    other_id = next(cid for cid in request_ids if cid != accepted["id"])
    retry = _decide(pid, other_id)
    assert retry.status_code == 409 and retry.json()["detail"]["code"] == "change_inputs_changed"


def test_stage_failure_rolls_back_version_and_request(monkeypatch):
    pm, pid, data, first_version = _published_baseline(monkeypatch)
    tid = data["task_ids"][0]
    previous_draft = reviewed(monkeypatch, pm, pid, data)
    cid = _propose(pid, tid, title="Revised reports task").json()["id"]

    def fail_preview(*args, **kwargs):
        raise RuntimeError("schedule preview storage failed")

    monkeypatch.setattr(changes, "save_schedule_draft", fail_preview)
    with pytest.raises(RuntimeError, match="schedule preview storage failed"):
        _decide(pid, cid)

    plans = client.get(f"/projects/{pid}/plan/versions", headers=pm).json()
    assert [(p["version"], p["status"]) for p in plans] == [(first_version, "approved"), (previous_draft, "draft")]
    with SessionLocal() as s:
        request = s.get(ChangeRequest, cid)
        assert (request.status, request.plan_version_id) == ("draft", None)
        assert s.get(Task, tid).title == "Build reports page"
    assert not audit_rows("change.decision") and not audit_rows("change.published")


def test_publication_failure_rolls_back_task_risks_version_and_audit(monkeypatch):
    pm, pid, data, first_version = _published_baseline(monkeypatch)
    task_id = data["task_ids"][0]
    cid = _propose(pid, task_id, title="Published only after commit", estimate_hours=16).json()["id"]
    version = _decide(pid, cid).json()["plan_version"]
    detail = _review_change_plan(pm, pid, version)
    write_audit = plan_approval.write_audit

    def fail_final_audit(db, actor, action, *args, **kwargs):
        write_audit(db, actor, action, *args, **kwargs)
        if action == "plan.approved":
            db.flush()
            raise RuntimeError("publication transaction interrupted")

    warning_args = {"acknowledge_warnings": bool(detail["warnings"]), "warnings_token": detail["warnings_token"]}
    with monkeypatch.context() as patch:
        patch.setattr(plan_approval, "write_audit", fail_final_audit)
        with pytest.raises(RuntimeError, match="publication transaction interrupted"):
            with SessionLocal() as db:
                plan_approval.approve_plan(db, project_id=pid, version=version, decided_by=1,
                                           comment="Test rollback", **warning_args)
                db.commit()

    previous = version_row(pid, first_version)
    assert previous.status == "approved" and version_row(pid, version).status == "draft"
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        assert (task.title, task.estimate_hours) == ("Build reports page", 8)
        previous_risks = db.scalars(select(Risk).where(Risk.plan_version_id == previous.id)).all()
        assert previous_risks and all(risk.status == "open" for risk in previous_risks)
    assert len(audit_rows("plan.approved")) == 1 and not audit_rows("change.published")
    assert approve(pm, pid, version, **warning_args).status_code == 200


def test_task_change_after_staging_refuses_publication(monkeypatch):
    pm, pid, data, first_version = _published_baseline(monkeypatch)
    tid = data["task_ids"][0]
    cid = _propose(pid, tid, title="Revised reports task").json()["id"]
    version = _decide(pid, cid).json()["plan_version"]
    detail = _review_change_plan(pm, pid, version)
    with SessionLocal() as s:
        s.get(Task, tid).title = "Task changed since staging"
        s.commit()

    refused = approve(pm, pid, version, acknowledge_warnings=bool(detail["warnings"]),
                      warnings_token=detail["warnings_token"])
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "inputs_changed"
    assert version_row(pid, first_version).status == "approved"
    assert version_row(pid, version).status == "draft"
    assert not audit_rows("change.published")
    with SessionLocal() as s:
        assert s.get(Task, tid).title == "Task changed since staging"


def test_stale_request_and_foreign_assignee_are_refused(monkeypatch):
    pm, pid, data, _ = _published_baseline(monkeypatch)
    tid = data["task_ids"][0]
    other = make_project(pm)["id"]
    member = _seed_member(other, "Other team", capacity=40)
    assert _propose(pid, tid, assignee_member_id=member).status_code == 422
    assert _propose(pid, tid, end=(START + timedelta(days=5)).isoformat()).status_code == 422
    assert _propose(pid, tid, start=(START + timedelta(days=2)).isoformat()).status_code == 422
    cid = _propose(pid, tid, title="Stale proposed title").json()["id"]
    with SessionLocal() as s:
        s.get(Task, tid).title = "Updated since proposal"
        s.commit()
    assert _decide(pid, cid).status_code == 409
    with SessionLocal() as s:
        assert s.get(ChangeRequest, cid).status == "draft"


def test_infeasible_task_window_does_not_stage_a_draft(monkeypatch):
    pm, pid, data, version = _published_baseline(monkeypatch)
    dependent = data["task_ids"][1]
    proposal = _propose(pid, dependent, start=START.isoformat(), end=START.isoformat())
    assert proposal.status_code == 201, proposal.text
    cid = proposal.json()["id"]
    denied = _decide(pid, cid)
    assert denied.status_code == 422, denied.text
    assert denied.json()["detail"]["code"] == "change_schedule_infeasible"
    plans = client.get(f"/projects/{pid}/plan/versions", headers=pm).json()
    assert [plan["version"] for plan in plans] == [version]
    with SessionLocal() as s:
        assert s.get(ChangeRequest, cid).status == "draft"
        assert s.get(Task, dependent).start == START + timedelta(days=1)


def test_change_schedule_uses_updated_project_start(monkeypatch):
    pm, pid, data, _ = _published_baseline(monkeypatch)
    task_id = data["task_ids"][0]
    new_start = START + timedelta(days=2)
    assert client.patch(f"/projects/{pid}", json={"start_date": new_start.isoformat()}, headers=pm).status_code == 200
    cid = _propose(pid, task_id, title="Revised after the kickoff moved").json()["id"]
    response = _decide(pid, cid)
    assert response.status_code == 200, response.text
    version = version_row(pid, response.json()["plan_version"])
    assert version.snapshot["schedule"]["project_start"] == new_start.isoformat()
    assert next(task["start"] for task in version.snapshot["tasks"] if task["id"] == task_id) == new_start.isoformat()
    with SessionLocal() as s:
        assert s.get(Task, task_id).start == START


def test_unrelated_change_carries_legacy_manual_window_forward(monkeypatch):
    pm, pid, data, _ = _published_baseline(monkeypatch)
    task_id, dependent_id = data["task_ids"]
    moved = START + timedelta(days=2)
    window = {"start": moved.isoformat(), "end": moved.isoformat()}
    with SessionLocal() as s:
        task = s.get(Task, dependent_id)
        before = {"start": task.start.isoformat(), "end": task.end.isoformat()}
        task.start = task.end = moved
        s.add(ChangeRequest(project_id=pid, target_task_id=dependent_id, status="approved",
                            payload={"before": before, "after": window}, reason="Legacy manual move", created_by="test"))
        s.commit()

    cid = _propose(pid, task_id, title="Revised parent task").json()["id"]
    response = _decide(pid, cid)
    assert response.status_code == 200, response.text
    version = response.json()["plan_version"]
    detail = _review_change_plan(pm, pid, version)
    assert detail["snapshot"]["fixed_dates"] == [{"task_id": dependent_id, **window}]
    assert approve(pm, pid, version, acknowledge_warnings=bool(detail["warnings"]),
                   warnings_token=detail["warnings_token"]).status_code == 200
    with SessionLocal() as s:
        task = s.get(Task, dependent_id)
        assert (task.start, task.end) == (moved, moved)


def test_capacity_impact_flags_overload():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    mid = _seed_member(pid, "Sara", capacity=10.0)
    tid = _seed_story_task(pid, "Migrate the database", estimate=2,
                           start=TODAY, end=TODAY + timedelta(days=7), member_id=mid)
    _seed_story_task(pid, "Write migration tests", estimate=60, start=TODAY,
                     end=TODAY + timedelta(days=7), member_id=mid)
    r = _propose(pid, tid, estimate_hours=30.0)
    assert r.status_code == 201, r.text
    impact = r.json()["impact"]
    assert impact["severity"] == "high"
    msgs = " ".join(i["message"] for i in impact["impacts"])
    assert "Sara's load in this window would be 90h" in msgs
    assert "capacity of 11h" in msgs  # 10h/week over an 8-day window


def test_draft_task_and_unknown_change_are_rejected():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    draft_id = _seed_story_task(pid, "Draft work item", estimate=5, review="draft")
    assert _propose(pid, draft_id, estimate_hours=9.0).status_code == 409
    assert _propose(pid, 999999, estimate_hours=9.0).status_code == 404
    # no-op proposal
    tid = _seed_story_task(pid, "Stable approved item", estimate=5,
                           start=TODAY, end=TODAY + timedelta(days=2))
    assert _propose(pid, tid).status_code == 422
    assert _decide(pid, 999999).status_code == 404


def test_team_leader_cannot_use_change_management():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    tid = _seed_story_task(pid, "Leader-locked task", estimate=5,
                           start=TODAY, end=TODAY + timedelta(days=2))
    assign_tl(pid)
    cid = _propose(pid, tid, estimate_hours=8.0).json()["id"]
    url = f"/projects/{pid}/changes"
    body = {"task_id": tid, "reason": "try to change", "estimate_hours": 9.0}
    for headers, wanted in ((tl_headers(), 403), ({}, 401)):
        assert client.get(url, headers=headers).status_code == wanted
        assert client.post(url, json=body, headers=headers).status_code == wanted
        assert client.post(f"{url}/{cid}/decision", json={"decision": "approved"}, headers=headers).status_code == wanted


def test_change_list_is_project_scoped_and_filtered():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    other = make_project(pm)["id"]
    tid = _seed_story_task(pid, "Scoped task", estimate=5, start=TODAY, end=TODAY + timedelta(days=2))
    _propose(pid, tid, estimate_hours=6.0)
    rows = client.get(f"/projects/{pid}/changes", headers=pm).json()
    assert all(c["project_id"] == pid for c in rows) and rows
    assert client.get(f"/projects/{other}/changes", headers=pm).json() == []
    assert client.get(f"/projects/{pid}/changes", params={"status": "draft"}, headers=pm).json()
