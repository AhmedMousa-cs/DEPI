"""Agent 3 as a job: the LLM step is replaced by a fake, queue/DB/audit/persistence are real.

Flow under test: the agent2 fake creates real tasks (save_draft_plan), the PM approves them,
then agent3.schedule computes a DRAFT schedule. Task dates change only through PM
approval of the reviewed plan version, and only PM-approved tasks get dated (locked_by_pm marks any
PM-decided row and does not block dating; drafts and rejected tasks stay undated).
"""
from app.agents.agent3_schedule import ScheduleExplanation
from app.agents import agent2_plan
from app.agents.agent4_testing import ScriptedChatModel, happy_path_script
from app.db.models import Project, ScheduleDraft, Task, TeamMember
from app.db.session import SessionLocal
from app.llm.runner import StructuredRun
from app.services.jobs import process_one
from helpers import assign_tl, audit_rows, client, make_project, pm_headers, tl_headers

from test_agent2_job import fake_plan, seed_approved, set_status


def run_job(pm, pid, job_type):
    r = client.post(f"/projects/{pid}/jobs", json={"type": job_type}, headers=pm)
    assert r.status_code == 202, r.text
    jid = r.json()["id"]
    # the hybrid agent3 flow auto-queues an agent4.review job, which may take the
    # next process_one slot — keep processing until THIS job leaves the queue
    from app.db.models import Job
    while True:
        with SessionLocal() as s:
            if s.get(Job, jid).status not in ("queued", "running"):
                break
        assert process_one() is True, "queue drained while the job is still queued"
    return client.get(f"/jobs/{jid}", headers=pm).json()


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


def review_plan(monkeypatch, pm, pid, version=1):
    model = ScriptedChatModel(script=happy_path_script(risks=[], verdict="ready"))
    monkeypatch.setattr("app.llm.langchain_model.get_langchain_model", lambda *a, **kw: model)
    assert process_one()  # Agent 3 queued the review for this plan version.
    response = client.get(f"/projects/{pid}/plan/versions/{version}", headers=pm)
    assert response.status_code == 200, response.text
    assert response.json()["snapshot"]["review"]
    return response.json()


def approve_plan(pm, pid, plan):
    acknowledgement = {}
    if plan["warnings"]:
        acknowledgement = {"acknowledge_warnings": True, "warnings_token": plan["warnings_token"]}
    return client.post(f"/projects/{pid}/plan/versions/{plan['version']}/approve", json=acknowledgement, headers=pm)


def test_schedule_job_creates_a_draft_and_touches_no_task_dates(monkeypatch):
    pm = pm_headers()
    pid = make_project(pm)["id"]
    tasks = plan_tasks(monkeypatch, pm, pid)
    approve_tasks(pm, pid, tasks)

    job, resp = schedule(monkeypatch, pm, pid)
    assert job["status"] == "done", job
    assert job["result_ref"].count("draft:") == 1
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


def test_only_the_reviewed_plan_can_write_gantt_dates_including_edited_tasks(monkeypatch):
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
    schedule_draft = resp.json()
    plan_url = f"/projects/{pid}/plan/versions/1"
    project_url = f"/projects/{pid}"
    before_plan = client.get(plan_url, headers=pm).json()
    before_project = client.get(project_url, headers=pm).json()
    before_tasks = client.get(f"{project_url}/tasks", headers=pm).json()
    for decision in ("approved", "rejected"):
        retired = client.post(
            f"{project_url}/scheduling/{schedule_draft['id']}/decision",
            json={"decision": decision, "comment": "legacy client"}, headers=pm,
        )
        assert retired.status_code == 410, retired.text
        assert retired.json()["detail"]["code"] == "schedule_decision_retired"
    assert client.post(f"{project_url}/scheduling/99999/decision", headers=pm).status_code == 410
    assert client.post(f"/projects/99999/scheduling/{schedule_draft['id']}/decision", headers=pm).status_code == 404
    assert client.get(plan_url, headers=pm).json() == before_plan
    assert client.get(project_url, headers=pm).json() == before_project
    assert client.get(f"{project_url}/scheduling", headers=pm).json() == schedule_draft
    assert client.get(f"{project_url}/tasks", headers=pm).json() == before_tasks
    assert all(t["start"] is None and t["end"] is None for t in before_tasks)
    assert not audit_rows("schedule.approved") and not audit_rows("schedule.rejected")

    plan = review_plan(monkeypatch, pm, pid)
    r = approve_plan(pm, pid, plan)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved" and r.json()["tasks_applied"] == 2

    now = {t["id"]: t for t in client.get(f"{project_url}/tasks", headers=pm).json()}
    scheduled = {str(t["id"]): t for t in schedule_draft["payload"]["schedule"]["tasks"]}
    for task_id in (free["id"], edited["id"]):
        actual = now[task_id]
        expected = scheduled[str(task_id)]
        assert actual["review_status"] == "approved" and actual["locked_by_pm"] is True
        assert (actual["start"], actual["end"], actual["is_critical"]) == (
            expected["start_date"], expected["end_date"], expected["is_critical"],
        )
    assert now[edited["id"]]["title"] == "Build the booking form (PM edited)"
    assert approve_plan(pm, pid, plan).status_code == 409
    assert client.post(f"{plan_url}/reject", json={"comment": "too late"}, headers=pm).status_code == 409
    assert len(audit_rows("plan.approved")) == 1


def test_approval_never_dates_draft_or_rejected_tasks(monkeypatch):
    pm = pm_headers()
    pid = make_project(pm)["id"]
    tasks = plan_tasks(monkeypatch, pm, pid)
    # one task approved, one rejected: only the approved one may receive dates.
    # The approved one must be the dependency-free task — the plan refuses to be
    # built from approved tasks that depend on unapproved work.
    r = client.post(f"/projects/{pid}/tasks/{tasks['Build the booking form']['id']}/decision", json={"decision": "approved"}, headers=pm)
    assert r.status_code == 200
    r = client.post(f"/projects/{pid}/tasks/{tasks['Test the booking flow']['id']}/decision", json={"decision": "rejected", "comment": "out of scope"}, headers=pm)
    assert r.status_code == 200
    approved_task = tasks["Build the booking form"]
    rejected_task = tasks["Test the booking flow"]
    with SessionLocal() as s:
        task = s.get(Task, approved_task["id"])
        extra = Task(project_id=pid, story_id=task.story_id, title="Draft follow-up", estimate_hours=2)
        s.add(extra)
        s.commit()
        draft_task_id = extra.id

    job, resp = schedule(monkeypatch, pm, pid)
    assert job["status"] == "done" and resp.status_code == 200
    r = approve_plan(pm, pid, review_plan(monkeypatch, pm, pid))
    assert r.status_code == 200, r.text
    assert r.json()["tasks_applied"] == 1

    now = {t["id"]: t for t in client.get(f"/projects/{pid}/tasks", headers=pm).json()}
    assert now[approved_task["id"]]["start"] is not None
    assert now[rejected_task["id"]]["review_status"] == "rejected"
    assert now[draft_task_id]["review_status"] == "draft"
    assert all(now[tid]["start"] is None and now[tid]["end"] is None for tid in (rejected_task["id"], draft_task_id))


def test_rejecting_the_reviewed_plan_leaves_schedule_dates_unchanged(monkeypatch):
    pm = pm_headers()
    pid = make_project(pm)["id"]
    tasks = plan_tasks(monkeypatch, pm, pid)
    approve_tasks(pm, pid, tasks)
    job, resp = schedule(monkeypatch, pm, pid)
    assert job["status"] == "done" and resp.status_code == 200
    plan = review_plan(monkeypatch, pm, pid)
    decision_url = f"/projects/{pid}/plan/versions/{plan['version']}/reject"

    assert client.post(decision_url, json={"comment": ""}, headers=pm).status_code == 422
    r = client.post(decision_url, json={"comment": "conflicts first"}, headers=pm)
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert approve_plan(pm, pid, plan).status_code == 409
    rows = client.get(f"/projects/{pid}/tasks", headers=pm).json()
    assert all(t["start"] is None and t["end"] is None for t in rows)


def test_job_refuses_when_the_plan_is_not_approved(monkeypatch):
    pm = pm_headers()
    pid = make_project(pm)["id"]
    plan_tasks(monkeypatch, pm, pid)  # tasks exist but none is PM-approved
    job, resp = schedule(monkeypatch, pm, pid, status="planning")
    assert job["status"] == "failed" and "approve the tasks" in job["error"]
    assert resp.status_code == 404 and "No schedule yet" in resp.json()["detail"]


def test_job_refuses_in_early_statuses_and_without_tasks(monkeypatch):
    pm = pm_headers()
    pid = make_project(pm)["id"]
    job, _ = schedule(monkeypatch, pm, pid, status="setup")
    assert job["status"] == "failed" and "can't be built" in job["error"]

    pid2 = make_project(pm)["id"]
    job2, _ = schedule(monkeypatch, pm, pid2, status="active")
    assert job2["status"] == "failed" and "No approved tasks" in job2["error"]


def test_new_draft_supersedes_the_previous_one(monkeypatch):
    pm = pm_headers()
    pid = make_project(pm)["id"]
    tasks = plan_tasks(monkeypatch, pm, pid)
    approve_tasks(pm, pid, tasks)
    _, first = schedule(monkeypatch, pm, pid)
    assert first.status_code == 200
    first_id = first.json()["id"]
    model = ScriptedChatModel(script=happy_path_script(risks=[], verdict="ready"))
    monkeypatch.setattr("app.llm.langchain_model.get_langchain_model", lambda *a, **kw: model)
    _, second = schedule(monkeypatch, pm, pid)
    assert second.status_code == 200
    assert second.json()["id"] != first_id

    with SessionLocal() as s:
        old = s.get(ScheduleDraft, first_id)
        assert old.review_status == "superseded"

    r = client.post(
        f"/projects/{pid}/scheduling/{first_id}/decision",
        json={"decision": "approved"},
        headers=pm,
    )
    assert r.status_code == 410
    with SessionLocal() as s:
        assert s.get(ScheduleDraft, first_id).review_status == "superseded"
        assert s.get(Project, pid).status == "plan_review"


def test_team_leader_cannot_see_or_decide_the_schedule(monkeypatch):
    pm = pm_headers()
    pid = make_project(pm)["id"]
    tasks = plan_tasks(monkeypatch, pm, pid)
    approve_tasks(pm, pid, tasks)
    job, draft = schedule(monkeypatch, pm, pid)
    assert job["status"] == "done"
    assign_tl(pid)
    tl = tl_headers()
    assert client.get(f"/projects/{pid}/scheduling", headers=tl).status_code == 403
    assert client.post(
        f"/projects/{pid}/scheduling/{draft.json()['id']}/decision", json={"decision": "approved"}, headers=tl,
    ).status_code == 403
