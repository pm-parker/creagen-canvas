"""users.api_key：Agent API Key（画布级 Agent API 认证）

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c9d0e1f2a3b4"
down_revision: str | Sequence[str] | None = "b8c9d0e1f2a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("api_key", sa.String(64), nullable=True))
    op.create_index("uq_users_api_key", "users", ["api_key"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_users_api_key", table_name="users")
    op.drop_column("users", "api_key")
