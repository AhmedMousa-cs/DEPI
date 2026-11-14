"""Job handler: Agent 3 builds a DRAFT plan version from the approved tasks, then queues Agent 4.

Writes drafts only (plan_versions.status='draft'). Tasks are not touched: the schedule reaches the
tasks table only when the PM approves the plan (app/orchestrator/plan_gate.py).
"""
from __future__ import annotations

import logging
from datetime import date

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.agents import agent3_schedule
from app.audit import write_audit
from app.config import settings
from app.db.models import Job
from app.db.session import SessionLocal
from app.schemas.plan import TaskOverride
from app.schemas.scheduling import TaskInput, TeamMemberInput
from app.services.jobs import JobContext, PermanentJobError, register
from app.services.plan_versions import build_snapshot, create_draft_version, load_plan_inputs

log = logging.getLogger("nexus.agent3_job")
PLANNABLE_STATUSES = ("planning", "plan_review", "active")
ACTOR = "agent:agent3_schedule"


def llm_available() -> bool:
    p = (settings.llm_provider or "").lower()
    return bool(settings.groq_api_key) if p == "groq" else bool(settings.anthropic_api_key) if p == "anthropic" \
        else p == "ollama"


def _parse_overrides(raw, task_ids: set[int], member_ids: set[int]) -> list[dict]:
    out: list[dict] = []
    seen: set[int] = set()
    for item in raw or []:
        try:
            o = TaskOverride.model_validate(item)
        except ValidationError as exc:
            raise PermanentJobError(f"invalid override: {exc.errors()[0]['msg']}") from None
        if o.task_id not in task_ids:
            raise PermanentJobError(f"override refers to task {o.task_id}, which is not an approved task of this project")
        if o.assignee_member_id is not None and o.assignee_member_id not in member_ids:
            raise PermanentJobError(f"override refers to member {o.assignee_member_id}, who is not in this project's team")
        if o.task_id in seen:
            raise PermanentJobError(f"two overrides for task {o.task_id}")
        seen.add(o.task_id)
        out.append(o.model_dump(exclude_none=True))
    return out


@register("agent3.schedule")
def run_agent3_schedule(ctx: JobContext, params: dict) -> str:
    with SessionLocal() as db:
        job = db.get(Job, ctx.job_id)
        if job is None:
            raise PermanentJobError("job not found")
        project_id = job.project_id
        inputs = load_plan_inputs(db, project_id)
    if inputs.project["status"] not in PLANNABLE_STATUSES:
        raise PermanentJobError(
            f"A plan can't be built while the project is '{inputs.project['status']}'; approve the scope first.")
    if not inputs.tasks:
        raise PermanentJobError("No approved tasks: approve the tasks (Agent 2) before building the plan.")
    if inputs.dangling:
        raise PermanentJobError(f"Approved tasks depend on tasks that are not approved: {inputs.dangling[:5]}")
    ctx.heartbeat(progress=10)

    overrides = _parse_overrides(params.get("overrides"), {t["id"] for t in inputs.tasks},
                                 {m["id"] for m in inputs.members})
    by_task = {o["task_id"]: o for o in overrides}
    start_raw = params.get("start_date") or inputs.project["start_date"]
    start = date.fromisoformat(start_raw) if start_raw else date.today()

    tasks = []
    for t in inputs.tasks:
        o = by_task.get(t["id"], {})
        tasks.append(TaskInput(
            id=t["id"], title=t["title"], estimate_hours=t["estimate_hours"], duration_days=o.get("duration_days"),
            assignee_id=o.get("assignee_member_id", t["assignee_member_id"]), depends_on=inputs.deps[t["id"]]))
    members = [TeamMemberInput(id=m["id"], name=m["name"], capacity_hours_per_week=m["capacity_hours_per_week"],
                               available_from=m["available_from"], available_to=m["available_to"])
               for m in inputs.members]
    llm = None
    if llm_available():
        from app.agents.agent3_llm import make_runner_llm

        llm = make_runner_llm(project_id=project_id, job_id=ctx.job_id)
    try:
        output = agent3_schedule.run_agent3(tasks, members, start, llm=llm)
    except agent3_schedule.SchedulingError as exc:
        raise PermanentJobError(str(exc)) from None
    ctx.heartbeat(progress=70)

    snapshot = build_snapshot(inputs=inputs, output=output, overrides=overrides)
    with SessionLocal() as db:
        pv = create_draft_version(db, project_id=project_id, snapshot=snapshot, created_by=ACTOR)
        write_audit(db, ACTOR, "agent3.schedule_completed", "project", project_id,
                    after={"job_id": ctx.job_id, "version": pv.version, "tasks": len(snapshot["tasks"]),
                           "conflicts": len(snapshot["conflicts"]), "overrides": len(overrides),
                           "project_end": snapshot["schedule"]["project_end"]})
        version = pv.version
        db.commit()

    try:  # hand over to Agent 4 (one active review job per project at a time)
        with SessionLocal() as db:
            db.add(Job(project_id=project_id, type="agent4.review", params={"version": version},
                       created_by=job.created_by))
            db.commit()
    except IntegrityError:
        log.info("agent4.review already active for project %s", project_id)
    ctx.heartbeat(progress=100)
    return f"version:{version};tasks:{len(snapshot['tasks'])};conflicts:{len(snapshot['conflicts'])}"[:255]
