"""Agent-side persistence for requirements.

This is the ONLY path from agent output to the requirements table, and it can only create
status="draft" rows (there is deliberately no status parameter). Approval happens in
app/api/review.py, behind the PM role.

It does not commit: the caller owns the transaction (same convention as app.audit.write_audit).
"""
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.db.models import Requirement, Source
from app.services.quotes import VerifiedRequirement, normalize

AGENT_NAME = "agent1_extract"


def save_draft_requirements(
    db: Session,
    *,
    project_id: int,
    items: Sequence[VerifiedRequirement],
    run_id: int | None = None,
    agent: str = AGENT_NAME,
) -> list[Requirement]:
    """Replace the project's untouched drafts with the new ones.

    - Items the PM already decided on (locked_by_pm or status != draft) are never overwritten,
      and a new item with the same source+quote is skipped instead of duplicated.
    - A source_id that does not belong to this project is stored as NULL (never trust the LLM).
    """
    valid_sources = set(db.scalars(select(Source.id).where(Source.project_id == project_id)))
    existing = list(db.scalars(select(Requirement).where(Requirement.project_id == project_id)))

    protected = {
        (r.source_id, normalize(r.quote or ""))
        for r in existing
        if r.locked_by_pm or r.status != "draft"
    }
    for r in existing:
        if r.status == "draft" and not r.locked_by_pm:
            db.delete(r)
    db.flush()

    created: list[Requirement] = []
    skipped = 0
    for item in items:
        req = item.requirement
        source_id = req.source_id if req.source_id in valid_sources else None
        key = (source_id, normalize(req.exact_quote))
        if key in protected:
            skipped += 1
            continue
        protected.add(key)
        row = Requirement(
            project_id=project_id,
            type=req.type,
            text=req.text,
            source_id=source_id,
            quote=req.exact_quote,
            quote_verified=item.quote_verified,
            confidence=item.confidence,  # already capped for unverified quotes
            run_id=run_id,
            created_by_agent=agent,
            status="draft",
            locked_by_pm=False,
        )
        db.add(row)
        created.append(row)
    db.flush()

    write_audit(
        db,
        f"agent:{agent}",
        "requirements.drafts_saved",
        "project",
        project_id,
        after={"created": len(created), "skipped_protected": skipped, "run_id": run_id},
    )
    return created