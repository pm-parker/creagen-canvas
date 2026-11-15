"""agent_runs / agent_steps 两表

Revision ID: e7a8b9c0d1e2
Revises: d3e4f5a6b7c8
Create Date: 2026-10-09
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e7a8b9c0d1e2"
down_revision: Union[str, Sequence[str], None] = "d3e4f5a6b7c8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("messages_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("pending_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("reference_whitelist_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("step_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_steps", sa.Integer(), nullable=False, server_default="12"),
        sa.Column("pinned_model_json", sa.Text(), nullable=True),
        sa.Column("brain_channel_id", sa.Integer(), nullable=True),
        sa.Column("tokens_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("est_cost", sa.Float(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_agent_runs_user_status", "agent_runs", ["user_id", "status"])
    op.create_table(
        "agent_steps",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("idx", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("thought", sa.Text(), server_default=""),
        sa.Column("action_json", sa.Text(), server_default="{}"),
        sa.Column("result_json", sa.Text(), server_default="{}"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_agent_steps_run_id", "agent_steps", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_steps_run_id", table_name="agent_steps")
    op.drop_table("agent_steps")
    op.drop_index("ix_agent_runs_user_status", table_name="agent_runs")
    op.drop_table("agent_runs")
