"""角色资产库（0.9.3）：character_assets 表。"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d2e3f4a5b6c1"
down_revision: Union[str, Sequence[str], None] = "c1d2e3f4a5b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "character_assets",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False, index=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("ref_image_url", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "name", name="uq_character_user_name"),
    )


def downgrade() -> None:
    op.drop_table("character_assets")
