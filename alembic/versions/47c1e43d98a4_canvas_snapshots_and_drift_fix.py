"""canvas_snapshots 建表 + 模型/迁移漂移修复

Revision ID: 47c1e43d98a4
Revises: b1c2d3e4f5a7
Create Date: 2026-11-16

背景：CanvasSnapshot 模型（backend/app/models/canvas_snapshot.py）一直没有对应迁移，
新库上画布快照接口直接 500（relation "canvas_snapshots" does not exist）。
本修订补建该表，并把 `alembic check` 报出的其余漂移（索引命名/唯一性、可空性）
对齐到模型——模型为准，迁移侧改，不改模型语义。

变更清单：
1. 新建 canvas_snapshots 表（列/类型/可空性/FK 与模型逐列一致）+ 索引
   ix_canvas_snapshots_project_id（模型 project_id 上 index=True）。
2. canvas_projects.share_token：模型声明 unique=True, index=True → 唯一索引名应为
   ix_canvas_projects_share_token；历史迁移建的是 uq_canvas_share_token，重命名对齐。
3. production_manifests.project_id：模型声明 unique=True, index=True → 唯一索引
   ix_production_manifests_project_id；历史迁移建的是 UNIQUE 约束
   production_manifests_project_id_key，改为同名唯一索引。
4. users.api_key：模型声明 unique=True, index=True → 唯一索引 ix_users_api_key；
   历史迁移建的是 uq_users_api_key，重命名对齐。

另有两处漂移在模型侧修（不动库，避免性能/完整性回退，详见对应模型文件）：
- run_events 复合索引 ix_run_events_run(run_id, id)：事件流按 (run_id, id) 有序读，
  保留库索引，改为在模型 __table_args__ 显式声明（同 agent_runs 的做法）。
- upload_records.created_at：库列 NOT NULL + now() 默认更严格，模型改为
  nullable=False（保留 default=func.now()），向库对齐而不放松完整性。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "47c1e43d98a4"
down_revision: Union[str, Sequence[str], None] = "b1c2d3e4f5a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) 画布版本快照表（此前完全没有迁移，新库上快照接口 500）
    op.create_table(
        "canvas_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("nodes_json", sa.Text(), nullable=False),
        sa.Column("connections_json", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["canvas_projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_canvas_snapshots_project_id", "canvas_snapshots", ["project_id"], unique=False
    )

    # 2) 分享 token 唯一索引改名（对齐模型 unique+index 的默认命名）
    op.drop_index("uq_canvas_share_token", table_name="canvas_projects")
    op.create_index(
        "ix_canvas_projects_share_token", "canvas_projects", ["share_token"], unique=True
    )

    # 3) 制片清单：一项目一清单 —— 唯一约束改为同名唯一索引
    op.drop_constraint(
        "production_manifests_project_id_key", "production_manifests", type_="unique"
    )
    op.create_index(
        "ix_production_manifests_project_id", "production_manifests", ["project_id"], unique=True
    )

    # 4) users.api_key 唯一索引改名
    op.drop_index("uq_users_api_key", table_name="users")
    op.create_index("ix_users_api_key", "users", ["api_key"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_api_key", table_name="users")
    op.create_index("uq_users_api_key", "users", ["api_key"], unique=True)

    op.drop_index("ix_production_manifests_project_id", table_name="production_manifests")
    op.create_unique_constraint(
        "production_manifests_project_id_key", "production_manifests", ["project_id"]
    )

    op.drop_index("ix_canvas_projects_share_token", table_name="canvas_projects")
    op.create_index("uq_canvas_share_token", "canvas_projects", ["share_token"], unique=True)

    op.drop_index("ix_canvas_snapshots_project_id", table_name="canvas_snapshots")
    op.drop_table("canvas_snapshots")
