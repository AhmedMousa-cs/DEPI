"""M9 delivery tracking: the PM records execution progress on approved tasks.

todo -> 0%, done -> 100%, in_progress keeps/sets the given percent; draft tasks are
rejected, the endpoint is PM-only, every change is audited, and the portfolio
command center reflects the progress (hours-weighted) without extra work.
"""
from datetime import date, timedelta

import pytest

from app.db.models import Task
from app.db.session import SessionLocal
from helpers import audit_rows, client, make_project, pm_headers, tl_headers

TODAY = date.today()


def seed_approved_task(project_id, *, estimate=10, start=None, end=None) -> int:
    from test_portfolio_api import _seed_story_task

    return _seed_story_task(project_id, "Delivery task", estimate=estimate,
                            start=start, end=end)


def test_pm_records_progress_in_progress():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    tid = seed_approved_task(pid)

    r = client.patch(f"/projects/{pid}/tasks/{tid}/progress",
                     json={"status": "in_progress", "progress_pct": 40}, headers=pm)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "in_progress" and r.json()["progress_pct"] == 40

    rows = [a for a in audit_rows("task.progress") if a.entity_id and int(a.entity_id) == tid]
    assert rows and rows[-1].before == {"status": "todo", "progress_pct": 0}
    assert rows[-1].after == {"status": "in_progress", "progress_pct": 40}


def test_done_forces_100_and_todo_resets_to_0():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    tid = seed_approved_task(pid)

    r = client.patch(f"/projects/{pid}/tasks/{tid}/progress",
                     json={"status": "done", "progress_pct": 55}, headers=pm)
    assert r.status_code == 200 and r.json()["progress_pct"] == 100  # done always 100

    r = client.patch(f"/projects/{pid}/tasks/{tid}/progress",
                     json={"status": "todo"}, headers=pm)
    assert r.status_code == 200 and r.json()["progress_pct"] == 0  # todo always 0

    # in_progress without an explicit percent keeps the last value
    client.patch(f"/projects/{pid}/tasks/{tid}/progress",
                 json={"status": "in_progress", "progress_pct": 30}, headers=pm)
    r = client.patch(f"/projects/{pid}/tasks/{tid}/progress",
                     json={"status": "in_progress"}, headers=pm)
    assert r.status_code == 200 and r.json()["progress_pct"] == 30


def test_draft_task_and_missing_task_are_rejected():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    from test_portfolio_api import _seed_story_task

    draft_id = _seed_story_task(pid, "Draft work", estimate=5, review="draft")
    assert client.patch(f"/projects/{pid}/tasks/{draft_id}/progress",
                        json={"status": "done"}, headers=pm).status_code == 409
    assert client.patch(f"/projects/{pid}/tasks/999999/progress",
                        json={"status": "done"}, headers=pm).status_code == 404


def test_team_leader_cannot_track_delivery():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    tid = seed_approved_task(pid)
    r = client.patch(f"/projects/{pid}/tasks/{tid}/progress",
                     json={"status": "done"}, headers=tl_headers())
    assert r.status_code == 403


def test_out_of_range_progress_is_422():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    tid = seed_approved_task(pid)
    r = client.patch(f"/projects/{pid}/tasks/{tid}/progress",
                     json={"status": "in_progress", "progress_pct": 150}, headers=pm)
    assert r.status_code == 422


def test_portfolio_overview_reflects_delivery_progress():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    seed_approved_task(pid, estimate=10)
    seed_approved_task(pid, estimate=10)

    client.patch(f"/projects/{pid}/tasks/{list(_all_task_ids(pid))[0]}/progress",
                 json={"status": "done"}, headers=pm)

    card = next(c for c in client.get("/portfolio/overview", headers=pm).json()["projects"]
                if c["project_id"] == pid)
    assert card["progress_pct"] == 50  # one of two 10h tasks done
    assert card["tasks"]["done"] == 1


def _all_task_ids(project_id):
    with SessionLocal() as s:
        return list(s.scalars(__import__("sqlalchemy").select(Task.id).where(Task.project_id == project_id)))
