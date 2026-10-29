import io
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth.security import hash_password
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


def pm_headers():
    return login(*PM)


def tl_headers():
    return login(*TL)


def make_project(headers, **override) -> dict:
    body = {
        "name": "Clinic Booking",
        "goal": "Online appointments",
        "start_date": "2026-11-01",
        "end_date": "2026-12-01",
        "brief": "Patients book clinic appointments online.",
    }
    body.update(override)
    r = client.post("/projects", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def ensure_user(email, name, role, password):
    with SessionLocal() as s:
        if s.scalar(select(User).where(User.email == email)) is None:
            s.add(
                User(
                    email=email,
                    name=name,
                    role=role,
                    password_hash=hash_password(password),
                )
            )
            s.commit()


def assign_tl(project_id, email=TL[0]):
    with SessionLocal() as s:
        u = s.scalar(select(User).where(User.email == email))
        s.add(ProjectMember(project_id=project_id, user_id=u.id))
        s.commit()


def audit_rows(action):
    with SessionLocal() as s:
        return list(s.scalars(select(AuditLog).where(AuditLog.action == action)))
