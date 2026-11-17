"""agent_runs.project_id becomes nullable (M7): the PM assistant chats at portfolio level.

Revision ID: a7c3e1d9f2b8
Revises: e5f8a3b7c6d4
Create Date: 2026-11-17 10:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = "a7c3e1d9f2b8"
# IMPORTANT: must be the CURRENT head. Check with:  alembic heads
down_revision = "e5f8a3b7c6d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Portfolio-level LLM calls (PM assistant chat) are not tied to one project:
    # the run is still budgeted, audited and traced, just without a project anchor.
    op.alter_column(
        "agent_runs", "project_id",
        existing_type=sa.Integer(),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "agent_runs", "project_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
