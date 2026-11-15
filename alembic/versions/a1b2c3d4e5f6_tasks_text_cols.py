"""tasks.output_url/error_msg 改 Text（LLM 长文本输出）

Revision ID: a1b2c3d4e5f6
Revises: b05f930fac0f
"""

from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f6"
down_revision = "b05f930fac0f"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("tasks") as batch:
        batch.alter_column("output_url", existing_type=sa.String(500), type_=sa.Text())
        batch.alter_column("error_msg", existing_type=sa.Text(), type_=sa.Text())


def downgrade():
    pass
