from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.auth import router as auth_router
from app.auth.dependencies import require_role
from app.auth.security import create_access_token, hash_password
from app.db.session import get_db

USERS = {
    1: SimpleNamespace(
        id=1, email="pm@x.test", name="PM", role="PM", password_hash=hash_password("pw")
    ),
    2: SimpleNamespace(
        id=2,
        email="tl@x.test",
        name="TL",
        role="TEAM_LEADER",
        password_hash=hash_password("pw"),
    ),
}


class FakeDB:
    def get(self, model, key):
        return USERS.get(key)

    def scalar(self, stmt):
        email = stmt.compile().params["email_1"]
        return next((u for u in USERS.values() if u.email == email), None)


def build_app():
    app = FastAPI()
    app.include_router(auth_router)

    @app.get("/pm-only")
    def pm_only(user=Depends(require_role("PM"))):
        return {"ok": True}

    app.dependency_overrides[get_db] = lambda: FakeDB()
    return app


@pytest.fixture
def client():
    return TestClient(build_app())


def auth(user_id, role):
    return {"Authorization": f"Bearer {create_access_token(user_id, role)}"}


def test_login_success(client):
    r = client.post("/auth/login", json={"email": "pm@x.test", "password": "pw"})
    assert r.status_code == 200
    assert r.json()["role"] == "PM"


def test_login_wrong_password(client):
    r = client.post("/auth/login", json={"email": "pm@x.test", "password": "bad"})
    assert r.status_code == 401


def test_login_unknown_email(client):
    r = client.post("/auth/login", json={"email": "no@x.test", "password": "pw"})
    assert r.status_code == 401


def test_me_requires_token(client):
    assert client.get("/auth/me").status_code == 401


def test_me_with_bad_token(client):
    r = client.get("/auth/me", headers={"Authorization": "Bearer garbage"})
    assert r.status_code == 401


def test_me_ok(client):
    r = client.get("/auth/me", headers=auth(1, "PM"))
    assert r.status_code == 200
    assert r.json()["email"] == "pm@x.test"


def test_pm_endpoint_allows_pm(client):
    assert client.get("/pm-only", headers=auth(1, "PM")).status_code == 200


def test_pm_endpoint_403_for_team_leader(client):
    assert client.get("/pm-only", headers=auth(2, "TEAM_LEADER")).status_code == 403