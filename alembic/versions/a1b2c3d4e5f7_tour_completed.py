"""users.tour_completed——新手引导标记服务端化

Revision ID: a1b2c3d4e5f7
Revises: e5f6a7b8c9d0
Create Date: 2026-10-03
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f7"
down_revision: Union[str, Sequence[str], None] = "20260828_0001_video_jobs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("tour_completed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # 存量用户视为已看过引导（避免升级后被重教育）；仅新用户首次进入画布时引导
    op.execute("UPDATE users SET tour_completed = true")


def downgrade() -> None:
    op.drop_column("users", "tour_completed")
