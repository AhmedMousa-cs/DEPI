import pytest

from app.config import settings
from helpers import (
    PM,
    TL,
    assign_tl,
    audit_rows,
    client,
    ensure_user,
    login,
    make_project,
    pm_headers,
    tl_headers,
)

MEMBER = {
    "name": "Omar Hassan",
    "role_title": "Backend Developer",
    "skills": ["python", "fastapi"],
    "capacity_hours_per_week": 30,
    "available_from": "2026-11-01",
    "available_to": "2026-12-15",
}
CSV_OK = (
    "name,role_title,skills,capacity_hours_per_week,available_from,available_to\n"
    "Mona Adel,Frontend Developer,javascript;css,25,2026-11-01,2026-12-15\n"
    "Karim Samir,QA Engineer,testing;pytest,20,,\n"
)


@pytest.fixture
def pm():
    return pm_headers()


@pytest.fixture
def tl():
    return tl_headers()


def add(headers, pid, **override):
    return client.post(f"/projects/{pid}/team", json={**MEMBER, **override}, headers=headers)


def import_csv(headers, pid, text, filename="team.csv"):
    return client.post(
        f"/projects/{pid}/team/import",
        files={"file": (filename, text.encode("utf-8"), "text/csv")},
        headers=headers,
    )


def team(headers, pid):
    return client.get(f"/projects/{pid}/team", headers=headers).json()


def test_add_member_persists_and_audits(pm):
    pid = make_project(pm)["id"]
    r = add(pm, pid)
    assert r.status_code == 201, r.text
    assert r.json()["skills"] == ["python", "fastapi"]
    assert r.json()["is_leader"] is False
    assert [m["name"] for m in team(pm, pid)] == ["Omar Hassan"]
    rows = audit_rows("team.create")
    assert len(rows) == 1 and rows[0].after["name"] == "Omar Hassan"


@pytest.mark.parametrize(
    "override",
    [
        {"name": ""},
        {"capacity_hours_per_week": 200},
        {"capacity_hours_per_week": -1},
        {"available_from": "2026-12-15", "available_to": "2026-11-01"},
        {"unknown_field": 1},
    ],
)
def test_add_member_validation_422(pm, override):
    pid = make_project(pm)["id"]
    assert add(pm, pid, **override).status_code == 422


def test_duplicate_name_409(pm):
    pid = make_project(pm)["id"]
    add(pm, pid)
    assert add(pm, pid, name="omar hassan").status_code == 409


def test_team_write_requires_pm(pm, tl):
    pid = make_project(pm)["id"]
    assert client.post(f"/projects/{pid}/team", json=MEMBER).status_code == 401
    assert add(tl, pid).status_code == 403
    assert import_csv(tl, pid, CSV_OK).status_code == 403


def test_team_read_requires_project_access(pm, tl):
    pid = make_project(pm)["id"]
    add(pm, pid)
    assert client.get(f"/projects/{pid}/team", headers=tl).status_code == 403
    assign_tl(pid)
    assert len(team(tl, pid)) == 1


def test_update_member_and_audit(pm):
    pid = make_project(pm)["id"]
    mid = add(pm, pid).json()["id"]
    r = client.patch(
        f"/projects/{pid}/team/{mid}",
        json={"capacity_hours_per_week": 20, "skills": ["python", "sql"]},
        headers=pm,
    )
    assert r.status_code == 200
    assert r.json()["capacity_hours_per_week"] == 20
    assert team(pm, pid)[0]["skills"] == ["python", "sql"]
    rows = audit_rows("team.update")
    assert rows[0].before["capacity_hours_per_week"] == 30
    assert rows[0].after["capacity_hours_per_week"] == 20


def test_update_validation_and_scoping(pm):
    p1 = make_project(pm)["id"]
    p2 = make_project(pm, name="Other")["id"]
    mid = add(pm, p1).json()["id"]
    assert client.patch(f"/projects/{p1}/team/{mid}", json={}, headers=pm).status_code == 400
    assert (
        client.patch(f"/projects/{p1}/team/{mid}", json={"name": None}, headers=pm).status_code
        == 422
    )
    assert (
        client.patch(f"/projects/{p1}/team/{mid}", json={"available_to": "2020-01-01"}, headers=pm).status_code
        == 422
    )
    assert client.patch(f"/projects/{p2}/team/{mid}", json={"name": "X"}, headers=pm).status_code == 404


def test_delete_member(pm):
    pid = make_project(pm)["id"]
    mid = add(pm, pid).json()["id"]
    assert client.delete(f"/projects/{pid}/team/{mid}", headers=pm).status_code == 204
    assert team(pm, pid) == []
    assert audit_rows("team.delete")[0].before["name"] == "Omar Hassan"
    assert client.delete(f"/projects/{pid}/team/{mid}", headers=pm).status_code == 404


def test_csv_import_success(pm):
    pid = make_project(pm)["id"]
    r = import_csv(pm, pid, CSV_OK)
    assert r.status_code == 201, r.text
    members = team(pm, pid)
    assert [m["name"] for m in members] == ["Mona Adel", "Karim Samir"]
    assert members[0]["skills"] == ["javascript", "css"]
    assert members[1]["available_from"] is None
    assert audit_rows("team.import")[0].after["count"] == 2


def test_csv_import_is_all_or_nothing(pm):
    pid = make_project(pm)["id"]
    bad = CSV_OK + "Nour Fathy,Designer,figma,abc,,\n"
    r = import_csv(pm, pid, bad)
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["errors"][0]["line"] == 4
    assert "capacity_hours_per_week" in detail["errors"][0]["message"]
    assert team(pm, pid) == []


@pytest.mark.parametrize(
    "text",
    [
        "",
        "role_title,skills\nBackend,python\n",
        "name,salary\nOmar,100\n",
        "name,capacity_hours_per_week\n",
        "name,available_from,available_to\nOmar,2026-12-01,2026-11-01\n",
    ],
)
def test_csv_import_invalid_422(pm, text):
    pid = make_project(pm)["id"]
    assert import_csv(pm, pid, text).status_code == 422
    assert team(pm, pid) == []


def test_csv_duplicates_rejected(pm):
    pid = make_project(pm)["id"]
    assert import_csv(pm, pid, "name\nAhmed\nahmed\n").status_code == 422
    add(pm, pid)
    assert import_csv(pm, pid, "name\nOmar Hassan\n").status_code == 422


def test_csv_wrong_extension_and_size(pm, monkeypatch):
    pid = make_project(pm)["id"]
    assert import_csv(pm, pid, CSV_OK, filename="team.txt").status_code == 415
    monkeypatch.setattr(settings, "max_upload_bytes", 50)
    assert import_csv(pm, pid, CSV_OK).status_code == 413


def test_assign_leader_grants_project_access(pm, tl):
    pid = make_project(pm)["id"]
    mid = add(pm, pid).json()["id"]
    assert client.get(f"/projects/{pid}", headers=tl).status_code == 403
    r = client.put(
        f"/projects/{pid}/leader",
        json={"member_id": mid, "user_email": TL[0]},
        headers=pm,
    )
    assert r.status_code == 200, r.text
    assert r.json()["is_leader"] is True and r.json()["user_email"] == TL[0]
    assert client.get(f"/projects/{pid}", headers=tl).status_code == 200
    assert audit_rows("team.set_leader")


def test_leader_rules(pm, tl):
    pid = make_project(pm)["id"]
    mid = add(pm, pid).json()["id"]
    put = lambda h, body: client.put(f"/projects/{pid}/leader", json=body, headers=h)
    assert put(tl, {"member_id": mid, "user_email": TL[0]}).status_code == 403
    assert put(pm, {"member_id": mid, "user_email": PM[0]}).status_code == 422
    assert put(pm, {"member_id": mid, "user_email": "ghost@nexus.test"}).status_code == 404
    assert put(pm, {"member_id": 99999, "user_email": TL[0]}).status_code == 404


def test_only_one_leader_and_reassignment_moves_access(pm, tl):
    ensure_user("tl2@nexus.test", "Second Leader", "TEAM_LEADER", "tl2-12345")
    tl2 = login("tl2@nexus.test", "tl2-12345")
    pid = make_project(pm)["id"]
    a = add(pm, pid).json()["id"]
    b = add(pm, pid, name="Mona Adel").json()["id"]
    client.put(f"/projects/{pid}/leader", json={"member_id": a, "user_email": TL[0]}, headers=pm)
    r = client.put(
        f"/projects/{pid}/leader", json={"member_id": b, "user_email": "tl2@nexus.test"}, headers=pm
    )
    assert r.status_code == 200
    leaders = [m for m in team(pm, pid) if m["is_leader"]]
    assert [m["id"] for m in leaders] == [b]
    assert client.get(f"/projects/{pid}", headers=tl).status_code == 403
    assert client.get(f"/projects/{pid}", headers=tl2).status_code == 200


def test_remove_leader_revokes_access(pm, tl):
    pid = make_project(pm)["id"]
    mid = add(pm, pid).json()["id"]
    client.put(f"/projects/{pid}/leader", json={"member_id": mid, "user_email": TL[0]}, headers=pm)
    assert client.delete(f"/projects/{pid}/leader", headers=pm).status_code == 204
    assert client.get(f"/projects/{pid}", headers=tl).status_code == 403
    assert client.delete(f"/projects/{pid}/leader", headers=pm).status_code == 404
    assert audit_rows("team.remove_leader")


def test_deleting_leader_member_revokes_access(pm, tl):
    pid = make_project(pm)["id"]
    mid = add(pm, pid).json()["id"]
    client.put(f"/projects/{pid}/leader", json={"member_id": mid, "user_email": TL[0]}, headers=pm)
    client.delete(f"/projects/{pid}/team/{mid}", headers=pm)
    assert client.get(f"/projects/{pid}", headers=tl).status_code == 403


def test_team_leaders_lookup_is_pm_only(pm, tl):
    r = client.get("/users/team-leaders", headers=pm)
    assert r.status_code == 200
    assert TL[0] in [u["email"] for u in r.json()]
    assert client.get("/users/team-leaders", headers=tl).status_code == 403
