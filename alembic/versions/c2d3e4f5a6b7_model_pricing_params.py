"""provider_models 扩列：参数/元数据/正交计价（模型配置升级批次）。

spec: docs/superpowers/specs/2026-10-07-model-config.md §4
- params_json/meta_json：语义参数默认值 + 能力元数据（域 schema 见 services/param_schema.py）
- is_free + pricing_unit + price_input/price_output：免费标记与计价单位正交拆分
- 全部可空/带默认：存量行 pricing_unit=NULL → 行为与升级前一致（兼容承诺）
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c2d3e4f5a6b7"
down_revision: Union[str, Sequence[str], None] = "b1c2d3e4f5a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("provider_models", sa.Column("params_json", sa.Text(), nullable=False, server_default="{}"))
    op.add_column("provider_models", sa.Column("meta_json", sa.Text(), nullable=False, server_default="{}"))
    op.add_column("provider_models", sa.Column("is_free", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("provider_models", sa.Column("pricing_unit", sa.String(20), nullable=True))
    op.add_column("provider_models", sa.Column("price_input", sa.Float(), nullable=True))
    op.add_column("provider_models", sa.Column("price_output", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("provider_models", "price_output")
    op.drop_column("provider_models", "price_input")
    op.drop_column("provider_models", "pricing_unit")
    op.drop_column("provider_models", "is_free")
    op.drop_column("provider_models", "meta_json")
    op.drop_column("provider_models", "params_json")
