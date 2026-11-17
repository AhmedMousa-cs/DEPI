"""Plan-level review: bulk story/task decisions and the story-reject cascade to child tasks."""
import pytest
from sqlalchemy import select

from app.db.models import Decision, Task, UserStory
from app.db.session import SessionLocal
from helpers import audit_rows, client, make_project, pm_headers, tl_headers

pytestmark = pytest.mark.usefixtures("scores")


@pytest.fixture(autouse=True)
def scores(monkeypatch):
    """Never talk to Langfuse from tests; record what would have been sent."""
    monkeypatch.setattr("app.api.planning.send_pm_score", lambda **kw: True)


def seed_plan_with_children(project_id: int) -> list[int]:
    """Two draft stories (each backed by an approved requirement); story 1 has 2 draft child
    tasks, story 2 has 1 approved + 1 draft."""
    from test_planning_api import seed_requirements

    rids = seed_requirements(project_id, statuses=("approved", "approved"))
    with SessionLocal() as s:
        stories = [
            UserStory(
                project_id=project_id, requirement_id=rids[i], text=f"As a user I want feature {i + 1}",
                acceptance_criteria=["It works."], confidence=0.8, review_status="draft",
                created_by_agent="agent2_plan",
            )
            for i in (0, 1)
        ]
        s.add_all(stories)
        s.flush()
        tasks = [
            Task(project_id=project_id, story_id=stories[0].id, title="Build feature one step A",
                 estimate_hours=6, confidence=0.8, review_status="draft", created_by_agent="agent2_plan"),
            Task(project_id=project_id, story_id=stories[0].id, title="Build feature one step B",
                 estimate_hours=4, confidence=0.8, review_status="draft", created_by_agent="agent2_plan"),
            Task(project_id=project_id, story_id=stories[1].id, title="Build feature two step A",
                 estimate_hours=5, confidence=0.8, review_status="approved", locked_by_pm=True,
                 created_by_agent="agent2_plan"),
            Task(project_id=project_id, story_id=stories[1].id, title="Build feature two step B",
                 estimate_hours=3, confidence=0.8, review_status="draft", created_by_agent="agent2_plan"),
        ]
        s.add_all(tasks)
        s.commit()
        return [st.id for st in stories]


def test_rejecting_a_story_cascades_to_its_draft_tasks_only():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    story_ids = seed_plan_with_children(pid)

    r = client.post(f"/projects/{pid}/stories/{story_ids[0]}/decision",
                    json={"decision": "rejected", "comment": "out of scope"}, headers=pm)
    assert r.status_code == 200 and r.json()["review_status"] == "rejected"

    with SessionLocal() as s:
        tasks = {t.title: t for t in s.scalars(select(Task).where(Task.story_id == story_ids[0]))}
        assert all(t.review_status == "rejected" and t.locked_by_pm for t in tasks.values())
        untouched = list(s.scalars(select(Task).where(Task.story_id == story_ids[1])))
        by_status = {t.title: t.review_status for t in untouched}
        assert by_status["Build feature two step A"] == "approved"  # decided work is never touched
        assert by_status["Build feature two step B"] == "draft"
        decisions = list(s.scalars(select(Decision).where(Decision.project_id == pid)))
        assert len(decisions) == 3  # the story + its two child tasks
    cascaded = [a for a in audit_rows("task.rejected") if a.after.get("cascade")]
    assert len(cascaded) == 2 and all(a.after["cascade"] == f"story:{story_ids[0]}" for a in cascaded)


def test_bulk_story_decision_approves_many_in_one_call():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    story_ids = seed_plan_with_children(pid)

    r = client.post(f"/projects/{pid}/stories/decision", json={"ids": story_ids, "decision": "approved"}, headers=pm)
    assert r.status_code == 200, r.text
    assert [row["id"] for row in r.json()] == story_ids
    assert all(row["review_status"] == "approved" for row in r.json())
    assert len([a for a in audit_rows("story.approved") if a.after.get("bulk") is True]) == 2


def test_bulk_reject_cascades_and_needs_a_comment():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    story_ids = seed_plan_with_children(pid)

    r = client.post(f"/projects/{pid}/stories/decision", json={"ids": story_ids, "decision": "rejected", "comment": ""}, headers=pm)
    assert r.status_code == 422

    r = client.post(f"/projects/{pid}/stories/decision", json={"ids": story_ids, "decision": "rejected", "comment": "cut"}, headers=pm)
    assert r.status_code == 200 and all(row["review_status"] == "rejected" for row in r.json())
    with SessionLocal() as s:
        tasks = list(s.scalars(select(Task).where(Task.project_id == pid)))
        # every DRAFT child task went with its story; the pre-approved one is never touched
        assert all(t.review_status == "rejected" for t in tasks if not t.locked_by_pm or t.pm_comment)
        assert sum(t.review_status == "approved" for t in tasks) == 1


def test_bulk_task_decision_and_guards():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    seed_plan_with_children(pid)
    with SessionLocal() as s:
        task_ids = [t.id for t in s.scalars(select(Task).where(Task.project_id == pid)).all()]

    r = client.post(f"/projects/{pid}/tasks/decision", json={"ids": task_ids[:2], "decision": "approved"}, headers=pm)
    assert r.status_code == 200 and all(row["review_status"] == "approved" for row in r.json())

    # unknown / foreign ids are a 404 with nothing written
    r = client.post(f"/projects/{pid}/tasks/decision", json={"ids": task_ids[:1] + [9999], "decision": "approved"}, headers=pm)
    assert r.status_code == 404 and "9999" in r.json()["detail"]
    with SessionLocal() as s:
        fresh = list(s.scalars(select(Task).where(Task.project_id == pid)))
        # 2 approved by the successful batch + 1 pre-approved in the seed; the failed batch changed nothing
        assert sum(t.review_status == "approved" for t in fresh) == 3

    other_pid = make_project(pm)["id"]
    seed_plan_with_children(other_pid)
    with SessionLocal() as s:
        foreign = s.scalars(select(Task).where(Task.project_id == other_pid)).first().id
    r = client.post(f"/projects/{pid}/tasks/decision", json={"ids": [foreign], "decision": "approved"}, headers=pm)
    assert r.status_code == 404

    # the leader cannot bulk-decide
    r = client.post(f"/projects/{pid}/tasks/decision", json={"ids": task_ids, "decision": "approved"}, headers=tl_headers())
    assert r.status_code == 403
