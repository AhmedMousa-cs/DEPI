"""Job handler: Agent 4 reviews the current DRAFT plan version (risks + verdict), draft-only."""
from __future__ import annotations

import logging

from app.agents import agent4_plan
from app.audit import write_audit
from app.db.models import Job, PlanVersion
from app.db.session import SessionLocal
from app.services.jobs import JobContext, PermanentJobError, register
from app.services.plan_versions import current_draft, get_version

log = logging.getLogger("nexus.agent4_job")


@register("agent4.review")
def run_agent4_review(ctx: JobContext, params: dict) -> str:
    with SessionLocal() as db:
        job = db.get(Job, ctx.job_id)
        if job is None:
            raise PermanentJobError("job not found")
        project_id = job.project_id
        if params.get("version") is not None:
            pv = get_version(db, project_id, int(params["version"]))
        else:
            pv = current_draft(db, project_id)
        if pv is None:
            raise PermanentJobError("No draft plan to review: build the plan first.")
        if pv.status != "draft":
            raise PermanentJobError(f"Plan version {pv.version} is '{pv.status}'; only a draft can be reviewed.")
        plan_version_id, version = pv.id, pv.version
    ctx.heartbeat(progress=10)

    result = agent4_plan.run_agent4(project_id=project_id, plan_version_id=plan_version_id, job_id=ctx.job_id,
                                    rules_only=bool(params.get("rules_only")))
    ctx.heartbeat(progress=85)
    with SessionLocal() as db:
        write_audit(db, f"agent:{agent4_plan.AGENT}", "agent4.review_completed", "plan_version", plan_version_id,
                    after={"job_id": ctx.job_id, "version": version, "verdict": result.verdict,
                           "source": result.review_source, "agent_risks": result.agent_risks_saved,
                           "rule_risks": result.rule_risks_saved, "stopped": result.stopped_reason,
                           "tool_calls": len(result.tool_calls)})
        db.commit()
    after_review(project_id, version)
    ctx.heartbeat(progress=100)
    return (f"version:{version};verdict:{result.verdict};risks:{result.agent_risks_saved + result.rule_risks_saved}"
            )[:255]


def after_review(project_id: int, version: int) -> None:
    """Hook: the approval gate is opened here (added with the gate)."""
