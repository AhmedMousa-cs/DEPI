"""Link accepted task change requests to plan versions.

Revision ID: f2a7c9d4e6b8
Revises: 9d4e6a8c1b25
"""
import sqlalchemy as sa
from alembic import op

revision = "f2a7c9d4e6b8"
down_revision = "9d4e6a8c1b25"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("change_requests", sa.Column("plan_version_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_change_requests_plan_version", "change_requests", "plan_versions", ["plan_version_id"], ["id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint("uq_change_requests_plan_version_id", "change_requests", ["plan_version_id"])


def downgrade() -> None:
    op.drop_constraint("uq_change_requests_plan_version_id", "change_requests", type_="unique")
    op.drop_constraint("fk_change_requests_plan_version", "change_requests", type_="foreignkey")
    op.drop_column("change_requests", "plan_version_id")
