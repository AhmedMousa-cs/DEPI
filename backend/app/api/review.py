from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.common import get_project_or_404
from app.audit import user_actor, write_audit
from app.auth.dependencies import require_role
from app.db.models import AgentRun, Decision, Requirement, User
from app.db.session import get_db
from app.observability.langfuse import send_pm_score
from app.services.coverage_gaps import latest_gap_payload, latest_gap_report, project_gaps, run_llm_gap_analysis
from app.schemas.review import (
    BulkRequirementDecision,
    ReqStatus,
    RequirementDecision,
    RequirementOut,
)

# Reviewing agent output is PM-only (Blueprint section 3).
router = APIRouter(prefix="/projects/{project_id}/requirements", tags=["requirements"])


def _snapshot(r: Requirement) -> dict:
    return {
        "status": r.status,
        "type": r.type,
        "text": r.text,
        "priority": r.priority,
        "module": r.module,
        "locked_by_pm": r.locked_by_pm,
    }


def _apply_decision(req: Requirement, *, decision: str, comment: str | None, user: User) -> None:
    """The shared PM-decision write (used by the single and bulk endpoints). Caller adds audit/Decision."""
    req.status = "rejected" if decision == "rejected" else "approved"
    req.locked_by_pm = True  # any PM decision protects the item from being overwritten by a re-run
    req.pm_comment = comment or None
    req.reviewed_by = user.id
    req.reviewed_at = datetime.now(timezone.utc)


def _load(db: Session, project_id: int, requirement_id: int, *, lock: bool = False) -> Requirement:
    # Filtering by project_id too: an id from another project is a 404, not a leak.
    stmt = select(Requirement).where(
        Requirement.id == requirement_id, Requirement.project_id == project_id
    )
    if lock:
        stmt = stmt.with_for_update()
    req = db.scalar(stmt)
    if req is None:
        raise HTTPException(404, "Requirement not found")
    return req


@router.get("", response_model=list[RequirementOut])
def list_requirements(
    project_id: int,
    status: ReqStatus | None = None,
    verified: bool | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("PM")),
):
    get_project_or_404(db, project_id)
    stmt = select(Requirement).where(Requirement.project_id == project_id)
    if status is not None:
        stmt = stmt.where(Requirement.status == status)
    if verified is not None:
        stmt = stmt.where(Requirement.quote_verified == verified)
    return list(db.scalars(stmt.order_by(Requirement.id)))


@router.get("/gaps", response_model=dict)
def requirements_gaps(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("PM")),
):
    """Fresh keyword rows, overlaid with the latest stored LLM verdict (if any)."""
    get_project_or_404(db, project_id)
    result = project_gaps(db, project_id)
    saved = latest_gap_payload(db, project_id)
    if saved:
        llm_rows = {a["area"]: a for a in saved.get("areas", []) if a.get("source") == "llm"}
        for row in result["areas"]:
            llm = llm_rows.get(row["area"])
            if llm:
                row.update(
                    covered=llm["covered"], matched_ids=llm["matched_ids"],
                    note=llm.get("note", ""), source="llm",
                )
        missing = [row["area"] for row in result["areas"] if not row["covered"]]
        result["covered_count"] = len(result["areas"]) - len(missing)
        result["missing_count"] = len(missing)
        result["missing"] = missing
        result["llm"] = latest_gap_report(db, project_id)
    else:
        result["llm"] = None
    return result


@router.post("/gaps/analyze", response_model=dict)
def analyze_gaps(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("PM")),
):
    """Run the LLM coverage analysis now (meaning-based), persist it, and return the combined report."""
    get_project_or_404(db, project_id)
    try:
        result = run_llm_gap_analysis(db, project_id)
    except ValueError as e:
        raise HTTPException(409, str(e))
    db.commit()
    return result


@router.get("/{requirement_id}", response_model=RequirementOut)
def get_requirement(
    project_id: int,
    requirement_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("PM")),
):
    get_project_or_404(db, project_id)
    return _load(db, project_id, requirement_id)


@router.post("/decision", response_model=list[RequirementOut])
def decide_requirements_bulk(
    project_id: int,
    body: BulkRequirementDecision,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("PM")),
):
    """One decision for many requirements (module-level review). One transaction for the whole batch."""
    get_project_or_404(db, project_id)
    wanted = list(dict.fromkeys(body.ids))
    rows = {
        r.id: r
        for r in db.scalars(
            select(Requirement)
            .where(Requirement.project_id == project_id, Requirement.id.in_(wanted))
            .with_for_update()
        )
    }
    missing = [rid for rid in wanted if rid not in rows]
    if missing:
        raise HTTPException(404, f"Requirement(s) not found in this project: {missing}")

    updated: list[Requirement] = []
    for rid in wanted:
        req = rows[rid]
        before = _snapshot(req)
        _apply_decision(req, decision=body.decision, comment=body.comment, user=user)
        db.add(
            Decision(
                project_id=project_id,
                subject_type="requirement",
                subject_id=req.id,
                decision=body.decision,
                reason=body.comment or None,
                decided_by=user.id,
            )
        )
        write_audit(
            db,
            user_actor(user),
            f"requirement.{body.decision}",
            "requirement",
            req.id,
            before=before,
            after={**_snapshot(req), "project_id": project_id, "bulk": True,
                   "comment": body.comment or None},
        )
        updated.append(req)
    db.commit()
    for req in updated:
        db.refresh(req)
    return updated


@router.post("/{requirement_id}/decision", response_model=RequirementOut)
def decide_requirement(
    project_id: int,
    requirement_id: int,
    body: RequirementDecision,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("PM")),
):
    get_project_or_404(db, project_id)
    req = _load(db, project_id, requirement_id, lock=True)
    before = _snapshot(req)

    if body.decision == "modified":
        if body.text is not None:
            req.text = body.text
        if body.type is not None:
            req.type = body.type
        if body.priority is not None:
            req.priority = body.priority

    _apply_decision(req, decision=body.decision, comment=body.comment, user=user)

    db.add(
        Decision(
            project_id=project_id,
            subject_type="requirement",
            subject_id=req.id,
            decision=body.decision,
            reason=body.comment or None,
            decided_by=user.id,
        )
    )
    write_audit(
        db,
        user_actor(user),
        f"requirement.{body.decision}",
        "requirement",
        req.id,
        before=before,
        after={**_snapshot(req), "project_id": project_id, "comment": body.comment or None},
    )
    run_id = req.run_id
    db.commit()

    # Best-effort quality signal for Langfuse; it can never fail the request.
    trace_id = db.scalar(select(AgentRun.trace_id).where(AgentRun.id == run_id)) if run_id else None
    send_pm_score(trace_id=trace_id, decision=body.decision, requirement_id=requirement_id)

    db.refresh(req)
    return req