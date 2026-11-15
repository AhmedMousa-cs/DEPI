"""schedule_drafts: Agent 3 draft schedule (M6.1)

Revision ID: c2a9e1f47b35
Revises: 9d5c1e8a2b74
Create Date: 2026-11-15 12:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = "c2a9e1f47b35"
# IMPORTANT: must be the CURRENT head. Check with:  alembic heads
down_revision = "9d5c1e8a2b74"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "schedule_drafts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("run_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("project_start", sa.Date(), nullable=False),
        sa.Column("project_end", sa.Date(), nullable=False),
        sa.Column("total_duration_days", sa.Integer(), nullable=False),
        sa.Column("conflicts_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("review_status", sa.String(20), nullable=False, server_default="draft", index=True),
        sa.Column("pm_comment", sa.Text(), nullable=True),
        sa.Column("created_by_agent", sa.String(50), nullable=True),
        sa.Column("reviewed_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("schedule_drafts")
