"""reports, schedules, and alerts

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-16 08:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "schedules",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("workspace_id", sa.String(length=32), nullable=False),
        sa.Column("source_run_id", sa.String(length=32), nullable=True),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("intent", sa.String(length=50), nullable=False),
        sa.Column("plan", sa.JSON(), nullable=False),
        sa.Column("dataset_ids", sa.JSON(), nullable=False),
        sa.Column("cadence", sa.String(length=10), nullable=False),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("hour", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_id", sa.String(length=32), nullable=True),
        sa.Column("created_by", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["last_run_id"], ["runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_run_id"], ["runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_schedules_workspace_id"), "schedules", ["workspace_id"], unique=False)
    op.create_index(op.f("ix_schedules_next_run_at"), "schedules", ["next_run_at"], unique=False)

    with op.batch_alter_table("runs") as batch:
        batch.add_column(sa.Column("schedule_id", sa.String(length=32), nullable=True))
        batch.create_foreign_key(
            "fk_runs_schedule_id", "schedules", ["schedule_id"], ["id"], ondelete="SET NULL"
        )
        batch.create_index(batch.f("ix_runs_schedule_id"), ["schedule_id"], unique=False)

    op.create_table(
        "alerts",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("workspace_id", sa.String(length=32), nullable=False),
        sa.Column("run_id", sa.String(length=32), nullable=False),
        sa.Column("schedule_id", sa.String(length=32), nullable=True),
        sa.Column("artifact_id", sa.String(length=32), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("acknowledged_by", sa.String(length=32), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["acknowledged_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["schedule_id"], ["schedules.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_alerts_workspace_id"), "alerts", ["workspace_id"], unique=False)
    op.create_index(op.f("ix_alerts_run_id"), "alerts", ["run_id"], unique=False)
    op.create_index(op.f("ix_alerts_status"), "alerts", ["status"], unique=False)

    op.create_table(
        "reports",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("workspace_id", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("insight_ids", sa.JSON(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_by", sa.String(length=32), nullable=True),
        sa.Column("share_token", sa.String(length=64), nullable=True),
        sa.Column("created_by", sa.String(length=32), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["published_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("share_token", name="uq_reports_share_token"),
    )
    op.create_index(op.f("ix_reports_workspace_id"), "reports", ["workspace_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_reports_workspace_id"), table_name="reports")
    op.drop_table("reports")
    op.drop_index(op.f("ix_alerts_status"), table_name="alerts")
    op.drop_index(op.f("ix_alerts_run_id"), table_name="alerts")
    op.drop_index(op.f("ix_alerts_workspace_id"), table_name="alerts")
    op.drop_table("alerts")
    with op.batch_alter_table("runs") as batch:
        batch.drop_index(batch.f("ix_runs_schedule_id"))
        batch.drop_constraint("fk_runs_schedule_id", type_="foreignkey")
        batch.drop_column("schedule_id")
    op.drop_index(op.f("ix_schedules_next_run_at"), table_name="schedules")
    op.drop_index(op.f("ix_schedules_workspace_id"), table_name="schedules")
    op.drop_table("schedules")
