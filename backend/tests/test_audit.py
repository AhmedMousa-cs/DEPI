from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.audit import user_actor, write_audit
from app.db.base import Base
from app.db.models import AuditLog


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[AuditLog.__table__])
    with sessionmaker(bind=engine)() as session:
        yield session


def count(db):
    return db.scalar(select(func.count()).select_from(AuditLog))


def test_user_actor_format():
    assert user_actor(SimpleNamespace(id=5)) == "user:5"


def test_write_audit_saves_fields(db):
    entry = write_audit(
        db,
        "user:1",
        "project.update",
        "project",
        7,
        before={"name": "old"},
        after={"name": "new"},
    )
    db.commit()
    row = db.get(AuditLog, entry.id)
    assert row.actor == "user:1"
    assert row.action == "project.update"
    assert row.entity_type == "project"
    assert row.entity_id == "7"
    assert row.before == {"name": "old"}
    assert row.after == {"name": "new"}
    assert row.created_at is not None


def test_entity_id_optional(db):
    entry = write_audit(db, "agent:agent1", "job.start", "job")
    assert entry.entity_id is None


def test_rollback_removes_audit_row(db):
    write_audit(db, "user:1", "project.create", "project", 1)
    db.rollback()
    assert count(db) == 0