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
    before: dict | None = None,
    after: dict | None = None,
) -> AuditLog:
    """Add an audit row to the current session.

    Uses flush, not commit: the row is saved in the same transaction as the
    change it describes, so both succeed or both roll back.
    """
    entry = AuditLog(
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_id=None if entity_id is None else str(entity_id),
        before=before,
        after=after,
    )
    db.add(entry)
    db.flush()
    return entry