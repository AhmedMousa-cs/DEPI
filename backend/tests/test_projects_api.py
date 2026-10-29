import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import AuditLog, ProjectMember, User
from app.db.session import SessionLocal
from app.main import app

client = TestClient(app)

PM = ("pm@nexus.test", "pm12345")
TL = ("leader@nexus.test", "leader12345")


def login(email, password):
    r = client.post("/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


@pytest.fixture
def pm():
    return login(*PM)


@pytest.fixture
def tl():
    return login(*TL)


def make_project(headers, **override):
    body = {
        "name": "Clinic Booking",
        "goal": "Online appointments",
        "start_date": "2026-11-01",
        "end_date": "2026-12-01",
        "brief": "Patients book clinic appointments online.",
    }
    body.update(override)
    return client.post("/projects", json=body, headers=headers)


def assign_tl(project_id):
    with SessionLocal() as s:
        leader = s.scalar(select(User).where(User.email == TL[0]))
        s.add(ProjectMember(project_id=project_id, user_id=leader.id))
        s.commit()


def audit_rows(action):
    with SessionLocal() as s:
        return list(s.scalars(select(AuditLog).where(AuditLog.action == action)))


def test_create_with_brief_only(pm):
    r = make_project(pm)
    assert r.status_code == 201
    data = r.json()
    assert data["status"] == "setup"
    assert data["brief"] == "Patients book clinic appointments online."
    assert [s["kind"] for s in data["sources"]] == ["brief"]


def test_create_with_pasted_transcript(pm):
    r = make_project(pm, transcript="PM: we need reminders.")
    assert r.status_code == 201
    sources = r.json()["sources"]
    assert sorted(s["kind"] for s in sources) == ["brief", "transcript"]
    assert all(s["phase"] == "initial" for s in sources)


@pytest.mark.parametrize(
    "override",
    [
        {"brief": ""},
        {"brief": "   "},
        {"name": ""},
        {"start_date": "2026-12-01", "end_date": "2026-11-01"},
    ],
)
def test_create_validation_422(pm, override):
    assert make_project(pm, **override).status_code == 422


def test_create_missing_brief_422(pm):
    r = client.post("/projects", json={"name": "x"}, headers=pm)
    assert r.status_code == 422


def test_create_requires_auth_and_pm(tl):
    assert client.post("/projects", json={"name": "x", "brief": "y"}).status_code == 401
    assert make_project(tl).status_code == 403


def test_create_writes_audit(pm):
    pid = make_project(pm).json()["id"]
    rows = audit_rows("project.create")
    assert len(rows) == 1
    assert rows[0].entity_type == "project"
    assert rows[0].entity_id == str(pid)
    assert rows[0].actor.startswith("user:")


def test_persistence_roundtrip(pm):
    pid = make_project(pm).json()["id"]
    r = client.get(f"/projects/{pid}", headers=pm)
    assert r.status_code == 200
    assert r.json()["name"] == "Clinic Booking"
    assert r.json()["brief"].startswith("Patients")


def test_list_scoped_by_role(pm, tl):
    a = make_project(pm, name="A").json()["id"]
    b = make_project(pm, name="B").json()["id"]
    assert {p["id"] for p in client.get("/projects", headers=pm).json()} == {a, b}
    assert client.get("/projects", headers=tl).json() == []
    assign_tl(a)
    assert [p["id"] for p in client.get("/projects", headers=tl).json()] == [a]


def test_get_access_rules(pm, tl):
    pid = make_project(pm).json()["id"]
    assert client.get(f"/projects/{pid}", headers=tl).status_code == 403
    assign_tl(pid)
    assert client.get(f"/projects/{pid}", headers=tl).status_code == 200
    assert client.get("/projects/99999", headers=pm).status_code == 404


def test_patch_updates_and_audits(pm):
    pid = make_project(pm).json()["id"]
    r = client.patch(
        f"/projects/{pid}",
        json={"name": "New name", "brief": "Updated brief"},
        headers=pm,
    )
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "New name"
    assert data["brief"] == "Updated brief"
    assert len(data["sources"]) == 1
    rows = audit_rows("project.update")
    assert len(rows) == 1
    assert rows[0].before["name"] == "Clinic Booking"
    assert rows[0].after["name"] == "New name"


def test_patch_forbidden_for_team_leader(pm, tl):
    pid = make_project(pm).json()["id"]
    assign_tl(pid)
    r = client.patch(f"/projects/{pid}", json={"name": "Hack"}, headers=tl)
    assert r.status_code == 403
    assert audit_rows("project.update") == []


def test_patch_validation(pm):
    pid = make_project(pm).json()["id"]
    assert client.patch(f"/projects/{pid}", json={}, headers=pm).status_code == 400
    assert (
        client.patch(f"/projects/{pid}", json={"name": None}, headers=pm).status_code
        == 422
    )
    assert (
        client.patch(
            f"/projects/{pid}", json={"end_date": "2020-01-01"}, headers=pm
        ).status_code
        == 422
    )
    assert (
        client.patch("/projects/99999", json={"name": "x"}, headers=pm).status_code
        == 404
    )
