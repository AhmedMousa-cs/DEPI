"""requirements: module grouping, priority suggestion and clarify questions (M4.1)

Revision ID: d4b7e2c95a61
Revises: c2a9e1f47b35
Create Date: 2026-11-15 18:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = "d4b7e2c95a61"
# IMPORTANT: must be the CURRENT head. Check with:  alembic heads
down_revision = "c2a9e1f47b35"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("requirements", sa.Column("module", sa.String(120), nullable=True))
    op.add_column("requirements", sa.Column("clarify_question", sa.Text(), nullable=True))
    op.create_index("ix_requirements_module", "requirements", ["module"])


def downgrade() -> None:
    op.drop_index("ix_requirements_module", table_name="requirements")
    op.drop_column("requirements", "clarify_question")
    op.drop_column("requirements", "module")
