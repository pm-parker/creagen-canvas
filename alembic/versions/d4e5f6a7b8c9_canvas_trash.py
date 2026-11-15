"""canvas_projects 软删除（回收站）

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-10-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("canvas_projects", sa.Column("deleted_at", sa.DateTime(), nullable=True))
    op.create_index("ix_canvas_projects_deleted_at", "canvas_projects", ["deleted_at"])


def downgrade() -> None:
    op.drop_index("ix_canvas_projects_deleted_at", table_name="canvas_projects")
    op.drop_column("canvas_projects", "deleted_at")
