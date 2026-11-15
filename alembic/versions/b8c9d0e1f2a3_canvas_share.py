"""canvas_projects.share_token：只读分享链接（P0）

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8c9d0e1f2a3"
down_revision: str | Sequence[str] | None = "a7b8c9d0e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("canvas_projects", sa.Column("share_token", sa.String(24), nullable=True))
    op.create_index("uq_canvas_share_token", "canvas_projects", ["share_token"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_canvas_share_token", table_name="canvas_projects")
    op.drop_column("canvas_projects", "share_token")
