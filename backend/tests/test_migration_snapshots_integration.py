"""迁移库集成冒烟（integration）：快照 create/list/restore 全链路走真实迁移库。

背景（本 PR 回归）：CanvasSnapshot 模型长期没有对应迁移，新库上
`alembic upgrade head` 后 canvas_snapshots 表不存在，快照接口 500。
本用例对“完全用 alembic 迁移建出来的真实 PG 库”跑完整 API 流程，防再次漂移。

依赖真实 PostgreSQL（本地 127.0.0.1:55432）；CI 用 -m integration 跑，
库地址取 TEST_DATABASE_URL（优先）或 DATABASE_URL。

derived from backend/tests/conftest.py（client fixture 的 PG 变体）@ 2026-11-16。
"""

import os
import uuid
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from backend.app.core.config import get_settings
from backend.app.core.db import get_db
from backend.app.main import create_app
from backend.app.models import Base

pytestmark = [pytest.mark.integration]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL") or ""


def _with_db_name(url: str, db_name: str) -> str:
    """把连接串里的库名换成 db_name（保留 host/auth 与 query 参数）。"""
    head, sep, query = url.partition("?")
    scheme, _, rest = head.partition("://")
    authority, _, _old_db = rest.rpartition("/")
    new = f"{scheme}://{authority}/{db_name}"
    return f"{new}{sep}{query}" if sep else new


@pytest.fixture(scope="module")
def migrated_engine() -> Generator:
    """真实 PG：建一个临时库，完全用 alembic 迁移升到 head（不用 create_all）。"""
    if not _BASE_URL.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.skip("需要 PostgreSQL（TEST_DATABASE_URL / DATABASE_URL）")

    db_name = f"creagen_mig_{uuid.uuid4().hex[:8]}"
    test_url = _with_db_name(_BASE_URL, db_name)
    admin_url = _with_db_name(_BASE_URL, "postgres")
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{db_name}"'))
        created_db = True
    except Exception:  # noqa: BLE001 — 无 CREATE DATABASE 权限时退回重建 public schema
        created_db = False

    prev_env = {k: os.environ.get(k) for k in ("DATABASE_URL", "JWT_SECRET", "ENCRYPTION_KEY")}
    os.environ["DATABASE_URL"] = test_url
    os.environ.setdefault("JWT_SECRET", "migration-test-secret-0000")
    os.environ.setdefault("ENCRYPTION_KEY", "migration-test-enc-key-0000")
    get_settings.cache_clear()

    from alembic.config import Config

    from alembic import command

    alembic_cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    if not created_db:
        engine = create_engine(test_url)
        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
        engine.dispose()

    command.upgrade(alembic_cfg, "head")
    command.check(alembic_cfg)  # 迁移库必须与模型一致（alembic check 语义）

    engine = create_engine(test_url)
    try:
        yield engine
    finally:
        engine.dispose()
        if created_db:
            with admin.connect() as conn:
                conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
        admin.dispose()
        for k, v in prev_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        get_settings.cache_clear()


@pytest.fixture
def migrated_client(migrated_engine) -> Generator[TestClient, None, None]:
    factory = sessionmaker(bind=migrated_engine, autoflush=False, expire_on_commit=False)

    def _override_get_db():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app = create_app()
    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _register(client: TestClient, username: str) -> dict:
    r = client.post(
        "/api/auth/register",
        json={
            "username": username,
            "email": f"{username}@migrate.test",
            "password": "pass-word-123456",
            "invite_code": "letmein",
        },
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_snapshots_end_to_end_on_migrated_db(migrated_client):
    """快照 create/list/restore 在迁移建出的真实库上全链路可用（此前新库直接 500）。"""
    headers = _register(migrated_client, "mig_snap_user")
    pid = migrated_client.post("/api/canvas/projects", json={}, headers=headers).json()[
        "project"
    ]["id"]

    # 初始节点 v1 → 快照
    r = migrated_client.put(
        f"/api/canvas/projects/{pid}/nodes",
        json={"nodes": [{"type": "text", "title": "v1", "position_x": 0, "position_y": 0}]},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    r = migrated_client.post(
        f"/api/canvas/projects/{pid}/snapshots", json={"label": "第一版"}, headers=headers
    )
    assert r.status_code == 201, r.text
    snap = r.json()["snapshot"]
    assert snap["version"] == 1

    # 画布推进到 v2
    r = migrated_client.put(
        f"/api/canvas/projects/{pid}/nodes",
        json={"nodes": [{"type": "image", "title": "v2", "position_x": 5, "position_y": 5}]},
        headers=headers,
    )
    assert r.status_code == 200, r.text

    # list
    snaps = migrated_client.get(f"/api/canvas/projects/{pid}/snapshots", headers=headers).json()[
        "snapshots"
    ]
    assert [s["version"] for s in snaps] == [1]

    # restore v1 → 节点回到 v1，且恢复前自动生成安全网快照
    r = migrated_client.post(
        f"/api/canvas/projects/{pid}/snapshots/{snaps[0]['id']}/restore", headers=headers
    )
    assert r.status_code == 200, r.text
    assert r.json()["restored"] is True
    nodes = r.json()["nodes"]
    assert len(nodes) == 1 and nodes[0]["title"] == "v1"

    snaps = migrated_client.get(f"/api/canvas/projects/{pid}/snapshots", headers=headers).json()[
        "snapshots"
    ]
    assert len(snaps) == 2
    assert snaps[0]["label"].startswith("恢复前")


def test_canvas_snapshots_table_matches_model(migrated_engine):
    """迁移建出的表必须覆盖模型声明的所有列（列级漂移守卫）。"""
    expected = {c.name for c in Base.metadata.tables["canvas_snapshots"].columns}
    with migrated_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'canvas_snapshots'"
            )
        ).fetchall()
    actual = {row[0] for row in rows}
    assert actual == expected, (
        f"canvas_snapshots 漂移: 缺 {expected - actual}, 多 {actual - expected}"
    )
