"""provider_channels.user_id 可空（系统渠道）

对齐生产实况：系统渠道功能上线时列已在生产侧手工置为 nullable 并存有 NULL 行，
本迁移把仓库 schema 与之对齐（幂等，生产重复执行无害）。

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-10-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, Sequence[str], None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "provider_channels",
        "user_id",
        existing_type=sa.Integer(),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "provider_channels",
        "user_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
