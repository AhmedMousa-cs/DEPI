"""M11 change management: approved tasks are edited through change requests.

Proposing stores a field-level diff + a deterministic impact analysis; approving
applies the change and audits it; rejecting closes it untouched. Draft tasks
never go through change management (409) — they are edited in review.
"""
from datetime import date, timedelta

from app.db.models import Task
from app.db.session import SessionLocal
from helpers import audit_rows, client, make_project, pm_headers, tl_headers
from test_portfolio_api import _seed_member, _seed_story_task

TODAY = date.today()


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
                           start=TODAY, end=TODAY + timedelta(days=5), critical=True)
    dep_tid = _seed_story_task(pid, "Test the login flow", estimate=8,
                               start=TODAY + timedelta(days=6), end=TODAY + timedelta(days=8))
    with SessionLocal() as s:
        s.execute(__import__("sqlalchemy").text(
            "INSERT INTO task_dependencies (task_id, depends_on_task_id) VALUES (:a, :b)"),
            {"a": dep_tid, "b": tid})
        s.commit()

    r = _propose(pid, tid, end=str(TODAY + timedelta(days=12)))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "draft" and body["kind"] == "task_update"
    assert body["payload"]["before"]["end"] == str(TODAY + timedelta(days=5))
    assert body["payload"]["after"]["end"] == str(TODAY + timedelta(days=12))
    # critical delay + dependent shift, both computed by code
    assert body["impact"]["severity"] == "high"
    msgs = " ".join(i["message"] for i in body["impact"]["impacts"])
    assert "critical-path task delayed by 7 day(s)" in msgs
    assert f"dependent task #{dep_tid}" in msgs

    rows = [a for a in audit_rows("change.proposed") if a.entity_id and int(a.entity_id) == body["id"]]
    assert rows and rows[-1].after["severity"] == "high"


def test_approve_applies_the_change_and_reject_closes_it():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    tid = _seed_story_task(pid, "Build the reports page", estimate=10,
                           start=TODAY, end=TODAY + timedelta(days=4))

    r = _propose(pid, tid, estimate_hours=20.0, end=str(TODAY + timedelta(days=9)))
    assert r.status_code == 201, r.text
    cid = r.json()["id"]

    r = _decide(pid, cid, "approved")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"
    with SessionLocal() as s:
        t = s.get(Task, tid)
        assert float(t.estimate_hours) == 20.0 and str(t.end) == str(TODAY + timedelta(days=9))

    rows = [a for a in audit_rows("change.decision") if a.entity_id and int(a.entity_id) == cid]
    assert rows and rows[-1].after["status"] == "approved"
    assert rows[-1].after["applied"]["estimate_hours"] == 20.0

    # a decided request cannot be decided again
    assert _decide(pid, cid, "rejected").status_code == 409

    # reject path: nothing is applied
    r = _propose(pid, tid, estimate_hours=99.0)
    cid2 = r.json()["id"]
    assert _decide(pid, cid2, "rejected").status_code == 200
    with SessionLocal() as s:
        assert float(s.get(Task, tid).estimate_hours) == 20.0


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
