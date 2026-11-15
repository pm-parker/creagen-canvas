"""agent run v2 M0：run_events 事件流表 + engine/记账/谱系列（技术设计 §3.1/3.3）

Revision ID: f8a9b0c1d2e3
Revises: e7a8b9c0d1e2
Create Date: 2026-10-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f8a9b0c1d2e3"
down_revision: Union[str, Sequence[str], None] = "e7a8b9c0d1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "run_events",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", sa.String(40), nullable=False),
        sa.Column("parent_id", sa.BigInteger(), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_run_events_run", "run_events", ["run_id", "id"])

    op.add_column("agent_runs", sa.Column("engine", sa.String(4), nullable=False, server_default="v1"))
    op.add_column("agent_runs", sa.Column("last_event_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "agent_runs", sa.Column("action_steps", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(
        "agent_runs", sa.Column("brain_calls", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(
        "agent_runs",
        sa.Column("done_rejections", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("agent_runs", sa.Column("plan_json", sa.Text(), nullable=True))
    op.add_column("agent_runs", sa.Column("extensions_json", sa.Text(), nullable=True))
    op.add_column("agent_runs", sa.Column("parent_run_id", sa.Integer(), nullable=True))
    op.add_column("agent_runs", sa.Column("deliverables_proj_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_runs", "deliverables_proj_json")
    op.drop_column("agent_runs", "parent_run_id")
    op.drop_column("agent_runs", "extensions_json")
    op.drop_column("agent_runs", "plan_json")
    op.drop_column("agent_runs", "done_rejections")
    op.drop_column("agent_runs", "brain_calls")
    op.drop_column("agent_runs", "action_steps")
    op.drop_column("agent_runs", "last_event_id")
    op.drop_column("agent_runs", "engine")
    op.drop_index("ix_run_events_run", table_name="run_events")
    op.drop_table("run_events")
