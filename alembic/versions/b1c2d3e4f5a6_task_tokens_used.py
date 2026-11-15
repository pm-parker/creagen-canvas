"""tasks.tokens_used：LLM 真实 token 用量落库（D3 成本看板二批）。

provider usage 透传 → 生命周期写入 → 成本看板展示 token 消耗（LLM 现金成本为 0，
以 token 计量呈现；预算治理 llm_budget 语义不变，仍为提交前预估熔断）。
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, Sequence[str], None] = "a9b8c7d6e5f4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("tokens_used", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "tokens_used")
