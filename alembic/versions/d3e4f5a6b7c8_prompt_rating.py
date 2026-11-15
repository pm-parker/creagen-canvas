"""prompts.rating：共享内容分级标记（内容分级拍板 ①B/②确认，2026-10-07）。

NULL=普通（默认）；"mature"=成人/擦边标记——共享条目列表默认隐藏，
?show_mature=true 显式开关才显示；个人条目不受限（中立自托管立场）。
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d3e4f5a6b7c8"
down_revision: Union[str, Sequence[str], None] = "c2d3e4f5a6b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("prompts", sa.Column("rating", sa.String(10), nullable=True))


def downgrade() -> None:
    op.drop_column("prompts", "rating")
