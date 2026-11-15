"""Agent 3 job: schedule the project's tasks (deterministic CPM + optional LLM estimates/words).

Draft-only rule: the agent computes and saves a schedule_draft row. tasks.start / tasks.end are
written only when the PM approves the draft (see services/scheduling.py).

Every LLM call goes through run_structured (budget reserve, agent_runs, audit, Langfuse), so Agent 3
obeys the same platform rules as Agents 1 and 2 (ADR-006 / ADR-007).
"""
import json
import logging
from datetime import date

from sqlalchemy import select

from app.agents.agent3_schedule import Agent3Output, PROMPT_VERSION, run_agent3
from app.audit import write_audit
from app.db.models import Job, Project, Task, TaskDependency, TeamMember
from app.db.session import SessionLocal
from app.llm.runner import StructuredRun, run_structured
from app.schemas.scheduling import TaskInput, TeamMemberInput
from app.services.jobs import JobContext, PermanentJobError, register
from app.services.scheduling import save_schedule_draft

log = logging.getLogger("nexus.agent3_job")

# The plan stage is live (Agent 2 has run). The real "plan reviewed" gate is enforced below:
# at least one task must be PM-approved before a schedule is computed.
SCHEDULABLE_STATUSES = ("planning", "plan_review", "active")


class BudgetedLLM:
    """Agent 3's StructuredLLM protocol, backed by run_structured (one agent_run per call)."""

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


def _load(job_id: int) -> tuple[int, str, list[Task], list[TeamMember]]:
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if job is None:
            raise PermanentJobError("job not found")
        project = db.scalar(select(Project).where(Project.id == job.project_id))
        if project is None:
            raise PermanentJobError("project not found")
        tasks = list(db.scalars(select(Task).where(Task.project_id == project.id).order_by(Task.id)))
        members = list(
            db.scalars(select(TeamMember).where(TeamMember.project_id == project.id).order_by(TeamMember.id))
        )
        return project.id, project.status, tasks, members


def _task_inputs(db, tasks: list[Task]) -> list[TaskInput]:
    """Agent 3 input shape: str ids, dependencies from task_dependencies, estimates in hours."""
    deps: dict[int, list[int]] = {}
    if tasks:
        rows = db.scalars(
            select(TaskDependency).where(TaskDependency.task_id.in_([t.id for t in tasks]))
        ).all()
        for row in rows:
            deps.setdefault(row.task_id, []).append(row.depends_on_task_id)
    return [
        TaskInput(
            id=str(t.id),
            title=t.title,
            estimate_hours=float(t.estimate_hours),
            assignee_id=str(t.assignee_member_id) if t.assignee_member_id else None,
            depends_on=tuple(str(d) for d in sorted(deps.get(t.id, []))),
        )
        for t in tasks
    ]


def _member_inputs(members: list[TeamMember]) -> list[TeamMemberInput]:
    return [
        TeamMemberInput(
            id=str(m.id),
            name=m.name,
            capacity_hours_per_week=float(m.capacity_hours_per_week),
            available_from=m.available_from,
            available_to=m.available_to,
        )
        for m in members
    ]


@register("agent3.schedule")
def run_agent3_schedule(ctx: JobContext, params: dict) -> str:
    project_id, status, tasks, members = _load(ctx.job_id)
    if status not in SCHEDULABLE_STATUSES:
        raise PermanentJobError(
            f"Agent 3 cannot schedule while the project is '{status}'."
        )
    if not tasks:
        raise PermanentJobError("No tasks to schedule: run Agent 2 first.")
    if not any(t.review_status == "approved" for t in tasks):
        raise PermanentJobError(
            "The work plan is not approved yet: approve the plan tasks, then run scheduling."
        )
    ctx.heartbeat(progress=10)

    with SessionLocal() as db:
        task_inputs = _task_inputs(db, tasks)
    # Every LLM call goes through run_structured. Agent 2 always fills estimate_hours, so the
    # estimator stays silent in the normal flow; the adapter is only used for missing estimates
    # and for the explanation wording (degrades to the deterministic template on any failure).
    llm = BudgetedLLM(project_id, ctx.job_id)
    out: Agent3Output = run_agent3(
        task_inputs,
        _member_inputs(members),
        date.today(),
        llm=llm,
        save_draft=None,  # persistence happens below, in one transaction with the audit row
    )
    ctx.heartbeat(progress=80)

    with SessionLocal() as db:  # one transaction: draft + audit
        draft = save_schedule_draft(
            db,
            project_id=project_id,
            payload=out.model_dump(mode="json"),
            project_start=out.schedule.project_start,
            project_end=out.schedule.project_end,
            total_duration_days=out.schedule.total_duration_days,
            conflicts_count=len(out.conflicts),
            run_ids=llm.run_ids,
        )
        write_audit(
            db,
            "agent:agent3_schedule",
            "agent3.scheduling_completed",
            "project",
            project_id,
            after={
                "job_id": ctx.job_id,
                "draft_id": draft.id,
                "run_ids": llm.run_ids,
                "tasks_scheduled": len(out.schedule.tasks),
                "project_start": out.schedule.project_start.isoformat(),
                "project_end": out.schedule.project_end.isoformat(),
                "total_duration_days": out.schedule.total_duration_days,
                "critical_path": list(out.schedule.critical_path),
                "conflicts": len(out.conflicts),
                "overloaded_members": [m.member_id for m in out.capacity.members if m.is_overloaded],
                "llm_estimated_tasks": [e.task_id for e in out.estimates if e.source == "llm"],
                "explanation_source": out.explanation.source,
            },
        )
        db.commit()

    ctx.heartbeat(progress=100)
    return (
        f"draft:{draft.id};tasks:{len(out.schedule.tasks)};"
        f"end:{out.schedule.project_end.isoformat()};conflicts:{len(out.conflicts)}"
    )[:255]
