"""data models

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-16 00:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "data_models",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("workspace_id", sa.String(length=32), nullable=False),
        sa.Column("run_id", sa.String(length=32), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("nodes", sa.JSON(), nullable=False),
        sa.Column("last_build", sa.JSON(), nullable=True),
        sa.Column("built_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_data_models_workspace_id"), "data_models", ["workspace_id"], unique=False)
    with op.batch_alter_table("datasets") as batch:
        batch.add_column(sa.Column("data_model_id", sa.String(length=32), nullable=True))
        batch.create_foreign_key(
            "fk_datasets_data_model_id", "data_models", ["data_model_id"], ["id"], ondelete="SET NULL"
        )


def downgrade() -> None:
    with op.batch_alter_table("datasets") as batch:
        batch.drop_constraint("fk_datasets_data_model_id", type_="foreignkey")
        batch.drop_column("data_model_id")
    op.drop_index(op.f("ix_data_models_workspace_id"), table_name="data_models")
    op.drop_table("data_models")
