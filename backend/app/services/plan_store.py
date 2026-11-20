"""Database-backed PlanStore for Agent 4's tools. Every write is draft-only (see plan_versions)."""
from __future__ import annotations

from sqlalchemy import select

from app.db.models import PlanVersion, Project, Risk, Source, TeamMember
from app.db.session import SessionLocal
from app.schemas.plan import PlanReviewDraft, RiskDraft
from app.services import plan_versions as pvs
from app.services.injection_guard import detect_injection, wrap_untrusted

AGENT_CREATOR = "agent4_risk"
BRIEF_CHARS = 3000


def _row(r: Risk) -> dict:
    return {"id": r.id, "title": r.title, "severity": r.severity, "likelihood": r.likelihood,
            "status": r.status, "created_by": r.created_by}


class DbPlanStore:
    def _version(self, db, project_id: int, plan_version_id: int) -> PlanVersion:
        pv = db.get(PlanVersion, plan_version_id)
        if pv is None or pv.project_id != project_id:
            raise pvs.PlanVersionError("plan version not found in this project")
        return pv

    def snapshot(self, project_id: int, plan_version_id: int) -> dict:
        with SessionLocal() as db:
            return self._version(db, project_id, plan_version_id).snapshot

    def brief(self, project_id: int) -> dict:
        with SessionLocal() as db:
            p = db.get(Project, project_id)
            if p is None:
                return {"error": "project not found"}
            sources = list(db.scalars(select(Source).where(Source.project_id == project_id, Source.kind == "brief")
                                      .order_by(Source.id)))
            text = "\n".join(s.content_text or "" for s in sources)
            return {
                "project": {"name": p.name, "goal": p.goal, "status": p.status,
                            "start_date": p.start_date.isoformat() if p.start_date else None,
                            "target_end_date": p.end_date.isoformat() if p.end_date else None},
                "brief_untrusted": [wrap_untrusted(source_id=s.id, kind="brief", text=(s.content_text or "")[:BRIEF_CHARS])
                                    for s in sources],
                "injection_flags": detect_injection(text),
            }

    def team(self, project_id: int) -> list[dict]:
        with SessionLocal() as db:
            return [{"id": m.id, "name": m.name, "skills": list(m.skills or []),
                     "available_from": m.available_from.isoformat() if m.available_from else None,
                     "available_to": m.available_to.isoformat() if m.available_to else None}
                    for m in db.scalars(select(TeamMember).where(TeamMember.project_id == project_id))]

    def open_risks(self, project_id: int, plan_version_id: int) -> dict:
        with SessionLocal() as db:
            self._version(db, project_id, plan_version_id)
            approved = db.scalars(select(Risk).where(Risk.project_id == project_id, Risk.status == "open").order_by(Risk.id))
            return {"approved_plan_risks": [_row(r) for r in approved],
                    "draft_risks": [_row(r) for r in pvs.risks_of(db, plan_version_id)]}

    def diff(self, project_id: int, plan_version_id: int, against: str) -> dict:
        with SessionLocal() as db:
            pv = self._version(db, project_id, plan_version_id)
            if against == "approved":
                base = pvs.current_approved(db, project_id)
                if base is None:
                    return {"initial": True, "summary": {"changes": 0}, "changes": [], "note": "no approved version yet"}
                return {"against_version": base.version, **pvs.diff_snapshots(base.snapshot, pv.snapshot)}
            return {"against_version": pv.base_version, **(pv.diff_from_prev or {})}

    def save_agent_risks(self, project_id: int, plan_version_id: int, risks: list[RiskDraft], run_id: int | None) -> dict:
        with SessionLocal() as db:
            out = pvs.save_draft_risks(db, project_id=project_id, plan_version_id=plan_version_id, risks=risks,
                                       created_by=AGENT_CREATOR, run_id=run_id, replace=True)
            db.commit()
            return {"saved": len(out.saved), "skipped_duplicates": out.skipped_duplicates,
                    "risk_ids": [r.id for r in out.saved]}

    def save_rule_risks(self, project_id: int, plan_version_id: int, findings: list, run_id: int | None) -> int:
        """Replace this version's rule risks with the current findings (idempotent)."""
        with SessionLocal() as db:
            pvs.clear_draft_risks(db, plan_version_id=plan_version_id, created_by_prefix="rule:")
            n = 0
            for f in findings:
                n += len(pvs.save_draft_risks(db, project_id=project_id, plan_version_id=plan_version_id,
                                              risks=[f.risk], created_by=f.created_by, run_id=run_id,
                                              actor="agent:agent4_rules").saved)
            db.commit()
            return n

    def save_review(self, project_id: int, plan_version_id: int, review: PlanReviewDraft, source: str,
                    run_id: int | None) -> dict:
        with SessionLocal() as db:
            pvs.save_review(db, project_id=project_id, plan_version_id=plan_version_id, review=review,
                            source=source, run_id=run_id)
            db.commit()
        return {"saved": True, "verdict": review.verdict}
