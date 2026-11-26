"""The ONLY code that can turn a plan version into 'approved' (called by the approval gate).

Everything happens in one transaction with the project and version rows locked, and every
precondition is re-checked inside it, so a stale or concurrent request can't approve the wrong thing.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone

from sqlalchemy import or_, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.db.models import ChangeRequest, Decision, Job, PlanVersion, Project, Risk, Task
from app.services.changes import task_change_baseline
from app.services import plan_versions as pvs
from app.services.jobs import ACTIVE_STATUSES

PLAN_STATUS_FOR_REVIEW = "plan_review"


def plan_warnings(snapshot: dict) -> list[str]:
    """Reasons the PM must explicitly acknowledge before approving."""
    out: list[str] = []
    review = snapshot.get("review") or {}
    if review.get("verdict") == "blocked":
        out.append("Agent 4's verdict is 'blocked'.")
    if snapshot.get("conflicts"):
        out.append(f"{len(snapshot['conflicts'])} resource conflict(s) are unresolved.")
    crit = [r["title"] for r in snapshot.get("risks", []) if r.get("severity") == "critical"]
    if crit:
        out.append(f"{len(crit)} critical risk(s): " + "; ".join(crit[:3]))
    return out


def approval_warnings(pv: PlanVersion) -> tuple[list[str], str | None]:
    warnings = plan_warnings(pv.snapshot)
    if not warnings:
        return warnings, None
    # A count or title can stay the same while the underlying conflict or risk changes.
    # Row identities and review timestamps can change even when the evidence is repeated.
    snapshot = pv.snapshot
    evidence = {
        "project_id": pv.project_id,
        "version": pv.version,
        "warnings": warnings,
        "conflicts": snapshot.get("conflicts", []),
        "critical_risks": [
            {key: value for key, value in risk.items() if key not in ("id", "status")}
            for risk in snapshot.get("risks", []) if risk.get("severity") == "critical"
        ],
        "review": {key: value for key, value in (snapshot.get("review") or {}).items()
                   if key not in ("run_id", "reviewed_at")},
    }
    token = hashlib.sha256(json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return warnings, token


def _linked_change(db: Session, pv: PlanVersion) -> ChangeRequest | None:
    change_id = pv.snapshot.get("change_request_id")
    return db.get(ChangeRequest, change_id) if change_id is not None else None


def approval_blockers(db: Session, project: Project | None, pv: PlanVersion | None, acknowledge: bool,
                      warnings_token: str | None = None, *, lock_inputs: bool = False) -> dict | None:
    """None = approvable. Otherwise {"code", "message", ...} explaining why not."""
    if project is None:
        return {"code": "project_not_found", "message": "Project not found."}
    if project.status != PLAN_STATUS_FOR_REVIEW:
        return {"code": "gate_not_open",
                "message": f"The plan can only be approved while the project is in '{PLAN_STATUS_FOR_REVIEW}' "
                           f"(it is '{project.status}')."}
    if pv is None:
        return {"code": "version_not_found", "message": "Plan version not found."}
    if pv.status != "draft":
        return {"code": "not_a_draft", "message": f"Plan version {pv.version} is '{pv.status}', not a draft."}
    latest = pvs.latest_version(db, pv.project_id)
    if latest is not None and latest.version != pv.version:
        return {"code": "superseded", "message": f"Version {latest.version} replaced this draft."}
    if not pv.snapshot.get("review"):
        return {"code": "not_reviewed", "message": "Agent 4 has not reviewed this draft yet."}
    job_version = Job.params["version"].as_string()
    review_job = db.scalar(select(Job.id).where(
        Job.project_id == pv.project_id, Job.type == "agent4.review", Job.status.in_(ACTIVE_STATUSES),
        or_(job_version.is_(None), job_version == str(pv.version)),
    ).limit(1))
    if review_job is not None:
        return {"code": "review_in_progress", "message": "Wait for Agent 4 to finish reviewing this draft."}
    inputs = pvs.load_plan_inputs(db, pv.project_id, lock=lock_inputs)
    is_change_plan = pv.snapshot.get("change_request_id") is not None
    if not pvs.snapshot_matches_inputs(pv.snapshot, inputs):
        action = "Reject this draft and propose the change again." if is_change_plan else "Rebuild the plan."
        return {"code": "inputs_changed",
                "message": f"The draft no longer matches the project dates, tasks, dependencies or team. {action}"}
    if is_change_plan:
        change = _linked_change(db, pv)
        task = db.get(Task, change.target_task_id) if change is not None and change.target_task_id is not None else None
        planned = next((t for t in pv.snapshot["tasks"] if task is not None and t["id"] == task.id), None)
        if (change is None or change.plan_version_id != pv.id or change.project_id != pv.project_id
                or change.status != "approved" or task is None or task.project_id != pv.project_id
                or task.review_status != "approved" or task_change_baseline(task) != change.payload["before"]
                or planned is None or any(planned.get(k) != value for k, value in change.payload["after"].items())):
            return {"code": "inputs_changed",
                    "message": "The accepted task change no longer matches this draft. Reject the draft and propose the change again."}
    warnings, current_token = approval_warnings(pv)
    if warnings and not acknowledge:
        return {"code": "warnings_not_acknowledged", "message": "Acknowledge the warnings to approve this plan.",
                "warnings": warnings, "warnings_token": current_token}
    if warnings_token != current_token:
        return {"code": "warnings_changed", "message": "Review the current warnings before approving this plan.",
                "warnings": warnings, "warnings_token": current_token}
    return None


def _decision(db: Session, pv: PlanVersion, decision: str, reason: str, user_id: int) -> None:
    db.add(Decision(project_id=pv.project_id, subject_type="plan_version", subject_id=pv.id, decision=decision,
                    reason=reason or None, decided_by=user_id))


def approve_plan(db: Session, *, project_id: int, version: int, decided_by: int, comment: str,
                 acknowledge_warnings: bool, warnings_token: str | None = None, run_id: str | None = None) -> dict:
    """Apply an approval decided by the PM. Returns {"written": bool, ...reason...}. Caller commits."""
    actor = f"user:{decided_by}"
    project = db.scalar(select(Project).where(Project.id == project_id).with_for_update()
                        .execution_options(populate_existing=True))
    pv = None
    # Review and task writers can hold child locks before referencing the project. Fail
    # quickly if a lock is held and release any partial child locks through the savepoint.
    try:
        with db.begin_nested():
            pv = pvs.get_version(db, project_id, version, lock=True, nowait=True)
            blocker = approval_blockers(db, project, pv, acknowledge_warnings, warnings_token, lock_inputs=True)
    except OperationalError as exc:
        if getattr(exc.orig, "sqlstate", None) != "55P03":  # PostgreSQL lock_not_available
            raise
        blocker = {"code": "inputs_busy", "message": "Plan inputs are being updated. Try approving again."}
    if blocker is not None:
        write_audit(db, actor, "plan.approval_blocked", "plan_version", pv.id if pv else None,
                    after={**blocker, "project_id": project_id, "version": version, "run_id": run_id})
        return {"written": False, **blocker}

    snap = pv.snapshot
    change = _linked_change(db, pv)
    applied = 0
    for t in snap["tasks"]:
        row = db.get(Task, t["id"])
        if row is None or row.project_id != project_id:
            continue
        row.start, row.end = date.fromisoformat(t["start"]), date.fromisoformat(t["end"])
        row.is_critical = bool(t["is_critical"])
        row.assignee_member_id = t["assignee_member_id"]
        if change is not None and row.id == change.target_task_id:
            fields = change.payload["after"]
            if "title" in fields:
                row.title = t["title"]
            if "estimate_hours" in fields:
                row.estimate_hours = t["estimate_hours"]
            write_audit(db, actor, "change.published", "change_request", change.id,
                        before=change.payload["before"],
                        after={"plan_version": version, "task_id": row.id, "applied": task_change_baseline(row)})
        applied += 1

    older = [v.id for v in db.scalars(select(PlanVersion).where(
        PlanVersion.project_id == project_id, PlanVersion.status == "approved", PlanVersion.id != pv.id))]
    if older:
        db.execute(update(PlanVersion).where(PlanVersion.id.in_(older)).values(status="superseded"))
        db.execute(update(Risk).where(Risk.plan_version_id.in_(older), Risk.status == "open").values(status="superseded"))
    pv.status = "approved"
    pv.approved_by = decided_by
    pv.approved_at = datetime.now(timezone.utc)
    db.execute(update(Risk).where(Risk.plan_version_id == pv.id, Risk.status == "draft").values(status="open"))
    project.status = "active"
    _decision(db, pv, "approved", comment, decided_by)
    warnings = plan_warnings(snap)
    write_audit(db, actor, "plan.approved", "plan_version", pv.id,
                before={"status": "draft", "project_status": PLAN_STATUS_FOR_REVIEW},
                after={"status": "approved", "project_status": "active", "version": version, "tasks_applied": applied,
                       "acknowledged_warnings": warnings if acknowledge_warnings else [], "comment": comment,
                       "warnings_token": warnings_token, "superseded_versions": older, "run_id": run_id})
    return {"written": True, "tasks_applied": applied}


def reject_plan(db: Session, *, project_id: int, version: int, decided_by: int, comment: str,
                run_id: str | None = None) -> dict:
    actor = f"user:{decided_by}"
    project = db.scalar(select(Project).where(Project.id == project_id).with_for_update())
    try:
        with db.begin_nested():
            pv = pvs.get_version(db, project_id, version, lock=True, nowait=True)
    except OperationalError as exc:
        if getattr(exc.orig, "sqlstate", None) != "55P03":
            raise
        return {"written": False, "code": "inputs_busy", "message": "The plan is being updated. Try rejecting again."}
    if project is None or pv is None or pv.status != "draft" or project.status != PLAN_STATUS_FOR_REVIEW:
        write_audit(db, actor, "plan.rejection_blocked", "plan_version", pv.id if pv else None,
                    after={"project_id": project_id, "version": version, "run_id": run_id})
        return {"written": False, "code": "not_rejectable", "message": "This draft can't be rejected any more."}
    pv.status = "rejected"
    db.execute(update(Risk).where(Risk.plan_version_id == pv.id, Risk.status == "draft").values(status="dismissed"))
    project.status = "active" if pvs.current_approved(db, project_id) is not None else "planning"
    _decision(db, pv, "rejected", comment, decided_by)
    write_audit(db, actor, "plan.rejected", "plan_version", pv.id,
                before={"status": "draft"}, after={"status": "rejected", "version": version,
                                                   "project_status": project.status, "comment": comment,
                                                   "run_id": run_id})
    return {"written": True}
