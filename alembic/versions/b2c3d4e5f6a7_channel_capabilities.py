"""渠道能力标志列（局部重绘 /images/edits 等能力开关）。

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-09-28
"""

from alembic import op
import sqlalchemy as sa

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("provider_channels") as batch:
        batch.add_column(
            sa.Column("capabilities", sa.Text(), nullable=False, server_default="[]")
        )


def downgrade() -> None:
    with op.batch_alter_table("provider_channels") as batch:
        batch.drop_column("provider_channels", "capabilities")
