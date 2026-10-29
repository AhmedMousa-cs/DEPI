"""jobs: available_at, heartbeat_at, one active job per (project, type)

Revision ID: 3f9c2b7d1a40
Revises: 6fee481959a8
Create Date: 2026-10-30 09:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "3f9c2b7d1a40"
down_revision = "6fee481959a8"
branch_labels = None
depends_on = None


_ACTIVE_STATUSES = "status IN ('queued','running','waiting_for_pm')"


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.add_column(
        "jobs",
        sa.Column(
            "heartbeat_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    op.create_index(
        "uq_jobs_active_project_type",
        "jobs",
        ["project_id", "type"],
        unique=True,
        postgresql_where=sa.text(_ACTIVE_STATUSES),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_jobs_active_project_type",
        table_name="jobs",
    )
    op.drop_column("jobs", "heartbeat_at")
    op.drop_column("jobs", "available_at")
