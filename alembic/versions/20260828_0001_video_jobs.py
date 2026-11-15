"""video_jobs 占位迁移：生产库历史版本（文件失传回流）。

生产库 alembic_version 曾推进到本版本；对应文件在开源仓库化时失传。
本文件为空操作占位，仅用于恢复 alembic 链连续性（upgrade/downgrade 均 no-op）。
真实作用域仅存在于生产库历史的 video jobs 结构，开源版代码不依赖。

Revision ID: 20260828_0001_video_jobs
Revises: c9d0e1f2a3b4
"""

from typing import Sequence, Union

revision: str = "20260828_0001_video_jobs"
down_revision: Union[str, Sequence[str], None] = "c9d0e1f2a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
