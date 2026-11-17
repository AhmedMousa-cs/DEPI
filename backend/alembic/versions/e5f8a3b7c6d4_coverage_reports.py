"""coverage_reports: LLM-judged coverage-gap report (M4.1)

Revision ID: e5f8a3b7c6d4
Revises: d4b7e2c95a61
Create Date: 2026-11-15 21:30:00
"""
import sqlalchemy as sa
from alembic import op

revision = "e5f8a3b7c6d4"
# IMPORTANT: must be the CURRENT head. Check with:  alembic heads
down_revision = "d4b7e2c95a61"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "coverage_reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by_agent", sa.String(50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("coverage_reports")
