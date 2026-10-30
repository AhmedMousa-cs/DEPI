from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient

from app.main import app
from app.auth.dependencies import require_role

router = APIRouter()


@router.get("/_test/pm-only", dependencies=[Depends(require_role("PM"))])
def pm_only():
    return {"ok": True}


app.include_router(router)
client = TestClient(app)


def login(email, password):
    r = client.post("/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def test_pm_only_without_token_is_401():
    assert client.get("/_test/pm-only").status_code == 401


def test_pm_only_with_team_leader_is_403():
    headers = login("leader@nexus.test", "leader12345")
    assert client.get("/_test/pm-only", headers=headers).status_code == 403


def test_pm_only_with_pm_is_200():
    headers = login("pm@nexus.test", "pm12345")
    assert client.get("/_test/pm-only", headers=headers).status_code == 200