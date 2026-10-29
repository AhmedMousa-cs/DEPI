import os
import sys
from pathlib import Path

# Make "app" importable when pytest is executed from backend/.
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.config import settings


def build_test_database_url():
    """
    Build a safe PostgreSQL URL for tests.

    Priority:
    1. TEST_DATABASE_URL, if explicitly provided.
    2. DATABASE_URL/settings.database_url.

    The database name is always forced to nexus_test.
    """

    raw_url = (
        os.environ.get("TEST_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or settings.database_url
    )

    url = make_url(raw_url)

    # Tests must never run against the normal development database.
    url = url.set(database="nexus_test")

    # When pytest runs on the host and PostgreSQL runs in Docker,
    # the Docker hostname "db" must be changed to localhost.
    running_inside_docker = os.environ.get("RUN_TESTS_IN_DOCKER") == "1"

    if url.host == "db" and not running_inside_docker:
        url = url.set(host="localhost")

    database_name = url.database or ""

    if not database_name.endswith("_test"):
        raise RuntimeError(
            "Refusing to run tests on a non-test database. "
            f"Resolved database: {database_name!r}"
        )

    if not url.host:
        raise RuntimeError(
            "TEST_DATABASE_URL must include a PostgreSQL host. "
            f"Resolved URL: {url.render_as_string(hide_password=True)}"
        )

    return url


TEST_DATABASE_URL = build_test_database_url()

# Make the selected URL visible to all application modules.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL.render_as_string(
    hide_password=False
)

# app.db.session creates its engine from settings.database_url.
settings.database_url = TEST_DATABASE_URL.render_as_string(
    hide_password=False
)

from app.db.session import SessionLocal, engine  # noqa: E402
from app.seed import seed  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def migrate_and_seed_test_database():
    """
    Rebuild the test schema from the real Alembic migrations,
    then insert deterministic seed users.
    """

    alembic_config = Config(str(BACKEND_DIR / "alembic.ini"))

    alembic_config.set_main_option(
        "script_location",
        str(BACKEND_DIR / "alembic"),
    )

    # Explicitly override the migration connection URL.
    alembic_config.set_main_option(
        "sqlalchemy.url",
        TEST_DATABASE_URL.render_as_string(hide_password=False),
    )

    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")

    seed()

    yield


@pytest.fixture(autouse=True)
def clean_business_data():
    """
    Keep seeded users but clean data created by each test.
    """

    yield

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                TRUNCATE
                    projects,
                    project_members,
                    team_members,
                    sources,
                    jobs,
                    audit_log
                RESTART IDENTITY CASCADE
                """
            )
        )


@pytest.fixture
def db_session():
    """
    Provide a transaction-scoped SQLAlchemy session.
    """

    connection = engine.connect()
    transaction = connection.begin()
    session = SessionLocal(bind=connection)

    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
