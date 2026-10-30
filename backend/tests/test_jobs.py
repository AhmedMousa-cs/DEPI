from datetime import timedelta

import pytest
from sqlalchemy import select

import app.services.job_handlers  # noqa: F401
from app.config import settings
from app.db.models import Job
from app.db.session import SessionLocal
from app.services.jobs import (
    HANDLERS,
    PermanentJobError,
    claim_next_job,
    process_one,
    recover_stale,
    utcnow,
)
from helpers import assign_tl, audit_rows, client, make_project, pm_headers, tl_headers


def new_project():
    return make_project(pm_headers())["id"]


def add_job(pid, type="demo.echo", params=None):
    with SessionLocal() as s:
        j = Job(project_id=pid, type=type, params=params or {})
        s.add(j)
        s.commit()
        return j.id


def get_job(jid):
    with SessionLocal() as s:
        return s.get(Job, jid)


def set_job(jid, **fields):
    with SessionLocal() as s:
        j = s.get(Job, jid)
        for k, v in fields.items():
            setattr(j, k, v)
        s.commit()


# ---------- queue mechanics ----------
def test_claim_marks_running_and_is_exclusive():
    jid = add_job(new_project())
    with SessionLocal() as db:
        job = claim_next_job(db)
        assert job.id == jid and job.status == "running"
        assert job.attempts == 1 and job.heartbeat_at is not None
    with SessionLocal() as db:
        assert claim_next_job(db) is None


def test_skip_locked_ignores_rows_locked_by_another_worker():
    jid = add_job(new_project())
    a = SessionLocal()
    try:
        a.scalar(select(Job).where(Job.id == jid).with_for_update())
        with SessionLocal() as b:
            assert claim_next_job(b) is None
    finally:
        a.rollback()
        a.close()
    with SessionLocal() as c:
        assert claim_next_job(c).id == jid


def test_successful_job_flow():
    jid = add_job(new_project(), params={"steps": 2, "message": "hi"})
    assert process_one() is True
    job = get_job(jid)
    assert job.status == "done" and job.progress == 100
    assert job.result_ref == "echo:hi" and job.finished_at is not None
    assert process_one() is False
    assert audit_rows("job.done")


def test_retry_with_backoff_then_permanent_failure(monkeypatch):
    def boom(ctx, params):
        raise RuntimeError("boom")

    monkeypatch.setitem(HANDLERS, "test.boom", boom)
    jid = add_job(new_project(), type="test.boom")

    assert process_one() is True
    job = get_job(jid)
    assert job.status == "queued" and job.attempts == 1
    assert job.available_at > utcnow() and "boom" in job.error
    assert process_one() is False  # still backing off

    set_job(jid, available_at=utcnow() - timedelta(seconds=1))
    assert process_one() is True
    assert get_job(jid).status == "queued" and get_job(jid).attempts == 2

    set_job(jid, available_at=utcnow() - timedelta(seconds=1))
    assert process_one() is True
    job = get_job(jid)
    assert job.status == "failed" and job.attempts == 3
    assert "RuntimeError: boom" in job.error and job.finished_at is not None
    assert audit_rows("job.failed")


def test_permanent_error_is_not_retried(monkeypatch):
    def nope(ctx, params):
        raise PermanentJobError("bad input")

    monkeypatch.setitem(HANDLERS, "test.nope", nope)
    jid = add_job(new_project(), type="test.nope")
    process_one()
    job = get_job(jid)
    assert job.status == "failed" and job.attempts == 1


def test_unknown_handler_fails_cleanly():
    jid = add_job(new_project(), type="test.missing")
    process_one()
    job = get_job(jid)
    assert job.status == "failed" and "no handler" in job.error


def test_timeout_is_a_retryable_failure(monkeypatch):
    monkeypatch.setattr(settings, "job_timeout_seconds", -1)
    jid = add_job(new_project())
    process_one()
    job = get_job(jid)
    assert job.status == "queued" and "JobTimeout" in job.error


def test_recover_stale_requeues_dead_worker_jobs():
    jid = add_job(new_project())
    with SessionLocal() as db:
        claim_next_job(db)
    assert recover_stale() == 0  # fresh heartbeat: leave it alone
    set_job(jid, heartbeat_at=utcnow() - timedelta(hours=1))
    assert recover_stale() == 1
    job = get_job(jid)
    assert job.status == "queued" and job.attempts == 1
    assert process_one() is True and get_job(jid).status == "done"


def test_recover_stale_fails_when_attempts_exhausted():
    jid = add_job(new_project())
    with SessionLocal() as db:
        claim_next_job(db)
    set_job(
        jid,
        attempts=settings.job_max_retries + 1,
        heartbeat_at=utcnow() - timedelta(hours=1),
    )
    assert recover_stale() == 1
    assert get_job(jid).status == "failed"


# ---------- API ----------
def test_api_create_run_and_poll():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    r = client.post(
        f"/projects/{pid}/jobs",
        json={"type": "demo.echo", "params": {"steps": 1, "message": "hi"}},
        headers=pm,
    )
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "queued"
    jid = r.json()["id"]
    assert process_one() is True
    polled = client.get(f"/jobs/{jid}", headers=pm).json()
    assert polled["status"] == "done" and polled["progress"] == 100
    assert audit_rows("job.create")


def test_api_unknown_type_422():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    r = client.post(f"/projects/{pid}/jobs", json={"type": "nope"}, headers=pm)
    assert r.status_code == 422


def test_api_one_active_job_per_project_and_type():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    first = client.post(f"/projects/{pid}/jobs", json={"type": "demo.echo"}, headers=pm)
    second = client.post(f"/projects/{pid}/jobs", json={"type": "demo.echo"}, headers=pm)
    assert first.status_code == 202 and second.status_code == 409
    assert second.json()["detail"]["job_id"] == first.json()["id"]
    process_one()  # finish it
    third = client.post(f"/projects/{pid}/jobs", json={"type": "demo.echo"}, headers=pm)
    assert third.status_code == 202  # allowed again after completion


def test_api_access_rules():
    pm, tl = pm_headers(), tl_headers()
    pid = make_project(pm)["id"]
    assert client.post(f"/projects/{pid}/jobs", json={"type": "demo.echo"}).status_code == 401
    assert client.post(f"/projects/{pid}/jobs", json={"type": "demo.echo"}, headers=tl).status_code == 403
    jid = client.post(f"/projects/{pid}/jobs", json={"type": "demo.echo"}, headers=pm).json()["id"]
    assert client.get(f"/jobs/{jid}").status_code == 401
    assert client.get(f"/jobs/{jid}", headers=tl).status_code == 403
    assert client.get(f"/projects/{pid}/jobs", headers=tl).status_code == 403
    assign_tl(pid)
    assert client.get(f"/jobs/{jid}", headers=tl).status_code == 200
    assert len(client.get(f"/projects/{pid}/jobs", headers=tl).json()) == 1
    assert client.get("/jobs/99999", headers=pm).status_code == 404
    assert client.post("/projects/99999/jobs", json={"type": "demo.echo"}, headers=pm).status_code == 404
