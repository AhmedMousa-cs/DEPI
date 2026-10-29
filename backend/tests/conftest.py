import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.config import settings

_override = os.environ.get("TEST_DATABASE_URL")
_url = make_url(_override or settings.database_url)
if not _override:
    _url = _url.set(database="nexus_test")
    if _url.host == "db":
        _url = _url.set(host="localhost")

if not (_url.database or "").endswith("_test"):
    raise RuntimeError(f"Refusing to run tests on non-test database: {_url.database}")
settings.database_url = _url.render_as_string(hide_password=False)

from app.db.session import SessionLocal, engine  # noqa: E402
from app.seed import seed  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _migrated_test_db():
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    seed()
    yield


@pytest.fixture(autouse=True)
def _clean_tables():
    yield
    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE projects, project_members, team_members, sources, "
                "jobs, audit_log RESTART IDENTITY CASCADE"
            )
        )


@pytest.fixture(scope="function")
def db_session():
    connection = engine.connect()
    transaction = connection.begin()
    session = SessionLocal(bind=connection)
    yield session
    session.close()
    transaction.rollback()
    connection.close()
