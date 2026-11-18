"""Agent 2 v2 fields: plan ordering, critical-path rationale (M5.1)

Revision ID: 9d5c1e8a2b74
Revises: 8b3d5f7a1c92
Create Date: 2026-11-13 13:30:00
"""
import sqlalchemy as sa
from alembic import op

revision = "9d5c1e8a2b74"
# IMPORTANT: must be the CURRENT head. Check with:  alembic heads
down_revision = "8b3d5f7a1c92"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable on purpose: rows written before this change (or by Agent 2 v1) simply have no order.
    op.add_column("user_stories", sa.Column("plan_order", sa.Integer(), nullable=True))
    op.add_column("tasks", sa.Column("plan_order", sa.Integer(), nullable=True))
    op.add_column("tasks", sa.Column("rationale", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "rationale")
    op.drop_column("tasks", "plan_order")
    op.drop_column("user_stories", "plan_order")
