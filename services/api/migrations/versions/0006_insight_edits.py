"""keep the original title and next step of an edited insight

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-19 08:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("insights") as batch:
        batch.add_column(sa.Column("original_title", sa.String(length=300), nullable=True))
        batch.add_column(sa.Column("original_action", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("insights") as batch:
        batch.drop_column("original_action")
        batch.drop_column("original_title")
