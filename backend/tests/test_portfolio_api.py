"""M7 PM command center: portfolio overview/resources/risks are deterministic; the
PM assistant chat is the only LLM path (patched here) and is sanitised by code."""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.db.models import AuditLog, Requirement, Task, TeamMember, UserStory
from app.db.session import SessionLocal
from app.llm.runner import StructuredRun
from app.schemas.portfolio import PortfolioChatOutput
from helpers import assign_tl, audit_rows, client, make_project, pm_headers, tl_headers

TODAY = date.today()


def _seed_story_task(project_id, title, *, estimate, status="todo", start=None, end=None,
                     progress=0, review="approved", member_id=None, critical=False):
    with SessionLocal() as s:
        req = Requirement(project_id=project_id, type="functional", text=f"req for {title}",
                          quote_verified=True, confidence=0.9, status="approved",
                          created_by_agent="agent1_extract")
        s.add(req)
        s.flush()
        story = UserStory(project_id=project_id, requirement_id=req.id, text=f"story {title}",
                          acceptance_criteria=["ok"], confidence=0.8, review_status="approved",
                          created_by_agent="agent2_plan")
        s.add(story)
        s.flush()
        task = Task(project_id=project_id, story_id=story.id, title=title,
                    estimate_hours=estimate, status=status, progress_pct=progress,
                    start=start, end=end, review_status=review, locked_by_pm=True,
                    assignee_member_id=member_id, is_critical=critical,
                    created_by_agent="agent2_plan")
        s.add(task)
        s.commit()
        return task.id


def _seed_member(project_id, name, *, role="Dev", capacity=40.0, skills=None):
    with SessionLocal() as s:
        m = TeamMember(project_id=project_id, name=name, role_title=role,
                       skills=skills or [], capacity_hours_per_week=capacity, is_leader=False)
        s.add(m)
        s.commit()
        return m.id


def _seed_draft(project_id, *, days_old=0, status="draft"):
    with SessionLocal() as s:
        r = Requirement(project_id=project_id, type="functional", text=f"draft req {days_old}",
                        quote_verified=True, confidence=0.9, status=status,
                        created_at=datetime.now(timezone.utc) - timedelta(days=days_old),
                        created_by_agent="agent1_extract")
        s.add(r)
        s.commit()
        return r.id


# --------------------------------------------------------------------------- overview


def test_overview_requires_login():
    assert client.get("/portfolio/overview").status_code == 401


def test_overview_is_scoped_for_leaders():
    pm = pm_headers()
    mine = make_project(pm)["id"]
    other = make_project(pm, name="Other project")["id"]
    assign_tl(mine)

    body = client.get("/portfolio/overview", headers=tl_headers()).json()
    ids = {c["project_id"] for c in body["projects"]}
    assert ids == {mine} and other not in ids

    pm_ids = {c["project_id"] for c in client.get("/portfolio/overview", headers=pm).json()["projects"]}
    assert {mine, other} <= pm_ids


def test_overdue_approved_task_turns_the_project_red():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    _seed_story_task(pid, "Overdue work", estimate=8, start=TODAY - timedelta(days=10),
                     end=TODAY - timedelta(days=1), progress=20)

    card = next(c for c in client.get("/portfolio/overview", headers=pm).json()["projects"]
                if c["project_id"] == pid)
    assert card["health"] == "red"
    assert card["overdue_tasks"] == 1
    assert any("past their end date" in r for r in card["health_reasons"])
    assert card["progress_pct"] == 20  # hours-weighted: 8h * 20%


def test_progress_is_hours_weighted_over_approved_tasks():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    _seed_story_task(pid, "Done work", estimate=10, status="done", progress=100)
    _seed_story_task(pid, "Started work", estimate=10, progress=40)

    card = next(c for c in client.get("/portfolio/overview", headers=pm).json()["projects"]
                if c["project_id"] == pid)
    assert card["progress_pct"] == 70  # (10*100 + 10*40) / 20
    assert card["tasks"]["approved"] == 2 and card["tasks"]["done"] == 1


def test_pending_drafts_turn_the_project_yellow():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    _seed_draft(pid)

    card = next(c for c in client.get("/portfolio/overview", headers=pm).json()["projects"]
                if c["project_id"] == pid)
    assert card["health"] == "yellow"
    assert any("draft(s) waiting" in r for r in card["health_reasons"])


def test_next_deadline_prefers_the_earliest_future_task_end():
    pm = pm_headers()
    pid = make_project(pm, end_date="2026-12-31")["id"]
    _seed_story_task(pid, "Soon", estimate=4, start=TODAY, end=TODAY + timedelta(days=2))
    _seed_story_task(pid, "Later", estimate=4, start=TODAY, end=TODAY + timedelta(days=9))

    card = next(c for c in client.get("/portfolio/overview", headers=pm).json()["projects"]
                if c["project_id"] == pid)
    assert card["next_deadline"] == (TODAY + timedelta(days=2)).isoformat()


# --------------------------------------------------------------------------- resources


def test_resources_utilization_and_cross_project_conflict():
    pm = pm_headers()
    p1 = make_project(pm, name="Alpha")["id"]
    p2 = make_project(pm, name="Beta")["id"]
    over = _seed_member(p1, "Omar Hassan", capacity=10.0)
    calm = _seed_member(p2, "Lina Adel", capacity=40.0)

    # Omar: 40h of approved dated work inside a 1-week window -> 400% of his 10h capacity
    _seed_story_task(p1, "A1", estimate=20, start=TODAY, end=TODAY + timedelta(days=6), member_id=over)
    _seed_story_task(p1, "A2", estimate=20, start=TODAY, end=TODAY + timedelta(days=6), member_id=over)
    # Lina: 16h over the same ~week -> 40%
    _seed_story_task(p2, "B1", estimate=16, start=TODAY, end=TODAY + timedelta(days=6), member_id=calm)
    # Same person booked in both projects with overlapping windows -> a conflict
    p2_omar = _seed_member(p2, "omar   hassan", capacity=40.0)  # name matched case/space-insensitively
    _seed_story_task(p2, "B2", estimate=10, start=TODAY + timedelta(days=1),
                     end=TODAY + timedelta(days=5), member_id=p2_omar)

    body = client.get("/portfolio/resources", headers=pm).json()
    rows = {r["name"]: r for r in body["members"]}
    assert rows["Omar Hassan"]["overallocated"] and rows["Omar Hassan"]["utilization_pct"] == 400
    assert rows["Lina Adel"]["utilization_pct"] == 40 and not rows["Lina Adel"]["overallocated"]

    conflicts = body["conflicts"]
    assert len(conflicts) == 1
    assert conflicts[0]["person"] == "Omar Hassan"
    assert set(conflicts[0]["projects"]) == {"Alpha", "Beta"}
    assert body["summary"]["overallocated"] == 1


# --------------------------------------------------------------------------- risks


def test_risks_are_severity_ordered_and_cover_aging_drafts():
    pm = pm_headers()
    pid = make_project(pm, name="Risk project")["id"]
    _seed_story_task(pid, "Overdue work", estimate=8, start=TODAY - timedelta(days=9),
                     end=TODAY - timedelta(days=2))
    _seed_draft(pid, days_old=6)  # aging: waiting for a PM decision for 6 days

    body = client.get("/portfolio/risks", headers=pm).json()
    mine = [r for r in body["risks"] if r["project_id"] == pid]
    assert mine, "the project must show up in the register"
    assert mine[0]["severity"] == "high"  # schedule risk sorts first
    assert any(r["type"] == "schedule" and "past their end date" in r["message"] for r in mine)
    assert any(r["type"] == "pipeline" and "days" in r["message"] and r["severity"] == "medium"
               for r in mine)
    assert all(r["suggestion"] for r in mine)  # every risk ships with a next action


def test_overallocated_member_becomes_a_high_risk():
    pm = pm_headers()
    pid = make_project(pm, name="Overbooked")["id"]
    member = _seed_member(pid, "Omar Hassan", capacity=10.0)
    _seed_story_task(pid, "A1", estimate=20, start=TODAY, end=TODAY + timedelta(days=6), member_id=member)
    _seed_story_task(pid, "A2", estimate=20, start=TODAY, end=TODAY + timedelta(days=6), member_id=member)

    body = client.get("/portfolio/risks", headers=pm).json()
    mine = [r for r in body["risks"] if r["project_id"] == pid and r["type"] == "resource"]
    assert any("400% utilization" in r["message"] and r["severity"] == "high" for r in mine)


# --------------------------------------------------------------------------- assistant chat


def _fake_llm(parsed: PortfolioChatOutput):
    def fake(**kwargs):
        assert kwargs["project_id"] is None and kwargs["job_id"] is None
        assert kwargs["agent"] == "pm_assistant"
        return StructuredRun(parsed=parsed, run_id=None, usage=None, cost_usd=0,
                             pricing_configured=False, attempts=1, trace_id=None)
    return fake


@pytest.fixture
def no_langfuse(monkeypatch):
    monkeypatch.setattr("app.services.portfolio.run_structured",
                        _fake_llm(PortfolioChatOutput(
                            reply="Two projects need attention.",
                            suggestions=[
                                {"title": "Open Risk project", "action": "open_project", "project_id": 999},
                                {"title": "Decide drafts", "action": "review_drafts", "project_id": None},
                                {"title": "Nuke the database", "action": "drop_database"},
                            ])))


def test_chat_is_pm_only(no_langfuse):
    pid = make_project(pm_headers(), name="Chat project")["id"]
    assert client.post("/portfolio/chat", json={"messages": [
        {"role": "user", "content": "What needs my attention?"}]},
        headers=tl_headers()).status_code == 403

    r = client.post("/portfolio/chat", json={"messages": [
        {"role": "user", "content": "What needs my attention?"}]}, headers=pm_headers())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reply"] == "Two projects need attention."
    # sanitised: the invented project id is dropped, the unknown action is dropped,
    # and only the dashboard-performable suggestion survives
    assert body["suggestions"] == [
        {"title": "Decide drafts", "action": "review_drafts", "project_id": None},
    ]
    assert any(a.action == "portfolio.assistant_chat" for a in audit_rows("portfolio.assistant_chat"))


def test_chat_rejects_history_without_a_user_message(no_langfuse):
    pm = pm_headers()
    r = client.post("/portfolio/chat", json={"messages": [
        {"role": "assistant", "content": "Hello!"}]}, headers=pm)
    assert r.status_code == 422


def test_chat_history_is_capped_and_snapshot_backs_the_answer(no_langfuse):
    pm = pm_headers()
    pid = make_project(pm, name="History project")["id"]
    _seed_story_task(pid, "Overdue work", estimate=8, start=TODAY - timedelta(days=9),
                     end=TODAY - timedelta(days=2))
    messages = [{"role": "user", "content": f"message {i}"} for i in range(20)]

    captured = {}

    def spy(**kwargs):
        captured["payload"] = kwargs["messages"][1]["content"]
        return _fake_llm(PortfolioChatOutput(reply="ok", suggestions=[]))(**kwargs)

    from app.services import portfolio as pf
    orig = pf.run_structured
    pf.run_structured = spy
    try:
        r = client.post("/portfolio/chat", json={"messages": messages}, headers=pm)
    finally:
        pf.run_structured = orig
    assert r.status_code == 200
    payload = captured["payload"]
    assert "message 19" in payload and "message 0" not in payload  # last-N history
    assert "History project" in payload and "Overdue work" in payload  # snapshot included