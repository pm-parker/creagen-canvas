"""分镜逐段目标时长（0.8.1-M4）：storyboard_segments.target_duration_ms nullable 列。

revision: b0c1d2e3f4a5
down: a1b2c3d4e5f7（tour_completed）
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b0c1d2e3f4a5"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "storyboard_segments",
        sa.Column("target_duration_ms", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("storyboard_segments", "target_duration_ms")
