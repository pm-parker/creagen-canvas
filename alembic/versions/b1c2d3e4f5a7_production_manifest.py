"""agent run v2 后: production manifest (制片清单闸门)

Revision ID: b1c2d3e4f5a7
Revises: a9b0c1d2e3f4
Create Date: 2026-10-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b1c2d3e4f5a7"
down_revision: Union[str, Sequence[str], None] = "a9b0c1d2e3f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "production_manifests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("canvas_projects.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("user_id", sa.Integer(), nullable=False, index=True),
        sa.Column("schema_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("slots_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("probes_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("strict", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("production_manifests")
