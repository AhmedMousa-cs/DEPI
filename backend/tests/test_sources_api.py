import pytest

from app.config import settings
from helpers import (
    assign_tl,
    audit_rows,
    client,
    make_blank_pdf,
    make_docx,
    make_pdf,
    make_project,
    pm_headers,
    tl_headers,
)


@pytest.fixture
def pm():
    return pm_headers()


@pytest.fixture
def tl():
    return tl_headers()


def upload(headers, pid, filename=None, data=None, **form):
    form.setdefault("kind", "transcript")
    files = {"file": (filename, data, "application/octet-stream")} if filename else None
    return client.post(
        f"/projects/{pid}/sources", data=form, files=files, headers=headers
    )


CASES = [
    ("notes.txt", b"Kickoff: patients book online.", "Kickoff"),
    ("notes.md", b"# Kickoff\n- reminders", "reminders"),
    ("arabic.txt", "الـPM يعتمد النطاق".encode("utf-8"), "يعتمد النطاق"),
    ("notes.docx", make_docx("Kickoff: doctors define slots."), "doctors define slots"),
    ("notes.pdf", make_pdf("Kickoff meeting notes"), "Kickoff meeting notes"),
]


@pytest.mark.parametrize("filename,data,expected", CASES)
def test_upload_supported_formats(pm, filename, data, expected):
    pid = make_project(pm)["id"]
    r = upload(pm, pid, filename, data)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["original_filename"] == filename
    assert body["kind"] == "transcript"
    assert body["phase"] == "initial"
    detail = client.get(f"/projects/{pid}/sources/{body['id']}", headers=pm)
    assert expected in detail.json()["content_text"]


def test_pasted_text_source(pm):
    pid = make_project(pm)["id"]
    r = upload(pm, pid, kind="note", text="Doctor asked for SMS reminders", title="Call")
    assert r.status_code == 201
    assert r.json()["title"] == "Call"
    assert r.json()["original_filename"] is None


@pytest.mark.parametrize("filename", ["virus.exe", "photo.png", "noextension"])
def test_unsupported_type_415(pm, filename):
    pid = make_project(pm)["id"]
    assert upload(pm, pid, filename, b"data").status_code == 415


def test_oversized_file_413(pm, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_bytes", 100)
    pid = make_project(pm)["id"]
    r = upload(pm, pid, "big.txt", b"a" * 101)
    assert r.status_code == 413
    assert "too large" in r.json()["detail"]


def test_oversized_pasted_text_413(pm, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_bytes", 100)
    pid = make_project(pm)["id"]
    assert upload(pm, pid, text="a" * 101).status_code == 413


@pytest.mark.parametrize(
    "filename,data",
    [
        ("bad.docx", b"PK\x03\x04 not really a zip"),
        ("bad.docx", b"plain text renamed to docx"),
        ("bad.pdf", b"%PDF-1.4\nthis is garbage"),
        ("bad.pdf", b"not a pdf at all"),
        ("bin.txt", b"abc\x00def"),
    ],
)
def test_corrupted_files_422(pm, filename, data):
    pid = make_project(pm)["id"]
    assert upload(pm, pid, filename, data).status_code == 422


def test_empty_pdf_422(pm):
    pid = make_project(pm)["id"]
    r = upload(pm, pid, "blank.pdf", make_blank_pdf())
    assert r.status_code == 422
    assert "no extractable text" in r.json()["detail"]


def test_empty_text_file_422(pm):
    pid = make_project(pm)["id"]
    assert upload(pm, pid, "empty.txt", b"").status_code == 422


def test_text_and_file_together_or_neither_422(pm):
    pid = make_project(pm)["id"]
    assert upload(pm, pid).status_code == 422
    assert upload(pm, pid, "a.txt", b"hello", text="also text").status_code == 422


def test_invalid_kind_422(pm):
    pid = make_project(pm)["id"]
    assert upload(pm, pid, kind="brief", text="x").status_code == 422


def test_filename_is_sanitized(pm):
    pid = make_project(pm)["id"]
    r = upload(pm, pid, "../../etc/passwd.txt", b"hello")
    assert r.status_code == 201
    assert r.json()["original_filename"] == "passwd.txt"


def test_sources_visible_after_refresh(pm):
    pid = make_project(pm, transcript="initial transcript")["id"]
    upload(pm, pid, "n.txt", b"more notes")
    listed = client.get(f"/projects/{pid}/sources", headers=pm).json()
    assert [s["kind"] for s in listed] == ["brief", "transcript", "transcript"]
    detail = client.get(f"/projects/{pid}", headers=pm).json()
    assert len(detail["sources"]) == 3


def test_team_leader_cannot_upload_but_can_read(pm, tl):
    pid = make_project(pm)["id"]
    assert upload(tl, pid, text="x").status_code == 403
    assert client.get(f"/projects/{pid}/sources", headers=tl).status_code == 403
    assign_tl(pid)
    assert client.get(f"/projects/{pid}/sources", headers=tl).status_code == 200


def test_unauthenticated_and_unknown_project(pm):
    assert client.post("/projects/1/sources", data={"text": "x"}).status_code == 401
    assert upload(pm, 99999, text="x").status_code == 404


def test_source_from_other_project_is_404(pm):
    a = make_project(pm)["id"]
    b = make_project(pm, name="Other")["id"]
    sid = upload(pm, a, text="secret notes").json()["id"]
    assert client.get(f"/projects/{b}/sources/{sid}", headers=pm).status_code == 404


def test_upload_writes_audit_without_content(pm):
    pid = make_project(pm)["id"]
    upload(pm, pid, "n.txt", b"TOP-SECRET-CONTENT")
    rows = audit_rows("source.create")
    assert len(rows) == 1
    assert rows[0].after["content_length"] == len(b"TOP-SECRET-CONTENT")
    assert "TOP-SECRET" not in str(rows[0].after)
