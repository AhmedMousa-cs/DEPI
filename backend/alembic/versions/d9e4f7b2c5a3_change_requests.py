"""change_requests: tracked edits to approved items (M11 change management).

Revision ID: d9e4f7b2c5a3
Revises: a7c3e1d9f2b8
Create Date: 2026-11-18 10:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = "d9e4f7b2c5a3"
# IMPORTANT: must be the CURRENT head. Check with:  alembic heads
down_revision = "a7c3e1d9f2b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A change request is the PM's formal, audited edit of an APPROVED item.
    # Agents never create or approve these. Impact is computed by code at
    # proposal time and stored so the decision is made with the numbers.
    op.create_table(
        "change_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("kind", sa.String(40), nullable=False),  # "task_update"
        sa.Column("target_task_id", sa.Integer(), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),  # {"before": {...}, "after": {...}}
        sa.Column("impact", sa.JSON(), nullable=False, server_default="{}"),  # deterministic analysis
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft", index=True),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("reviewed_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("change_requests")
