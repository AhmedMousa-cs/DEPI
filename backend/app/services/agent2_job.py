"""Job handler: run Agent 2 over the project's APPROVED requirements and store draft stories/tasks.

The agent never touches the database; this handler is the only writer and it writes drafts only.
It refuses to run before the scope is approved, so a draft requirement can never reach Agent 2.
"""
import logging

from sqlalchemy import select

from app.agents import agent2_plan
from app.agents.agent2_plan import PlanningContext
from app.audit import write_audit
from app.db.models import Job, Project, Requirement, Source
from app.db.session import SessionLocal
from app.services.jobs import JobContext, PermanentJobError, register
from app.services.planning import get_approved_requirements, save_draft_plan

log = logging.getLogger("nexus.agent2_job")

PLANNABLE_STATUSES = ("planning", "plan_review")


def _load(job_id: int) -> tuple[int, str, list[dict], PlanningContext]:
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if job is None:
            raise PermanentJobError("job not found")
        project = db.scalar(select(Project).where(Project.id == job.project_id))
        if project is None:
            raise PermanentJobError("project not found")
        requirements = get_approved_requirements(db, project.id)
        # Background for the agent: the brief, the transcript and what the PM wrote at scope review.
        brief = db.scalar(
            select(Source.content_text).where(Source.project_id == project.id, Source.kind == "brief")
        )
        transcript = db.scalar(
            select(Source.content_text).where(Source.project_id == project.id, Source.kind == "transcript")
        )
        pm_decisions = tuple(
            f"Requirement {r['id']}: {r['pm_comment']}"
            for r in requirements
            if r.get("pm_comment")
        )
        return (
            project.id,
            project.status,
            requirements,
            PlanningContext(brief=brief, transcript=transcript, pm_decisions=pm_decisions),
        )


@register("agent2.plan")
def run_agent2_plan(ctx: JobContext, params: dict) -> str:
    project_id, status, requirements, context = _load(ctx.job_id)
    if status not in PLANNABLE_STATUSES:
        raise PermanentJobError(
            f"Agent 2 needs an approved scope (project status is '{status}'); approve the scope first."
        )
    if not requirements:
        raise PermanentJobError("No approved requirements: decide the scope before generating stories and tasks.")
    ctx.heartbeat(progress=10)

    flagged = agent2_plan.injection_flags(requirements)
    run = agent2_plan.generate_plan(
        project_id=project_id, requirements=requirements, context=context, job_id=ctx.job_id
    )
    ctx.heartbeat(progress=80)

    with SessionLocal() as db:  # one transaction: stories + tasks + dependencies + audit
        saved = save_draft_plan(
            db,
            project_id=project_id,
            output=run.parsed,
            run_id=run.run_id,
        )
        write_audit(
            db,
            "agent:agent2_plan",
            "agent2.planning_completed",
            "project",
            project_id,
            after={
                "job_id": ctx.job_id,
                "run_id": run.run_id,
                "approved_requirements": len(requirements),
                "stories_drafted": len(saved.stories),
                "tasks_drafted": len(saved.tasks),
                "dependencies_created": saved.dependencies,
                "skipped_protected": saved.skipped_protected,
                "plan_soft_issues": run.soft_issues,
                "flagged_requirements": {str(rid): flags for rid, flags in flagged.items()},
            },
        )
        db.commit()

    ctx.heartbeat(progress=100)
    return f"stories_drafted:{len(saved.stories)};tasks_drafted:{len(saved.tasks)}"[:255]
