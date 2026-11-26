"""PM task-change proposals, deterministic plan drafts, and change decisions."""
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.audit import user_actor, write_audit
from app.db.models import ChangeRequest, Project, Task, TaskDependency, TeamMember
from app.schemas.scheduling import EGYPT_CALENDAR
from app.services import plan_versions
from app.services.change_schedule import build_change_snapshot
from app.services.scheduling import save_schedule_draft


class ChangeError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400, **context):
        self.code, self.message, self.status_code = code, message, status_code
        self.detail = {"code": code, "message": message, **context}
        super().__init__(message)


EDITABLE_FIELDS = ("title", "estimate_hours", "start", "end", "assignee_member_id")


def task_change_baseline(task: Task) -> dict:
    return {
        "title": task.title,
        "estimate_hours": float(task.estimate_hours),
        "start": task.start.isoformat() if task.start else None,
        "end": task.end.isoformat() if task.end else None,
        "assignee_member_id": task.assignee_member_id,
    }


def _validate_change_fields(db: Session, task: Task, changes: dict, before: dict) -> dict:
    """Check the effective window and team membership, and return dates for impact analysis."""
    member_id = changes.get("assignee_member_id")
    if member_id is not None:
        member = db.get(TeamMember, member_id)
        if member is None or member.project_id != task.project_id:
            raise ChangeError("invalid_assignee", "Select an assignee from this project's team.", 422)

    normalized = dict(changes)
    if "start" in changes or "end" in changes:
        effective = {field: changes.get(field, before[field]) for field in ("start", "end")}
        try:
            for field, value in effective.items():
                effective[field] = date.fromisoformat(value) if value else None
        except (TypeError, ValueError) as exc:
            raise ChangeError("invalid_task_dates", "Enter valid start and end dates.", 422) from exc
        start, end = effective["start"], effective["end"]
        if start and end and end < start:
            raise ChangeError("invalid_task_dates", "The task end date must be on or after its start date.", 422)
        for field in ("start", "end"):
            if field in changes:
                if not effective[field] or not EGYPT_CALENDAR.is_working_day(effective[field]):
                    raise ChangeError("invalid_task_dates", f"The task {field} date must be a working day.", 422)
                normalized[field] = effective[field]
    return normalized


def _approved_change_task(db: Session, project_id: int, task_id: int, *, lock: bool = False) -> Task:
    query = select(Task).where(Task.id == task_id, Task.project_id == project_id)
    if lock:
        query = query.with_for_update(nowait=True).execution_options(populate_existing=True)
    task = db.scalar(query)
    if task is None:
        raise ChangeError("task_not_found", "Task not found", 404)
    if task.review_status != "approved":
        raise ChangeError("task_not_approved", "Only approved tasks go through change management.", 409)
    return task


def analyze_task_change(db: Session, task: Task, after: dict) -> dict:
    """Deterministic impact of a proposed change on an approved task.

    Every finding is {severity, message}; severity high/medium/low mirrors the
    risk register. Pure code — the same numbers always produce the same impact.
    """
    impacts: list[dict] = []
    today = date.today()

    old_end, new_end = task.end, after.get("end")
    old_start, new_start = task.start, after.get("start")

    if new_end and old_end and new_end > old_end:
        delta = (new_end - old_end).days
        if task.is_critical:
            impacts.append({
                "severity": "high",
                "message": f"critical-path task delayed by {delta} day(s) could move the project end",
            })
        else:
            impacts.append({
                "severity": "medium",
                "message": f"task end shifted +{delta} day(s)",
            })
        dependents = db.scalars(
            select(Task).join(TaskDependency, TaskDependency.task_id == Task.id).where(
                TaskDependency.depends_on_task_id == task.id, Task.project_id == task.project_id,
                Task.review_status == "approved", Task.start.is_not(None),
            ).order_by(Task.id)
        )
        for dep in dependents:
            impacts.append({
                "severity": "medium" if not dep.is_critical else "high",
                "message": f"dependent task #{dep.id} '{dep.title}' may need a later window",
            })

    if new_start and old_start and new_start < today and new_start < old_start:
        impacts.append({
            "severity": "low",
            "message": f"new start {new_start.isoformat()} is in the past",
        })

    est_old, est_new = task.estimate_hours, after.get("estimate_hours")
    if est_new is not None and float(est_new) != float(est_old):
        diff = float(est_new) - float(est_old)
        member_id = after.get("assignee_member_id") or task.assignee_member_id
        if member_id:
            week = timedelta(days=7)
            window_start = new_start or old_start or today
            window_end = new_end or old_end or (today + week)
            peers = [
                t for t in db.scalars(select(Task).where(
                    Task.project_id == task.project_id,
                    Task.assignee_member_id == member_id,
                    Task.review_status == "approved",
                )) if t.id != task.id and t.start and t.end
                and t.start <= window_end and (t.end or t.start) >= window_start
            ]
            load = sum(float(t.estimate_hours) for t in peers)
            if member := db.get(TeamMember, member_id):
                cap_weeks = max(0.25, ((window_end - window_start).days + 1) / 7)
                capacity = float(member.capacity_hours_per_week) * cap_weeks
                if load + float(est_new) > capacity:
                    impacts.append({
                        "severity": "high",
                        "message": f"{member.name}'s load in this window would be "
                                   f"{load + float(est_new):.0f}h against a capacity of {capacity:.0f}h",
                    })
        if diff > 0:
            impacts.append({
                "severity": "low",
                "message": f"estimate raised by {diff:.1f}h",
            })
        else:
            impacts.append({
                "severity": "low",
                "message": f"estimate reduced by {abs(diff):.1f}h",
            })

    if after.get("assignee_member_id") not in (None, task.assignee_member_id):
        impacts.append({"severity": "low", "message": "task reassigned to a different team member"})

    severity = "high" if any(i["severity"] == "high" for i in impacts) \
        else "medium" if any(i["severity"] == "medium" for i in impacts) \
        else "low"
    return {
        "severity": severity,
        "impacts": impacts or [{"severity": "low", "message": "no schedule or capacity impact detected"}],
    }


def propose_task_change(
    db: Session, *, project_id: int, task_id: int,
    after: dict, reason: str, pm,
) -> ChangeRequest:
    task = _approved_change_task(db, project_id, task_id)
    before = task_change_baseline(task)
    # keep only fields whose value actually changes (honest diff)
    clean = {k: v for k, v in after.items() if k in EDITABLE_FIELDS and v is not None and v != before[k]}
    if not clean:
        raise ChangeError("no_changes", "The proposal changes nothing", 422)
    impact = analyze_task_change(db, task, _validate_change_fields(db, task, clean, before))

    cr = ChangeRequest(
        project_id=project_id,
        kind="task_update",
        target_task_id=task.id,
        payload={"before": before, "after": clean},
        impact=impact,
        reason=reason,
        status="draft",
        created_by=pm.email,
    )
    db.add(cr)
    db.flush()  # assign cr.id before the audit row references it
    write_audit(
        db,
        actor=pm.email,
        action="change.proposed",
        entity_type="change_request",
        entity_id=cr.id,
        after={"task_id": task.id, "fields": list(clean.keys()), "severity": impact["severity"]},
    )
    return cr


def decide_change(
    db: Session, *, project_id: int, change_id: int, decision: str,
    comment: str | None, pm,
) -> ChangeRequest:
    project = db.scalar(select(Project).where(Project.id == project_id).with_for_update()
                        .execution_options(populate_existing=True))
    if project is None:
        raise ChangeError("project_not_found", "Project not found", 404)
    cr = db.scalar(select(ChangeRequest).where(ChangeRequest.id == change_id, ChangeRequest.project_id == project_id)
                   .with_for_update().execution_options(populate_existing=True))
    if cr is None:
        raise ChangeError("change_not_found", "Change request not found", 404)
    if cr.status != "draft":
        raise ChangeError("already_decided", f"Change request is already {cr.status}", 409)

    staged = {}
    if decision == "approved":
        staged = _stage_change(db, project, cr, pm)
    cr.status = decision
    cr.reviewed_by = pm.id
    cr.reviewed_at = datetime.now(timezone.utc)

    write_audit(
        db,
        actor=pm.email,
        action="change.decision",
        entity_type="change_request",
        entity_id=cr.id,
        before={"status": "draft", "task": cr.payload.get("before")},
        after={
            "status": cr.status,
            "decision": decision,
            **staged,
            "comment": comment or None,
        },
    )
    return cr


def _stage_change(db: Session, project: Project, cr: ChangeRequest, pm) -> dict:
    if project.status == "closed":
        raise ChangeError("project_closed", "Reopen the project before staging a task change.", 409)
    if plan_versions.current_approved(db, project.id) is None:
        raise ChangeError("approved_plan_required", "Approve a baseline plan before accepting task changes.", 409)
    try:
        with db.begin_nested():
            draft = plan_versions.ensure_can_replace_draft(db, project.id)
            if draft is not None:
                # A review can hold the draft while trying to reference the locked project.
                plan_versions.get_version(db, project.id, draft.version, lock=True, nowait=True)
            task = _approved_change_task(db, project.id, cr.target_task_id, lock=True)
    except plan_versions.PendingChangeDraft as exc:
        raise ChangeError("change_plan_pending", str(exc), 409, plan_version=exc.version) from exc
    except OperationalError as exc:
        if getattr(exc.orig, "sqlstate", None) != "55P03":
            raise
        raise ChangeError("change_inputs_busy", "Plan inputs are being updated. Try accepting the change again.", 409) from exc
    before = task_change_baseline(task)
    if before != cr.payload.get("before"):
        raise ChangeError("change_inputs_changed", "The task changed since this request was proposed. Propose it again.", 409)
    after = cr.payload["after"]
    _validate_change_fields(db, task, after, before)

    try:
        snapshot, output = build_change_snapshot(db, project.id, task.id, after)
    except ValueError as exc:
        raise ChangeError("change_schedule_infeasible", str(exc), 422) from exc
    snapshot["change_request_id"] = cr.id
    version = plan_versions.create_draft_version(db, project_id=project.id, snapshot=snapshot, created_by=user_actor(pm))
    payload = output.model_dump(mode="json")
    payload.update(plan_version=version.version, change_request_id=cr.id)
    save_schedule_draft(
        db, project_id=project.id, payload=payload, project_start=output.schedule.project_start,
        project_end=output.schedule.project_end, total_duration_days=output.schedule.total_duration_days,
        conflicts_count=len(output.conflicts), run_ids=[],
    )
    cr.plan_version_id = version.id
    return {"plan_version": version.version, "plan_version_id": version.id, "staged": after}
