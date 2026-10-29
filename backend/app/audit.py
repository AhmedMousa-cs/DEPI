from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.db.models import AuditLog, User


def user_actor(user: User) -> str:
    return f"user:{user.id}"


def write_audit(
    db: Session,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: int | str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> AuditLog:
    """
    Add an audit event to the current transaction.

    This function intentionally uses the same names as the AuditLog model:
    entity_type and entity_id.

    It does not commit. The caller owns the transaction, so the audit row
    and the business change succeed or roll back together.
    """

    audit_entry = AuditLog(
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_id=None if entity_id is None else str(entity_id),
        before=before,
        after=after,
    )

    db.add(audit_entry)
    db.flush()

    return audit_entry
