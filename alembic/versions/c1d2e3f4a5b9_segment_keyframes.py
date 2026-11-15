"""分镜关键帧层（0.9.0-M5）：keyframe_url / keyframe_task_id nullable 列。

revision: c1d2e3f4a5b9
down: b0c1d2e3f4a5（segment target_duration）
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c1d2e3f4a5b9"
down_revision: str | Sequence[str] | None = "b0c1d2e3f4a5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("storyboard_segments", sa.Column("keyframe_url", sa.String(500), nullable=True))
    op.add_column("storyboard_segments", sa.Column("keyframe_task_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("storyboard_segments", "keyframe_task_id")
    op.drop_column("storyboard_segments", "keyframe_url")
