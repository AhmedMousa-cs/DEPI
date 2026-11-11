import inspect

import pytest
from sqlalchemy import select

from app.db.models import Decision, Requirement, Source
from app.db.session import SessionLocal
from app.schemas.extraction import ExtractedRequirement
from app.services.quotes import verify_requirements
from app.services.requirements import save_draft_requirements
from helpers import (  # if this import fails, copy the import style used in test_projects_api.py
    assign_tl,
    audit_rows,
    client,
    make_project,
    pm_headers,
    tl_headers,
)

SOURCE_TEXT = "We need online booking. We also need SMS reminders for patients."


@pytest.fixture(autouse=True)
def scores(monkeypatch):
    """Never talk to Langfuse from tests; record what would have been sent."""
    calls = []
    monkeypatch.setattr("app.api.review.send_pm_score", lambda **kw: calls.append(kw) or True)
    return calls


def seed_requirements(project_id: int, n: int = 2) -> list[int]:
    with SessionLocal() as s:
        src = Source(project_id=project_id, kind="transcript", title="t", content_text=SOURCE_TEXT, phase="initial")
        s.add(src)
        s.flush()
        rows = [
            Requirement(
                project_id=project_id,
                type="functional",
                text=f"Requirement {i}",
                source_id=src.id,
                quote="We need online booking.",
                quote_verified=True,
                confidence=0.9,
                created_by_agent="agent1_extract",
                status="draft",
            )
            for i in range(n)
        ]
        s.add_all(rows)
        s.commit()
        return [r.id for r in rows]


def url(pid, rid=None, suffix=""):
    base = f"/projects/{pid}/requirements"
    return base if rid is None else f"{base}/{rid}{suffix}"


# ---------------- access control ----------------

def test_list_requires_auth_and_pm():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    seed_requirements(pid)
    assert client.get(url(pid)).status_code == 401
    tl = tl_headers()
    assert client.get(url(pid), headers=tl).status_code == 403
    assign_tl(pid)  # even a Team Leader on the project cannot review agent output
    assert client.get(url(pid), headers=tl).status_code == 403
    assert len(client.get(url(pid), headers=pm).json()) == 2


def test_team_leader_cannot_decide():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    rid = seed_requirements(pid, 1)[0]
    assign_tl(pid)
    r = client.post(url(pid, rid, "/decision"), json={"decision": "approved"}, headers=tl_headers())
    assert r.status_code == 403
    assert client.get(url(pid, rid), headers=pm).json()["status"] == "draft"
    assert audit_rows("requirement.approved") == []


def test_requirement_of_another_project_is_404():
    pm = pm_headers()
    a = make_project(pm, name="A")["id"]
    b = make_project(pm, name="B")["id"]
    rid = seed_requirements(a, 1)[0]
    assert client.get(url(b, rid), headers=pm).status_code == 404
    assert client.post(url(b, rid, "/decision"), json={"decision": "approved"}, headers=pm).status_code == 404


# ---------------- decisions ----------------

def test_approve_locks_and_records(scores):
    pm = pm_headers()
    pid = make_project(pm)["id"]
    rid = seed_requirements(pid, 1)[0]
    r = client.post(url(pid, rid, "/decision"), json={"decision": "approved"}, headers=pm)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "approved"
    assert data["locked_by_pm"] is True
    assert data["reviewed_by"] is not None and data["reviewed_at"] is not None

    with SessionLocal() as s:
        decisions = list(s.scalars(select(Decision)))
    assert [(d.subject_type, d.subject_id, d.decision) for d in decisions] == [("requirement", rid, "approved")]
    assert decisions[0].decided_by == data["reviewed_by"]

    rows = audit_rows("requirement.approved")
    assert len(rows) == 1 and rows[0].before["status"] == "draft" and rows[0].after["status"] == "approved"
    assert scores and scores[0]["decision"] == "approved" and scores[0]["requirement_id"] == rid


def test_reject_requires_comment():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    rid = seed_requirements(pid, 1)[0]
    assert client.post(url(pid, rid, "/decision"), json={"decision": "rejected"}, headers=pm).status_code == 422
    r = client.post(url(pid, rid, "/decision"), json={"decision": "rejected", "comment": "duplicate"}, headers=pm)
    assert r.status_code == 200
    assert r.json()["status"] == "rejected" and r.json()["pm_comment"] == "duplicate"


def test_modify_applies_edits_and_audits():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    rid = seed_requirements(pid, 1)[0]
    body = {"decision": "modified", "comment": "clearer wording", "text": "Patients can book online", "priority": "high"}
    r = client.post(url(pid, rid, "/decision"), json=body, headers=pm)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "approved" and data["text"] == "Patients can book online" and data["priority"] == "high"
    row = audit_rows("requirement.modified")[0]
    assert row.before["text"] == "Requirement 0" and row.after["text"] == "Patients can book online"


@pytest.mark.parametrize(
    "body",
    [
        {"decision": "modified", "comment": "x"},  # nothing to modify
        {"decision": "modified", "text": "New text here"},  # no comment
        {"decision": "approved", "text": "sneaky edit"},  # edits only with modified
        {"decision": "approved", "decided_by": 1},  # decided_by comes from the JWT only
        {"decision": "bogus"},
    ],
)
def test_invalid_decision_bodies_422(body):
    pm = pm_headers()
    pid = make_project(pm)["id"]
    rid = seed_requirements(pid, 1)[0]
    assert client.post(url(pid, rid, "/decision"), json=body, headers=pm).status_code == 422
    assert client.get(url(pid, rid), headers=pm).json()["status"] == "draft"


def test_list_filters():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    a, _ = seed_requirements(pid, 2)
    client.post(url(pid, a, "/decision"), json={"decision": "approved"}, headers=pm)
    assert len(client.get(url(pid) + "?status=draft", headers=pm).json()) == 1
    assert len(client.get(url(pid) + "?status=approved", headers=pm).json()) == 1
    assert client.get(url(pid) + "?status=weird", headers=pm).status_code == 422


# ---------------- the agent can only write drafts ----------------

def _items(source_id):
    return verify_requirements(
        [
            ExtractedRequirement(
                type="functional", text="Online booking", source_id=source_id,
                exact_quote="We need online booking.", confidence=0.9,
            ),
            ExtractedRequirement(
                type="feature", text="Invented thing", source_id=999999,  # source that does not exist
                exact_quote="This sentence is not in any source", confidence=0.95,
            ),
        ],
        {source_id: SOURCE_TEXT},
    )


def test_save_draft_requirements_has_no_status_parameter():
    assert "status" not in inspect.signature(save_draft_requirements).parameters


def test_agent_save_creates_only_drafts_and_nulls_foreign_sources():
    pid = make_project(pm_headers())["id"]
    with SessionLocal() as s:
        src = Source(project_id=pid, kind="transcript", title="t", content_text=SOURCE_TEXT, phase="initial")
        s.add(src)
        s.flush()
        created = save_draft_requirements(s, project_id=pid, items=_items(src.id), run_id=None)
        assert len(created) == 2
        assert all(r.status == "draft" and r.locked_by_pm is False for r in created)
        ok, bad = created
        assert ok.quote_verified is True and ok.source_id == src.id
        assert bad.quote_verified is False and bad.source_id is None and bad.confidence <= 0.3
        s.commit()
    assert len(audit_rows("requirements.drafts_saved")) == 1


def test_rerun_never_overwrites_pm_decisions():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    with SessionLocal() as s:
        src = Source(project_id=pid, kind="transcript", title="t", content_text=SOURCE_TEXT, phase="initial")
        s.add(src)
        s.flush()
        sid = src.id
        save_draft_requirements(s, project_id=pid, items=_items(sid))
        s.commit()

    rows = client.get(url(pid), headers=pm).json()
    booking = next(r for r in rows if r["quote"] == "We need online booking.")
    client.post(
        url(pid, booking["id"], "/decision"),
        json={"decision": "modified", "comment": "wording", "text": "Edited by PM"},
        headers=pm,
    )

    with SessionLocal() as s:  # the agent runs again with the same output
        save_draft_requirements(s, project_id=pid, items=_items(sid))
        s.commit()

    rows = client.get(url(pid), headers=pm).json()
    assert len(rows) == 2  # PM-decided item kept once, the untouched draft was replaced
    kept = next(r for r in rows if r["id"] == booking["id"])
    assert kept["text"] == "Edited by PM" and kept["status"] == "approved"