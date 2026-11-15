"""Agent 3 as a job: the LLM step is replaced by a fake, queue/DB/audit/persistence are real.

Flow under test: the agent2 fake creates real tasks (save_draft_plan), the PM approves them,
then agent3.schedule computes a DRAFT schedule. Draft-only: task dates change only through PM
approval of the schedule, and only PM-approved tasks get dated (locked_by_pm marks any
PM-decided row and does not block dating; drafts and rejected tasks stay undated).
"""
from sqlalchemy import select

from app.agents.agent3_schedule import ScheduleExplanation
from app.agents import agent2_plan
from app.db.models import ScheduleDraft, TeamMember
from app.db.session import SessionLocal
from app.llm.runner import StructuredRun
from app.services.jobs import process_one
from helpers import audit_rows, client, make_project, pm_headers, tl_headers

from test_agent2_job import fake_plan, seed_approved, set_status


def run_job(pm, pid, job_type):
    r = client.post(f"/projects/{pid}/jobs", json={"type": job_type}, headers=pm)
    assert r.status_code == 202, r.text
    assert process_one() is True
    return client.get(f"/jobs/{r.json()['id']}", headers=pm).json()


def fake_explanation_run():
    """A run_structured replacement that answers the explanation call only."""
    def fake(**kwargs):
        parsed = ScheduleExplanation(
            summary="fake summary", critical_path="fake chain", conflicts=[], estimates=[], source="llm"
        )
        return StructuredRun(
            parsed=parsed, run_id=77, usage=None, cost_usd=0, pricing_configured=False, attempts=1, trace_id=None
        )

    return fake


def seed_member(pid: int, name: str, capacity: float = 40) -> int:
    with SessionLocal() as s:
        member = TeamMember(project_id=pid, name=name, role_title="dev", capacity_hours_per_week=capacity)
        s.add(member)
        s.commit()
        return member.id


def plan_tasks(monkeypatch, pm, pid):
    """Run the agent2 fake so the project has two real draft tasks (T2 depends on T1)."""
    monkeypatch.setattr(agent2_plan, "generate_plan", fake_plan())
    seed_approved(pid)
    set_status(pid, "planning")
    job = run_job(pm, pid, "agent2.plan")
    assert job["status"] == "done", job
    return {t["title"]: t for t in client.get(f"/projects/{pid}/tasks", headers=pm).json()}


def approve_tasks(pm, pid, tasks, titles=None):
    """PM-approve the plan tasks (the real gate before scheduling)."""
    chosen = tasks.items() if titles is None else [(t, tasks[t]) for t in titles]
    for _, task in chosen:
        r = client.post(f"/projects/{pid}/tasks/{task['id']}/decision", json={"decision": "approved"}, headers=pm)
        assert r.status_code == 200, r.text


def schedule(monkeypatch, pm, pid, status="planning"):
    """Run agent3.schedule and return the job + the draft as the API sees it."""
    monkeypatch.setattr("app.services.agent3_job.run_structured", fake_explanation_run())
    set_status(pid, status)
    job = run_job(pm, pid, "agent3.schedule")
    return job, client.get(f"/projects/{pid}/scheduling", headers=pm)


def test_schedule_job_creates_a_draft_and_touches_no_task_dates(monkeypatch):
    pm = pm_headers()
    pid = make_project(pm)["id"]
    tasks = plan_tasks(monkeypatch, pm, pid)
    approve_tasks(pm, pid, tasks)

    job, resp = schedule(monkeypatch, pm, pid)
    assert job["status"] == "done", job
    assert job["result_ref"].startswith("draft:")
    assert resp.status_code == 200
    draft = resp.json()
    assert draft["review_status"] == "draft" and draft["conflicts_count"] == 0
    assert draft["created_by_agent"] == "agent3_schedule"
    assert draft["run_ids"] == [77]
    scheduled = draft["payload"]["schedule"]["tasks"]
    assert len(scheduled) == 2
    first = next(t for t in scheduled if t["title"] == "Build the booking form")
    second = next(t for t in scheduled if t["title"] == "Test the booking flow")
    # CPM: the dependent task starts after its dependency finishes
    assert second["start_date"] >= first["end_date"]
    assert second["total_float"] >= 0 and first["is_critical"] is True
    assert draft["payload"]["schedule"]["critical_path"]

    # draft-only: the agent never wrote task dates
    rows = client.get(f"/projects/{pid}/tasks", headers=pm).json()
    assert all(t["start"] is None and t["end"] is None for t in rows)

    after = audit_rows("agent3.scheduling_completed")[0].after
    assert after["draft_id"] == draft["id"] and after["tasks_scheduled"] == 2
    assert after["critical_path"] == list(draft["payload"]["schedule"]["critical_path"])


def test_pm_approval_writes_dates_including_edited_tasks(monkeypatch):
    pm = pm_headers()
    pid = make_project(pm)["id"]
    tasks = plan_tasks(monkeypatch, pm, pid)
    approve_tasks(pm, pid, tasks)
    free = tasks["Test the booking flow"]
    edited = tasks["Build the booking form"]
    # the PM edits a task afterwards: its title changes but the task stays approved
    r = client.post(
        f"/projects/{pid}/tasks/{edited['id']}/decision",
        json={"decision": "modified", "title": "Build the booking form (PM edited)", "comment": "clearer"},
        headers=pm,
    )
    assert r.status_code == 200, r.text

    job, resp = schedule(monkeypatch, pm, pid)
    assert job["status"] == "done" and resp.status_code == 200
    draft_id = resp.json()["id"]

    r = client.post(
        f"/projects/{pid}/scheduling/{draft_id}/decision",
        json={"decision": "approved", "comment": "dates look fine"},
        headers=pm,
    )
    assert r.status_code == 200, r.text
    assert r.json()["review_status"] == "approved"

    now = client.get(f"/projects/{pid}/tasks", headers=pm).json()
    dated = next(t for t in now if t["id"] == free["id"])
    also_dated = next(t for t in now if t["id"] == edited["id"])
    assert dated["start"] is not None and dated["end"] is not None
    assert dated["end"] >= dated["start"]
    # a PM-edited task keeps its own decision but still gets its dates from the approved schedule
    assert also_dated["start"] is not None and also_dated["locked_by_pm"] is True

    after = audit_rows("schedule.approved")[0].after
    assert after["tasks_dated"] == 2

    # a decided draft cannot be decided again
    r2 = client.post(
        f"/projects/{pid}/scheduling/{draft_id}/decision",
        json={"decision": "approved"},
        headers=pm,
    )
    assert r2.status_code == 409


def test_approval_never_dates_draft_or_rejected_tasks(monkeypatch):
    pm = pm_headers()
    pid = make_project(pm)["id"]
    tasks = plan_tasks(monkeypatch, pm, pid)
    # one task approved, one rejected: only the approved one may receive dates
    r = client.post(f"/projects/{pid}/tasks/{tasks['Test the booking flow']['id']}/decision", json={"decision": "approved"}, headers=pm)
    assert r.status_code == 200
    r = client.post(f"/projects/{pid}/tasks/{tasks['Build the booking form']['id']}/decision", json={"decision": "rejected", "comment": "out of scope"}, headers=pm)
    assert r.status_code == 200
    approved_task = tasks["Test the booking flow"]
    rejected_task = tasks["Build the booking form"]

    job, resp = schedule(monkeypatch, pm, pid)
    assert job["status"] == "done" and resp.status_code == 200
    draft_id = resp.json()["id"]
    r = client.post(
        f"/projects/{pid}/scheduling/{draft_id}/decision", json={"decision": "approved"}, headers=pm
    )
    assert r.status_code == 200

    now = {t["id"]: t for t in client.get(f"/projects/{pid}/tasks", headers=pm).json()}
    assert now[approved_task["id"]]["start"] is not None
    assert now[rejected_task["id"]]["start"] is None  # rejected work never gets dates


def test_reject_requires_a_comment(monkeypatch):
    pm = pm_headers()
    pid = make_project(pm)["id"]
    tasks = plan_tasks(monkeypatch, pm, pid)
    approve_tasks(pm, pid, tasks)
    job, resp = schedule(monkeypatch, pm, pid)
    assert job["status"] == "done" and resp.status_code == 200
    draft_id = resp.json()["id"]

    r = client.post(
        f"/projects/{pid}/scheduling/{draft_id}/decision",
        json={"decision": "rejected", "comment": ""},
        headers=pm,
    )
    assert r.status_code == 422  # validation: a comment is required when rejecting
    r = client.post(
        f"/projects/{pid}/scheduling/{draft_id}/decision",
        json={"decision": "rejected", "comment": "conflicts first"},
        headers=pm,
    )
    assert r.status_code == 200 and r.json()["review_status"] == "rejected"
    # rejection writes no dates
    rows = client.get(f"/projects/{pid}/tasks", headers=pm).json()
    assert all(t["start"] is None for t in rows)


def test_job_refuses_when_the_plan_is_not_approved(monkeypatch):
    pm = pm_headers()
    pid = make_project(pm)["id"]
    plan_tasks(monkeypatch, pm, pid)  # tasks exist but none is PM-approved
    job, resp = schedule(monkeypatch, pm, pid, status="planning")
    assert job["status"] == "failed" and "approve the plan tasks" in job["error"]
    assert resp.status_code == 404 and "No schedule yet" in resp.json()["detail"]


def test_job_refuses_in_early_statuses_and_without_tasks(monkeypatch):
    pm = pm_headers()
    pid = make_project(pm)["id"]
    job, _ = schedule(monkeypatch, pm, pid, status="setup")
    assert job["status"] == "failed" and "cannot schedule" in job["error"]

    pid2 = make_project(pm)["id"]
    job2, _ = schedule(monkeypatch, pm, pid2, status="active")
    assert job2["status"] == "failed" and "No tasks to schedule" in job2["error"]


def test_new_draft_supersedes_the_previous_one(monkeypatch):
    pm = pm_headers()
    pid = make_project(pm)["id"]
    tasks = plan_tasks(monkeypatch, pm, pid)
    approve_tasks(pm, pid, tasks)
    first_job, first = schedule(monkeypatch, pm, pid)
    assert first.status_code == 200
    first_id = first.json()["id"]
    second_job, second = schedule(monkeypatch, pm, pid)
    assert second.status_code == 200
    assert second.json()["id"] != first_id

    with SessionLocal() as s:
        old = s.get(ScheduleDraft, first_id)
        assert old.review_status == "superseded"

    # deciding on the superseded draft is refused
    r = client.post(
        f"/projects/{pid}/scheduling/{first_id}/decision",
        json={"decision": "approved"},
        headers=pm,
    )
    assert r.status_code == 409


def test_team_leader_cannot_see_or_decide_the_schedule(monkeypatch):
    pm = pm_headers()
    pid = make_project(pm)["id"]
    tasks = plan_tasks(monkeypatch, pm, pid)
    approve_tasks(pm, pid, tasks)
    job, _ = schedule(monkeypatch, pm, pid)
    assert job["status"] == "done"
    assert client.get(f"/projects/{pid}/scheduling", headers=tl_headers()).status_code == 403
