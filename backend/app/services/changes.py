"""M11 change management: formal, audited edits to APPROVED tasks.

Once the PM approves a task it is locked — editing it is a CHANGE, not a silent
mutation. The flow:

  propose (PM)  -> impact is computed by CODE from live data (date shift,
                   critical path, dependent tasks, assignee load, project end)
  decide  (PM)  -> approved: the change is applied and audited
                   rejected: recorded and closed, nothing changes

Agents never create or decide changes; every state transition writes an audit
row with before/after.
"""
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.db.models import ChangeRequest, Task, TaskDependency, TeamMember


class ChangeError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        self.code, self.message, self.status_code = code, message, status_code
        super().__init__(message)


EDITABLE_FIELDS = ("title", "estimate_hours", "start", "end", "assignee_member_id")


def _snapshot(task: Task) -> dict:
    return {
        "title": task.title,
        "estimate_hours": float(task.estimate_hours),
        "start": task.start.isoformat() if task.start else None,
        "end": task.end.isoformat() if task.end else None,
        "assignee_member_id": task.assignee_member_id,
    }


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
                "message": f"critical-path task delayed by {delta} day(s) — the project end moves with it",
            })
        else:
            impacts.append({
                "severity": "medium",
                "message": f"task end shifted +{delta} day(s)",
            })
        # downstream dependents must move too
        dep_ids = list(db.scalars(
            select(TaskDependency.task_id).where(TaskDependency.depends_on_task_id == task.id)
        ))
        dependents = [
            t for t in db.scalars(select(Task).where(Task.id.in_(dep_ids)))
            if t.review_status == "approved" and t.start
        ]
        for dep in dependents:
            impacts.append({
                "severity": "medium" if not dep.is_critical else "high",
                "message": f"dependent task #{dep.id} '{dep.title}' would need its window shifted +{delta} day(s)",
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
    task = db.get(Task, task_id)
    if task is None or task.project_id != project_id:
        raise ChangeError("task_not_found", "Task not found", 404)
    if task.review_status != "approved":
        raise ChangeError(
            "task_not_approved",
            "Only approved tasks go through change management — edit drafts directly in review",
            409,
        )

    before = _snapshot(task)
    # keep only fields whose value actually changes (honest diff)
    clean = {k: v for k, v in after.items() if k in EDITABLE_FIELDS and v is not None and v != before[k]}
    if not clean:
        raise ChangeError("no_changes", "The proposal changes nothing", 422)
    # impact analysis works with real date objects (payload keeps ISO strings)
    norm = dict(clean)
    for f in ("start", "end"):
        if isinstance(norm.get(f), str):
            norm[f] = date.fromisoformat(norm[f])
    impact = analyze_task_change(db, task, norm)

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


def _apply(db: Session, task: Task, after: dict) -> None:
    if "title" in after:
        task.title = after["title"]
    if "estimate_hours" in after:
        task.estimate_hours = float(after["estimate_hours"])
    if "start" in after:
        task.start = date.fromisoformat(after["start"]) if after["start"] else None
    if "end" in after:
        task.end = date.fromisoformat(after["end"]) if after["end"] else None
    if "assignee_member_id" in after:
        task.assignee_member_id = after["assignee_member_id"]


def decide_change(
    db: Session, *, project_id: int, change_id: int, decision: str,
    comment: str | None, pm,
) -> ChangeRequest:
    cr = db.get(ChangeRequest, change_id)
    if cr is None or cr.project_id != project_id:
        raise ChangeError("change_not_found", "Change request not found", 404)
    if cr.status != "draft":
        raise ChangeError("already_decided", f"Change request is already {cr.status}", 409)

    before_state = _snapshot(db.get(Task, cr.target_task_id)) if cr.target_task_id else None
    cr.status = "approved" if decision == "approved" else "rejected"
    cr.reviewed_by = pm.id
    cr.reviewed_at = datetime.now(timezone.utc)

    applied = None
    if decision == "approved" and cr.target_task_id:
        task = db.get(Task, cr.target_task_id)
        _apply(db, task, cr.payload["after"])
        applied = _snapshot(task)

    write_audit(
        db,
        actor=pm.email,
        action="change.decision",
        entity_type="change_request",
        entity_id=cr.id,
        before={"status": "draft", **({"task": before_state} if before_state else {})},
        after={
            "status": cr.status,
            "decision": decision,
            **({"applied": applied} if applied else {}),
            "comment": comment or None,
        },
    )
    return cr
