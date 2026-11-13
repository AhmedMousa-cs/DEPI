"""M5: Agent 2 storage, coverage and PM review API (deterministic, no LLM)."""
import inspect

import pytest
from sqlalchemy import select

from app.db.models import Decision, Requirement, Task, TaskDependency, UserStory
from app.db.session import SessionLocal
from app.schemas.planning import PlanningOutput
from app.services.planning import (
    PlanValidationError,
    get_approved_requirements,
    planning_coverage,
    save_draft_plan,
)
from helpers import assign_tl, audit_rows, client, make_project, pm_headers, tl_headers


@pytest.fixture(autouse=True)
def scores(monkeypatch):
    calls = []
    monkeypatch.setattr("app.api.planning.send_pm_score", lambda **kw: calls.append(kw) or True)
    return calls


def seed_requirements(project_id, statuses=("approved", "approved", "draft")) -> list[int]:
    with SessionLocal() as s:
        rows = [
            Requirement(
                project_id=project_id,
                type="functional",
                text=f"Requirement {i}",
                quote_verified=True,
                confidence=0.9,
                created_by_agent="agent1_extract",
                status=status,
            )
            for i, status in enumerate(statuses)
        ]
        s.add_all(rows)
        s.commit()
        return [r.id for r in rows]


def seed_plan(project_id, requirement_ids, *, cycle=False) -> tuple[list[int], list[int]]:
    with SessionLocal() as s:
        stories = [
            UserStory(
                project_id=project_id,
                requirement_id=rid,
                text=f"As a user I want feature {i}",
                acceptance_criteria=[f"Feature {i} works."],
                confidence=0.8,
                review_status="draft",
                created_by_agent="agent2_plan",
            )
            for i, rid in enumerate(requirement_ids)
        ]
        s.add_all(stories)
        s.flush()
        tasks = [
            Task(
                project_id=project_id,
                story_id=stories[0].id,
                title="Build the first feature",
                estimate_hours=8,
                confidence=0.8,
                review_status="draft",
                created_by_agent="agent2_plan",
            ),
            Task(
                project_id=project_id,
                story_id=stories[-1].id,
                title="Test the last feature",
                estimate_hours=4,
                confidence=0.8,
                review_status="draft",
                created_by_agent="agent2_plan",
            ),
        ]
        s.add_all(tasks)
        s.flush()
        if cycle:
            s.add_all(
                [
                    TaskDependency(task_id=tasks[0].id, depends_on_task_id=tasks[1].id),
                    TaskDependency(task_id=tasks[1].id, depends_on_task_id=tasks[0].id),
                ]
            )
        else:
            s.add(TaskDependency(task_id=tasks[1].id, depends_on_task_id=tasks[0].id))
        s.commit()
        return [x.id for x in stories], [x.id for x in tasks]


def url(pid, suffix=""):
    return f"/projects/{pid}{suffix}"


def decide(pm, pid, kind, item_id, body):
    r = client.post(url(pid, f"/{kind}/{item_id}/decision"), json=body, headers=pm)
    assert r.status_code == 200, r.text
    return r.json()


# ---------------- access control ----------------

def test_planning_endpoints_are_pm_only():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    req = seed_requirements(pid, ("approved",))[0]
    sids, tids = seed_plan(pid, [req])
    tl = tl_headers()
    assign_tl(pid)
    for suffix in ("/stories", "/tasks", "/planning/coverage", f"/stories/{sids[0]}", f"/tasks/{tids[0]}"):
        assert client.get(url(pid, suffix)).status_code == 401
        assert client.get(url(pid, suffix), headers=tl).status_code == 403
        assert client.get(url(pid, suffix), headers=pm).status_code == 200


def test_team_leader_cannot_decide_agent2_output():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    req = seed_requirements(pid, ("approved",))[0]
    sids, tids = seed_plan(pid, [req])
    tl = tl_headers()
    assign_tl(pid)
    assert client.post(url(pid, f"/stories/{sids[0]}/decision"), json={"decision": "approved"}, headers=tl).status_code == 403
    assert client.post(url(pid, f"/tasks/{tids[0]}/decision"), json={"decision": "approved"}, headers=tl).status_code == 403
    assert client.get(url(pid, f"/stories/{sids[0]}"), headers=pm).json()["review_status"] == "draft"
    assert audit_rows("story.approved") == []


def test_another_projects_story_is_404():
    pm = pm_headers()
    a = make_project(pm, name="A")["id"]
    b = make_project(pm, name="B")["id"]
    req = seed_requirements(a, ("approved",))[0]
    sids, tids = seed_plan(a, [req])
    assert client.get(url(b, f"/stories/{sids[0]}"), headers=pm).status_code == 404
    assert client.post(url(b, f"/stories/{sids[0]}/decision"), json={"decision": "approved"}, headers=pm).status_code == 404
    assert client.get(url(b, f"/tasks/{tids[0]}"), headers=pm).status_code == 404


# ---------------- PM decisions ----------------

def test_approve_story_locks_and_records(scores):
    pm = pm_headers()
    pid = make_project(pm)["id"]
    req = seed_requirements(pid, ("approved",))[0]
    sids, _ = seed_plan(pid, [req])
    sid = sids[0]
    data = decide(pm, pid, "stories", sid, {"decision": "approved"})
    assert data["review_status"] == "approved" and data["locked_by_pm"] is True
    assert data["reviewed_by"] is not None and data["reviewed_at"] is not None
    with SessionLocal() as s:
        decisions = list(s.scalars(select(Decision)))
    assert [(d.subject_type, d.subject_id, d.decision) for d in decisions] == [("story", sid, "approved")]
    row = audit_rows("story.approved")[0]
    assert row.before["review_status"] == "draft" and row.after["review_status"] == "approved"
    assert scores and scores[0]["subject_type"] == "story" and scores[0]["subject_id"] == sid


def test_modify_story_applies_edits(scores):
    pm = pm_headers()
    pid = make_project(pm)["id"]
    req = seed_requirements(pid, ("approved",))[0]
    sids, _ = seed_plan(pid, [req])
    body = {
        "decision": "modified",
        "comment": "clearer",
        "text": "As a patient I can book an appointment in two taps",
        "acceptance_criteria": ["Booking completes in two taps."],
    }
    data = decide(pm, pid, "stories", sids[0], body)
    assert data["text"].startswith("As a patient") and data["acceptance_criteria"] == ["Booking completes in two taps."]
    assert audit_rows("story.modified")[0].before["text"] != data["text"]


def test_reject_and_modify_task(scores):
    pm = pm_headers()
    pid = make_project(pm)["id"]
    req = seed_requirements(pid, ("approved",))[0]
    _, tids = seed_plan(pid, [req])
    tid = tids[0]
    assert client.post(url(pid, f"/tasks/{tid}/decision"), json={"decision": "rejected"}, headers=pm).status_code == 422
    data = decide(pm, pid, "tasks", tid, {"decision": "rejected", "comment": "duplicate"})
    assert data["review_status"] == "rejected" and data["pm_comment"] == "duplicate"

    data = decide(pm, pid, "tasks", tid, {"decision": "modified", "comment": "too big", "estimate_hours": 12})
    assert data["estimate_hours"] == 12 and data["review_status"] == "approved"


@pytest.mark.parametrize(
    "body",
    [
        {"decision": "modified", "comment": "x"},  # nothing to modify
        {"decision": "modified", "text": "A different story text"},  # no comment
        {"decision": "approved", "text": "sneaky edit"},
        {"decision": "approved", "decided_by": 1},
        {"decision": "bogus"},
    ],
)
def test_invalid_story_decisions_422(body):
    pm = pm_headers()
    pid = make_project(pm)["id"]
    req = seed_requirements(pid, ("approved",))[0]
    sids, _ = seed_plan(pid, [req])
    assert client.post(url(pid, f"/stories/{sids[0]}/decision"), json=body, headers=pm).status_code == 422
    assert client.get(url(pid, f"/stories/{sids[0]}"), headers=pm).json()["review_status"] == "draft"


@pytest.mark.parametrize(
    "body",
    [
        {"decision": "modified", "comment": "x"},
        {"decision": "modified", "estimate_hours": 12},
        {"decision": "approved", "estimate_hours": 12},
        {"decision": "modified", "comment": "bad", "estimate_hours": 0},
        {"decision": "bogus"},
    ],
)
def test_invalid_task_decisions_422(body):
    pm = pm_headers()
    pid = make_project(pm)["id"]
    req = seed_requirements(pid, ("approved",))[0]
    _, tids = seed_plan(pid, [req])
    assert client.post(url(pid, f"/tasks/{tids[0]}/decision"), json=body, headers=pm).status_code == 422


def test_task_lists_dependencies():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    req = seed_requirements(pid, ("approved",))[0]
    _, tids = seed_plan(pid, [req])
    rows = client.get(url(pid, "/tasks"), headers=pm).json()
    second = next(t for t in rows if t["id"] == tids[1])
    assert second["depends_on"] == [tids[0]]


def test_filters():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    req = seed_requirements(pid, ("approved",))[0]
    sids, _ = seed_plan(pid, [req])
    decide(pm, pid, "stories", sids[0], {"decision": "approved"})
    assert len(client.get(url(pid, "/stories?review_status=draft"), headers=pm).json()) == 0
    assert len(client.get(url(pid, "/stories?review_status=approved"), headers=pm).json()) == 1
    assert len(client.get(url(pid, f"/stories?requirement_id={req}"), headers=pm).json()) == 1
    assert client.get(url(pid, "/stories?review_status=weird"), headers=pm).status_code == 422


# ---------------- coverage ----------------

def test_coverage_reports_gaps_and_cycles():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    approved = seed_requirements(pid, ("approved", "approved", "approved"))
    seed_plan(pid, approved[:2], cycle=True)
    data = client.get(url(pid, "/planning/coverage"), headers=pm).json()
    assert data["approved_requirements"] == 3
    assert data["requirements_without_story"] == [approved[2]]
    assert data["stories"] == 2 and data["tasks"] == 2
    assert len(data["dependency_cycles"]) == 1
    assert data["unreviewed_stories"] == 2 and data["unreviewed_tasks"] == 2
    assert data["clean"] is False


def test_coverage_is_clean_after_review():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    req = seed_requirements(pid, ("approved",))[0]
    sids, tids = seed_plan(pid, [req])
    for sid in sids:
        decide(pm, pid, "stories", sid, {"decision": "approved"})
    for tid in tids:
        decide(pm, pid, "tasks", tid, {"decision": "approved"})
    data = client.get(url(pid, "/planning/coverage"), headers=pm).json()
    assert data["requirements_without_story"] == []
    assert data["stories_without_tasks"] == []
    assert data["dependency_cycles"] == []
    assert data["total_estimate_hours"] == 12
    assert data["clean"] is True


# ---------------- the agent can only write drafts, and only approved requirements ----------------

def _output(requirement_ids, *, text="As a patient I can book online", task_title="Build booking API"):
    return PlanningOutput(
        stories=[
            {
                "ref": f"S{i}",
                "requirement_id": rid,
                "text": f"{text} {i}",
                "acceptance_criteria": ["The feature is usable end to end."],
                "confidence": 0.9,
            }
            for i, rid in enumerate(requirement_ids, start=1)
        ],
        tasks=[
            {
                "ref": f"T{i}",
                "story_ref": f"S{i}",
                "title": f"{task_title} {i}",
                "estimate_hours": 8,
                "depends_on": [f"T{i - 1}"] if i > 1 else [],
                "confidence": 0.8,
            }
            for i in range(1, len(requirement_ids) + 1)
        ],
    )


def test_save_draft_plan_has_no_review_status_parameter():
    assert "review_status" not in inspect.signature(save_draft_plan).parameters


def test_agent2_only_sees_approved_requirements():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    ids = seed_requirements(pid, ("approved", "draft", "rejected"))
    with SessionLocal() as s:
        visible = [r["id"] for r in get_approved_requirements(s, pid)]
    assert visible == [ids[0]]


def test_save_draft_plan_rejects_unapproved_requirement():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    ids = seed_requirements(pid, ("approved", "draft", "rejected"))
    with SessionLocal() as s:
        with pytest.raises(PlanValidationError, match="not approved"):
            save_draft_plan(s, project_id=pid, output=_output([ids[1]]))
        assert s.scalars(select(UserStory)).all() == []


def test_save_draft_plan_creates_drafts_and_dependencies():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    approved = seed_requirements(pid, ("approved", "approved", "draft"))[:2]
    with SessionLocal() as s:
        saved = save_draft_plan(s, project_id=pid, output=_output(approved), run_id=None)
        s.commit()
        assert len(saved.stories) == 2 and len(saved.tasks) == 2 and saved.dependencies == 1
        assert all(x.review_status == "draft" and x.locked_by_pm is False for x in saved.stories)
        assert all(x.review_status == "draft" and x.status == "todo" for x in saved.tasks)
        edges = s.execute(select(TaskDependency.task_id, TaskDependency.depends_on_task_id)).all()
        assert len(edges) == 1
    assert len(audit_rows("planning.drafts_saved")) == 1


def test_rerun_never_overwrites_pm_decisions():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    approved = seed_requirements(pid, ("approved", "approved", "draft"))[:2]
    with SessionLocal() as s:
        save_draft_plan(s, project_id=pid, output=_output(approved))
        s.commit()
    stories = client.get(url(pid, "/stories"), headers=pm).json()
    tasks = client.get(url(pid, "/tasks"), headers=pm).json()
    kept_story, kept_task = stories[0]["id"], tasks[0]["id"]
    decide(pm, pid, "stories", kept_story, {"decision": "approved"})
    decide(pm, pid, "tasks", kept_task, {"decision": "modified", "comment": "split later", "title": "PM edited task title"})

    with SessionLocal() as s:  # Agent 2 runs again with the same output
        saved = save_draft_plan(s, project_id=pid, output=_output(approved))
        s.commit()
        assert saved.skipped_protected == 2

    after_story = client.get(url(pid, f"/stories/{kept_story}"), headers=pm).json()
    after_task = client.get(url(pid, f"/tasks/{kept_task}"), headers=pm).json()
    assert after_story["review_status"] == "approved"
    assert after_task["title"] == "PM edited task title"
    assert len(client.get(url(pid, "/stories"), headers=pm).json()) == 2
    assert len(client.get(url(pid, "/tasks"), headers=pm).json()) == 2
