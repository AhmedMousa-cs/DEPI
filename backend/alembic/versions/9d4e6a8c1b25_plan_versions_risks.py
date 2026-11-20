"""plan_versions and risks (M6 / US04) — re-chained on the current head after the m6-B merge.

Revision ID: 9d4e6a8c1b25
Revises: d9e4f7b2c5a3
Create Date: 2026-11-14 12:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = "9d4e6a8c1b25"
# IMPORTANT: must be the CURRENT head. Check with:  alembic heads
down_revision = "d9e4f7b2c5a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plan_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("base_version", sa.Integer(), nullable=True),
        sa.Column("diff_from_prev", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.String(60), nullable=False),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("approved_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("project_id", "version", name="uq_plan_versions_project_version"),
    )
    op.create_index("ix_plan_versions_project_id", "plan_versions", ["project_id"])
    op.create_index(
        "uq_plan_versions_one_draft",
        "plan_versions",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("status = 'draft'"),
    )

    op.create_table(
        "risks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("plan_version_id", sa.Integer(), sa.ForeignKey("plan_versions.id", ondelete="CASCADE"), nullable=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(10), nullable=False),
        sa.Column("likelihood", sa.String(10), nullable=False),
        sa.Column("mitigation", sa.Text(), nullable=True),
        sa.Column("evidence_refs", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("created_by", sa.String(60), nullable=False),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_risks_project_id", "risks", ["project_id"])
    op.create_index("ix_risks_plan_version_id", "risks", ["plan_version_id"])
    op.create_index("ix_risks_status", "risks", ["status"])


def downgrade() -> None:
    op.drop_table("risks")
    op.drop_index("uq_plan_versions_one_draft", table_name="plan_versions")
    op.drop_table("plan_versions")
