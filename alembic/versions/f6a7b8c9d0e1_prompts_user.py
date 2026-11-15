"""prompts.user_id——个人提示词隔离（种子 NULL=全员共享）

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-10-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, Sequence[str], None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("prompts", sa.Column("user_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_prompts_user", "prompts", "users", ["user_id"], ["id"], ondelete="CASCADE")
    op.create_index("ix_prompts_user_id", "prompts", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_prompts_user_id", table_name="prompts")
    op.drop_constraint("fk_prompts_user", "prompts", type_="foreignkey")
    op.drop_column("prompts", "user_id")
