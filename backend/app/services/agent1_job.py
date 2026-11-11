"""Job handler: run Agent 1 over a project's initial sources and store the result as DRAFT requirements.

The agent itself never touches the database; this handler is the only place that persists its
output, and it does so through save_draft_requirements (drafts only).
"""
import logging

from sqlalchemy import select

from app.agents import agent1_extract
from app.audit import write_audit
from app.db.models import Job, Source
from app.db.session import SessionLocal
from app.services.jobs import JobContext, PermanentJobError, register
from app.orchestrator.checkpoints import postgres_checkpointer
from app.orchestrator.scope_gate import ScopeGate
from app.services.requirements import save_draft_requirements

log = logging.getLogger("nexus.agent1_job")


def _load_sources(job_id: int) -> tuple[int, list[dict]]:
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if job is None:
            raise PermanentJobError("job not found")
        project_id = job.project_id
        rows = db.scalars(
            select(Source)
            .where(Source.project_id == project_id, Source.phase == "initial")
            .order_by(Source.id)
        )
        sources = [
            {"id": s.id, "kind": s.kind, "title": s.title, "content_text": s.content_text}
            for s in rows
            if (s.content_text or "").strip()
        ]
    return project_id, sources


@register("agent1.extract")
def run_agent1_extract(ctx: JobContext, params: dict) -> str:
    project_id, sources = _load_sources(ctx.job_id)
    if not sources:
        raise PermanentJobError("project has no initial sources with text to extract from")
    ctx.heartbeat(progress=5)
    flagged = agent1_extract.injection_flags(sources)

    def on_batch(done: int, total: int) -> None:
        ctx.check_deadline()
        ctx.heartbeat(progress=10 + 80 * done // total)

    extraction = agent1_extract.extract_requirements_chunked(
        project_id=project_id, sources=sources, job_id=ctx.job_id, on_batch=on_batch
    )
    # Every quote is checked against its own FULL source; unverified items get a capped confidence.
    verified = agent1_extract.verify_extraction(extraction.requirements, sources)
    run_id = extraction.runs[0].run_id if extraction.runs else None

    with SessionLocal() as db:  # one transaction: drafts + audit succeed or fail together
        created = save_draft_requirements(db, project_id=project_id, items=verified, run_id=run_id)
        unverified = sum(1 for r in created if not r.quote_verified)
        write_audit(
            db,
            "agent:agent1_extract",
            "agent1.extraction_completed",
            "project",
            project_id,
            after={
                "job_id": ctx.job_id,
                "drafts": len(created),
                "unverified": unverified,
                "batches": extraction.batches,
                "flagged_sources": {str(sid): flags for sid, flags in flagged.items()},
            },
        )
        db.commit()
    try:  # drafts are already saved; if this fails the PM can re-open the gate with POST /scope/open
        with postgres_checkpointer() as saver:
            ScopeGate(saver).open(project_id)
    except Exception:  # noqa: BLE001
        log.exception('could not open the scope gate for project %s', project_id)
    ctx.heartbeat(progress=100)
    return f"requirements_drafted:{len(created)}"[:255]