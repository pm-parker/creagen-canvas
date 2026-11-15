"""drop users.balance：假余额列物理移除（D2 债务清理）。

前序裁定：balance 全库零写入（纯装饰），返回假余额违背信任原则（W1）。
UI/response_model 已于 0.7.x 摘除，本迁移完成模型层与库表层的最终清理。
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a9b8c7d6e5f4"
down_revision: Union[str, Sequence[str], None] = "f4a5b6c7d8e0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("users", "balance")


def downgrade() -> None:
    op.add_column("users", sa.Column("balance", sa.Float(), nullable=True))
