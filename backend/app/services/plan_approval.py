"""The ONLY code that can turn a plan version into 'approved' (called by the approval gate).

Everything happens in one transaction with the project and version rows locked, and every
precondition is re-checked inside it, so a stale or concurrent request can't approve the wrong thing.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.db.models import Decision, PlanVersion, Project, Risk, Task
from app.services import plan_versions as pvs

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


def approval_blockers(db: Session, project: Project | None, pv: PlanVersion | None, acknowledge: bool) -> dict | None:
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
    if pvs.inputs_fingerprint(pvs.load_plan_inputs(db, pv.project_id)) != pv.snapshot.get("inputs_fingerprint"):
        return {"code": "inputs_changed",
                "message": "Tasks, dependencies or the team changed after this draft was built. Rebuild the plan."}
    warnings = plan_warnings(pv.snapshot)
    if warnings and not acknowledge:
        return {"code": "warnings_not_acknowledged", "message": "Acknowledge the warnings to approve this plan.",
                "warnings": warnings}
    return None


def _decision(db: Session, pv: PlanVersion, decision: str, reason: str, user_id: int) -> None:
    db.add(Decision(project_id=pv.project_id, subject_type="plan_version", subject_id=pv.id, decision=decision,
                    reason=reason or None, decided_by=user_id))


def approve_plan(db: Session, *, project_id: int, version: int, decided_by: int, comment: str,
                 acknowledge_warnings: bool, run_id: str | None = None) -> dict:
    """Apply an approval decided by the PM. Returns {"written": bool, ...reason...}. Caller commits."""
    actor = f"user:{decided_by}"
    project = db.scalar(select(Project).where(Project.id == project_id).with_for_update())
    pv = pvs.get_version(db, project_id, version, lock=True)
    blocker = approval_blockers(db, project, pv, acknowledge_warnings)
    if blocker is not None:
        write_audit(db, actor, "plan.approval_blocked", "plan_version", pv.id if pv else None,
                    after={**blocker, "project_id": project_id, "version": version, "run_id": run_id})
        return {"written": False, **blocker}

    snap = pv.snapshot
    applied = 0
    for t in snap["tasks"]:
        row = db.get(Task, t["id"])
        if row is None or row.project_id != project_id:
            continue
        row.start, row.end = date.fromisoformat(t["start"]), date.fromisoformat(t["end"])
        row.is_critical = bool(t["is_critical"])
        row.assignee_member_id = t["assignee_member_id"]
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
                       "superseded_versions": older, "run_id": run_id})
    return {"written": True, "tasks_applied": applied}


def reject_plan(db: Session, *, project_id: int, version: int, decided_by: int, comment: str,
                run_id: str | None = None) -> dict:
    actor = f"user:{decided_by}"
    project = db.scalar(select(Project).where(Project.id == project_id).with_for_update())
    pv = pvs.get_version(db, project_id, version, lock=True)
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
