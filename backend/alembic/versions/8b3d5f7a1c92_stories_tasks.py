"""user stories, tasks and task dependencies (M5 / US03)

Revision ID: 8b3d5f7a1c92
Revises: 7c1d4e9a2b53
Create Date: 2026-11-13 12:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = "8b3d5f7a1c92"
# IMPORTANT: must be the CURRENT head. Check with:  alembic heads
down_revision = "7c1d4e9a2b53"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_stories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("requirement_id", sa.Integer(), sa.ForeignKey("requirements.id", ondelete="CASCADE"), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("acceptance_criteria", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("review_status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("locked_by_pm", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("pm_comment", sa.Text(), nullable=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by_agent", sa.String(50), nullable=True),
        sa.Column("reviewed_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_user_stories_project_id", "user_stories", ["project_id"])
    op.create_index("ix_user_stories_requirement_id", "user_stories", ["requirement_id"])
    op.create_index("ix_user_stories_review_status", "user_stories", ["review_status"])

    op.create_table(
        "tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("story_id", sa.Integer(), sa.ForeignKey("user_stories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("estimate_hours", sa.Float(), nullable=False),
        sa.Column("assignee_member_id", sa.Integer(), sa.ForeignKey("team_members.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="todo"),
        sa.Column("progress_pct", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("start", sa.Date(), nullable=True),
        sa.Column("end", sa.Date(), nullable=True),
        sa.Column("is_critical", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("review_status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("locked_by_pm", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("pm_comment", sa.Text(), nullable=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by_agent", sa.String(50), nullable=True),
        sa.Column("reviewed_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_tasks_project_id", "tasks", ["project_id"])
    op.create_index("ix_tasks_story_id", "tasks", ["story_id"])
    op.create_index("ix_tasks_status", "tasks", ["status"])
    op.create_index("ix_tasks_review_status", "tasks", ["review_status"])

    op.create_table(
        "task_dependencies",
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True),
        sa.Column(
            "depends_on_task_id",
            sa.Integer(),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.CheckConstraint("task_id <> depends_on_task_id", name="ck_task_dependency_not_self"),
    )


def downgrade() -> None:
    op.drop_table("task_dependencies")
    op.drop_index("ix_tasks_review_status", table_name="tasks")
    op.drop_index("ix_tasks_status", table_name="tasks")
    op.drop_index("ix_tasks_story_id", table_name="tasks")
    op.drop_index("ix_tasks_project_id", table_name="tasks")
    op.drop_table("tasks")
    op.drop_index("ix_user_stories_review_status", table_name="user_stories")
    op.drop_index("ix_user_stories_requirement_id", table_name="user_stories")
    op.drop_index("ix_user_stories_project_id", table_name="user_stories")
    op.drop_table("user_stories")
