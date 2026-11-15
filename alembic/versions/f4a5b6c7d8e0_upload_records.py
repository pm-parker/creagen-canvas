"""upload_records：上传归属/配额记账（0.10.3 审计批次二）。"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f4a5b6c7d8e0"
down_revision: Union[str, Sequence[str], None] = "e3f4a5b6c7d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "upload_records",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False, index=True),
        sa.Column("path", sa.String(500), nullable=False),
        sa.Column("mime", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("upload_records")
