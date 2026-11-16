"""迁移覆盖守卫：模型里的每张表都必须有对应迁移（不需要真实数据库）。

背景（本 PR 回归）：CanvasSnapshot 模型长期没有 Alembic 迁移，新库上
`alembic upgrade head` 后快照接口 500。本用例用 alembic 的 offline 模式
把整条迁移链渲染成 SQL（不连库），再断言模型里的每张表都在其中建了出来——
新增模型忘了写迁移会在这里红。

纯单测（不进 integration 标记），CI 默认就会跑。
"""

import contextlib
import io
from pathlib import Path

from backend.app.core.config import get_settings
from backend.app.models import Base

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _render_migration_sql() -> str:
    """offline 模式渲染 `alembic upgrade head` 的完整 SQL（不建立任何连接）。"""
    from alembic.config import Config

    from alembic import command

    cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    buf = io.StringIO()
    cfg.stdout = buf
    with contextlib.redirect_stdout(buf):
        command.upgrade(cfg, "head", sql=True)
    return buf.getvalue()


def test_every_model_table_is_created_by_migrations(monkeypatch):
    # offline 渲染只需要一个 PG 风格的 URL（方言决定渲染语法，不会真连库）
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@127.0.0.1:5432/offline_render")
    get_settings.cache_clear()

    sql = _render_migration_sql()

    missing = [name for name in Base.metadata.tables if f"CREATE TABLE {name}" not in sql]
    assert not missing, f"模型表缺少对应迁移: {missing}"


def test_canvas_snapshots_table_is_created_by_migrations(monkeypatch):
    """快照表曾整表漏迁移——单独钉一条，防回归。"""
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@127.0.0.1:5432/offline_render")
    get_settings.cache_clear()

    sql = _render_migration_sql()

    assert "CREATE TABLE canvas_snapshots" in sql
    assert "ix_canvas_snapshots_project_id" in sql
