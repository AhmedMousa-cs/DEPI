"""jobs queue columns and active-job uniqueness

Revision ID: 3f9c2b7d1a40
Revises: 6fee481959a8
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "3f9c2b7d1a40"
down_revision = "6fee481959a8"
branch_labels = None
depends_on = None


ACTIVE_STATUSES = "status IN ('queued','running','waiting_for_pm')"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    existing_columns = {
        column["name"]
        for column in inspector.get_columns("jobs")
    }

    if "available_at" not in existing_columns:
        op.add_column(
            "jobs",
            sa.Column(
                "available_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
        )

    if "heartbeat_at" not in existing_columns:
        op.add_column(
            "jobs",
            sa.Column(
                "heartbeat_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
        )

    existing_indexes = {
        index["name"]
        for index in inspect(bind).get_indexes("jobs")
    }

    if "uq_jobs_active_project_type" not in existing_indexes:
        op.create_index(
            "uq_jobs_active_project_type",
            "jobs",
            ["project_id", "type"],
            unique=True,
            postgresql_where=sa.text(ACTIVE_STATUSES),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    existing_indexes = {
        index["name"]
        for index in inspector.get_indexes("jobs")
    }

    if "uq_jobs_active_project_type" in existing_indexes:
        op.drop_index(
            "uq_jobs_active_project_type",
            table_name="jobs",
        )

    existing_columns = {
        column["name"]
        for column in inspect(bind).get_columns("jobs")
    }

    if "heartbeat_at" in existing_columns:
        op.drop_column("jobs", "heartbeat_at")

    if "available_at" in existing_columns:
        op.drop_column("jobs", "available_at")
