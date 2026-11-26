"""Job handler: Agent 3 schedules the project (CPM + optional LLM) and snapshots the plan.

One Agent3Output feeds the Schedule tab preview and the draft plan version for Agent 4.
The agent leaves task rows untouched. The PM publishes the generated schedule through
the versioned approval gate in Plan Review.
"""
from __future__ import annotations

import json
import logging
from datetime import date

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.agents import agent3_schedule
from app.agents.agent3_schedule import PROMPT_VERSION
from app.audit import write_audit
from app.config import settings
from app.db.models import Job
from app.db.session import SessionLocal
from app.llm.runner import StructuredRun, run_structured
from app.schemas.plan import TaskOverride
from app.schemas.scheduling import EGYPT_CALENDAR
from app.services.change_schedule import build_schedule_inputs, schedule_constraints
from app.services.jobs import JobContext, PermanentJobError, register
from app.services.plan_versions import (
    PendingChangeDraft,
    build_snapshot,
    create_draft_version,
    current_approved,
    ensure_can_replace_draft,
    load_plan_inputs,
)
from app.services.scheduling import save_schedule_draft

log = logging.getLogger("nexus.agent3_job")
PLANNABLE_STATUSES = ("planning", "plan_review", "active")
ACTOR = "agent:agent3_schedule"


def llm_available() -> bool:
    p = (settings.llm_provider or "").lower()
    return bool(settings.groq_api_key) if p == "groq" else bool(settings.anthropic_api_key) if p == "anthropic" \
        else p == "ollama"


class BudgetedLLM:
    """Agent 3's StructuredLLM protocol, backed by run_structured (one agent_run per call).

    The runner handles the budget reserve, agent_runs, audit and Langfuse tracing; without a
    configured provider it raises and Agent 3 degrades to its deterministic template.
    """

    def __init__(self, project_id: int, job_id: int):
        self.project_id = project_id
        self.job_id = job_id
        self.run_ids: list[int] = []

    def __call__(self, *, system_prompt: str, payload: dict, schema):
        messages = [
            {"role": "system", "content": system_prompt},
            # payload is untrusted data; the prompt says so explicitly
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
        ]
        run: StructuredRun = run_structured(
            project_id=self.project_id,
            job_id=self.job_id,
            agent="agent3_schedule",
            prompt_version=PROMPT_VERSION,
            messages=messages,
            schema=schema,
        )
        self.run_ids.append(run.run_id)
        return run.parsed


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
        try:
            ensure_can_replace_draft(db, project_id)
        except PendingChangeDraft as exc:
            raise PermanentJobError(str(exc)) from None
        published = current_approved(db, project_id)
        inputs = load_plan_inputs(db, project_id)
    if inputs.project["status"] not in PLANNABLE_STATUSES:
        raise PermanentJobError(
            f"A plan can't be built while the project is '{inputs.project['status']}'; approve the scope first.")
    if not inputs.tasks:
        raise PermanentJobError("No approved tasks: approve the tasks (Agent 2) before building the plan.")
    if inputs.dangling:
        raise PermanentJobError(f"Approved tasks depend on tasks that are not approved: {inputs.dangling[:5]}")
    ctx.heartbeat(progress=10)

    task_ids = {task["id"] for task in inputs.tasks}
    requested = _parse_overrides(params.get("overrides"), task_ids, {m["id"] for m in inputs.members})
    baseline = published.snapshot if published is not None else {}
    overrides, fixed_dates = schedule_constraints(baseline, task_ids, requested)
    start_raw = params.get("start_date") or inputs.project["start_date"]
    if fixed_dates and not params.get("start_date") and baseline["project"]["start_date"] == start_raw:
        start_raw = baseline["schedule"]["project_start"]
    # Every LLM call goes through run_structured (budget, agent_runs, audit, Langfuse). Agent 2
    # always fills estimate_hours, so the estimator stays silent in the normal flow; the adapter
    # is only used for missing estimates and the explanation wording (degrades to the
    # deterministic template on any failure).
    llm = BudgetedLLM(project_id, ctx.job_id)
    try:
        start = date.fromisoformat(start_raw) if start_raw else date.today()
        tasks, members = build_schedule_inputs(inputs, overrides, fixed_dates)
        output = agent3_schedule.run_agent3(tasks, members, start, calendar=EGYPT_CALENDAR, llm=llm)
    except ValueError as exc:
        raise PermanentJobError(str(exc)) from None
    ctx.heartbeat(progress=70)

    snapshot = build_snapshot(inputs=inputs, output=output, overrides=[overrides[key] for key in sorted(overrides)])
    snapshot["fixed_dates"] = [fixed_dates[key] for key in sorted(fixed_dates)]
    with SessionLocal() as db:  # one transaction: schedule draft + plan version + audits
        try:
            pv = create_draft_version(db, project_id=project_id, snapshot=snapshot, created_by=ACTOR)
        except PendingChangeDraft as exc:
            raise PermanentJobError(str(exc)) from None
        # The project lock held by draft creation prevents a publication between this
        # check and commit. Discard schedules computed from constraints that just changed.
        current = current_approved(db, project_id)
        if (current.id if current else None) != (published.id if published else None):
            raise PermanentJobError("The approved plan changed during scheduling. Run Agent 3 again.")
        draft = save_schedule_draft(
            db,
            project_id=project_id,
            payload=output.model_dump(mode="json"),
            project_start=output.schedule.project_start,
            project_end=output.schedule.project_end,
            total_duration_days=output.schedule.total_duration_days,
            conflicts_count=len(output.conflicts),
            run_ids=llm.run_ids,
        )
        write_audit(
            db,
            ACTOR,
            "agent3.scheduling_completed",
            "project",
            project_id,
            after={
                "job_id": ctx.job_id,
                "draft_id": draft.id,
                "run_ids": llm.run_ids,
                "tasks_scheduled": len(output.schedule.tasks),
                "project_start": output.schedule.project_start.isoformat(),
                "project_end": output.schedule.project_end.isoformat(),
                "total_duration_days": output.schedule.total_duration_days,
                "critical_path": list(output.schedule.critical_path),
                "conflicts": len(output.conflicts),
                "overloaded_members": [m.member_id for m in output.capacity.members if m.is_overloaded],
                "llm_estimated_tasks": [e.task_id for e in output.estimates if e.source == "llm"],
                "explanation_source": output.explanation.source,
            },
        )
        write_audit(db, ACTOR, "agent3.schedule_completed", "project", project_id,
                    after={"job_id": ctx.job_id, "version": pv.version, "tasks": len(snapshot["tasks"]),
                           "conflicts": len(snapshot["conflicts"]), "overrides": len(overrides),
                           "project_end": snapshot["schedule"]["project_end"], "draft_id": draft.id})
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
    return f"version:{version};tasks:{len(snapshot['tasks'])};draft:{draft.id};conflicts:{len(snapshot['conflicts'])}"[:255]
