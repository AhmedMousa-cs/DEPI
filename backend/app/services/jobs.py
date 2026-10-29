"""PostgreSQL job queue: claim with SKIP LOCKED, retry with backoff, recover stale jobs."""
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import func, select, update

from app.audit import write_audit
from app.config import settings
from app.db.models import Job
from app.db.session import SessionLocal

log = logging.getLogger("nexus.jobs")

WORKER_ACTOR = "system:worker"
ACTIVE_STATUSES = ("queued", "running", "waiting_for_pm")


class JobTimeout(Exception):
    """The job ran past its deadline."""


class PermanentJobError(Exception):
    """Raise from a handler when retrying cannot help."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class JobContext:
    job_id: int
    deadline: datetime

    def heartbeat(self, progress: int | None = None) -> None:
        values: dict = {"heartbeat_at": utcnow()}
        if progress is not None:
            values["progress"] = max(0, min(100, int(progress)))
        with SessionLocal() as db:
            db.execute(
                update(Job)
                .where(Job.id == self.job_id, Job.status == "running")
                .values(**values)
            )
            db.commit()

    def check_deadline(self) -> None:
        if utcnow() > self.deadline:
            raise JobTimeout(f"job exceeded {settings.job_timeout_seconds}s")


Handler = Callable[[JobContext, dict], "str | None"]
HANDLERS: dict[str, Handler] = {}


def register(job_type: str):
    def deco(fn: Handler) -> Handler:
        HANDLERS[job_type] = fn
        return fn

    return deco


def _msg(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"[:500]


def claim_next_job(db) -> Job | None:
    """Atomically take the oldest runnable job. Rows locked by another worker are skipped."""
    job = db.scalar(
        select(Job)
        .where(Job.status == "queued", Job.available_at <= func.now())
        .order_by(Job.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if job is None:
        return None
    now = utcnow()
    job.status = "running"
    job.attempts += 1
    job.started_at = now
    job.heartbeat_at = now
    job.error = None
    write_audit(
        db, WORKER_ACTOR, "job.start", "job", job.id,
        after={"type": job.type, "attempt": job.attempts},
    )
    db.commit()
    return job


def _settle_done(job_id: int, result_ref: str | None) -> None:
    with SessionLocal() as db:
        job = db.get(Job, job_id, with_for_update=True)
        if job is None or job.status != "running":
            return  # cancelled or recovered meanwhile
        job.status = "done"
        job.progress = 100
        job.result_ref = result_ref
        job.finished_at = utcnow()
        write_audit(db, WORKER_ACTOR, "job.done", "job", job_id, after={"result_ref": result_ref})
        db.commit()


def _settle_failure(job_id: int, exc: Exception, retryable: bool) -> None:
    with SessionLocal() as db:
        job = db.get(Job, job_id, with_for_update=True)
        if job is None or job.status != "running":
            return
        job.error = _msg(exc)
        if retryable and job.attempts <= settings.job_max_retries:
            delay = settings.job_backoff_seconds * 2 ** (job.attempts - 1)
            job.status = "queued"
            job.available_at = utcnow() + timedelta(seconds=delay)
            action = "job.retry"
        else:
            job.status = "failed"
            job.finished_at = utcnow()
            action = "job.failed"
        write_audit(
            db, WORKER_ACTOR, action, "job", job_id,
            after={"error": job.error, "attempt": job.attempts},
        )
        db.commit()


def process_one() -> bool:
    """Claim and run one job. Returns False when nothing was runnable."""
    with SessionLocal() as db:
        job = claim_next_job(db)
        if job is None:
            return False
        job_id, job_type, params = job.id, job.type, dict(job.params or {})
    ctx = JobContext(
        job_id=job_id,
        deadline=utcnow() + timedelta(seconds=settings.job_timeout_seconds),
    )
    try:
        handler = HANDLERS.get(job_type)
        if handler is None:
            raise PermanentJobError(f"no handler registered for '{job_type}'")
        result_ref = handler(ctx, params)
    except PermanentJobError as exc:
        log.warning("job %s failed permanently: %s", job_id, exc)
        _settle_failure(job_id, exc, retryable=False)
    except Exception as exc:  # noqa: BLE001  (any handler error is a job failure)
        log.exception("job %s raised", job_id)
        _settle_failure(job_id, exc, retryable=True)
    else:
        _settle_done(job_id, result_ref)
    return True


def recover_stale() -> int:
    """Running jobs whose worker stopped heart-beating go back to the queue (or fail)."""
    cutoff = utcnow() - timedelta(seconds=settings.job_stale_seconds)
    count = 0
    with SessionLocal() as db:
        stale = db.scalars(
            select(Job)
            .where(Job.status == "running", Job.heartbeat_at < cutoff)
            .with_for_update(skip_locked=True)
        ).all()
        for job in stale:
            job.error = "worker stopped responding"
            if job.attempts <= settings.job_max_retries:
                job.status = "queued"
                job.available_at = utcnow()
            else:
                job.status = "failed"
                job.finished_at = utcnow()
            write_audit(
                db, WORKER_ACTOR, "job.recovered", "job", job.id,
                after={"status": job.status, "attempt": job.attempts},
            )
            count += 1
        db.commit()
    return count
