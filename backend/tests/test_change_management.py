"""Manual changes are accepted as drafts and published through the plan gate."""
from datetime import date, timedelta

from app.db.models import ChangeRequest, Task
from app.db.session import SessionLocal
from app.services.jobs import process_one
from helpers import audit_rows, client, make_project, pm_headers, tl_headers
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


def test_rejecting_change_or_plan_keeps_published_tasks(monkeypatch):
    pm, pid, data, first_version = _published_baseline(monkeypatch)
    tid = data["task_ids"][0]
    declined = _propose(pid, tid, estimate_hours=14.0).json()["id"]
    assert _decide(pid, declined, "rejected").json()["status"] == "rejected"
    pending = _propose(pid, tid, estimate_hours=16.0).json()["id"]
    version = _decide(pid, pending).json()["plan_version"]
    _review_change_plan(pm, pid, version)
    r = client.post(f"/projects/{pid}/plan/versions/{version}/reject", json={"comment": "Keep plan"}, headers=pm)
    assert r.status_code == 200, r.text
    assert version_row(pid, first_version).status == "approved"
    with SessionLocal() as s:
        assert float(s.get(Task, tid).estimate_hours) == 8.0
    assert not audit_rows("change.published")
    history = client.get(f"/projects/{pid}/changes", headers=pm).json()
    assert history[0]["publication_status"] == "rejected"


def test_stale_request_and_foreign_assignee_are_refused(monkeypatch):
    pm, pid, data, _ = _published_baseline(monkeypatch)
    tid = data["task_ids"][0]
    other = make_project(pm)["id"]
    member = _seed_member(other, "Other team", capacity=40)
    assert _propose(pid, tid, assignee_member_id=member).status_code == 422
    assert _propose(pid, tid, end=(START + timedelta(days=5)).isoformat()).status_code == 422
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
    body = {"task_id": tid, "reason": "try to change", "estimate_hours": 8.0}
    r = client.post(f"/projects/{pid}/changes", json=body, headers=tl_headers())
    assert r.status_code == 403
    r = client.post(f"/projects/{pid}/changes/999/decision",
                    json={"decision": "approved"}, headers=tl_headers())
    assert r.status_code == 403


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
