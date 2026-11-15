"""应用级 fixtures：SQLite 内存库 + TestClient。"""

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.db import get_db
from backend.app.main import create_app
from backend.app.models import Base


@pytest.fixture(autouse=True)
def _clean_rate_limiter():
    """内存限流计数器跨测试清理：function 级 DB 重建使用户 id 复用，
    同 uid 计数会跨测试串扰（审计批次二测试发现的坑）。"""
    from backend.app.services import rate_limiter

    rate_limiter._memory_store.clear()
    yield
    rate_limiter._memory_store.clear()


@pytest.fixture(autouse=True)
def fake_arq_pool(monkeypatch):
    """默认 mock arq 连接（单测无 Redis）；真 Redis 集成用 integration 标记单独跑。"""
    jobs: list[tuple] = []

    class FakePool:
        async def enqueue_job(self, fn, *args, **kwargs):  # noqa: ANN001
            jobs.append((fn, args, kwargs))
            return None

    async def _pool():
        return FakePool()

    from backend.app.services import task_queue

    monkeypatch.setattr(task_queue, "get_arq_pool", _pool)
    yield jobs


@pytest.fixture(autouse=True)
def test_env(monkeypatch):
    """每个测试的确定性环境；必填变量齐全，缺变量场景由测试自己 delenv。
    REDIS_URL 尊重外部传入（integration 跑真实 Redis 时用 -m integration + 环境变量指向）。"""
    import os

    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("REDIS_URL", os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-0123456789abcdef")
    monkeypatch.setenv("ENCRYPTION_KEY", "test-encryption-key-0123456789abcdef")
    monkeypatch.setenv("REGISTER_MODE", "invite")
    monkeypatch.setenv("INVITE_CODES", "letmein,testcode")
    monkeypatch.setenv("ENABLE_FAKE_PROVIDER", "true")
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60")

    from backend.app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def db_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def worker_db(db_engine, monkeypatch):
    """worker 进程函数的 DB 会话工厂指向测试引擎（模拟独立进程同库）。"""
    factory = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr("backend.worker.get_session_factory", lambda: factory)
    return factory


@pytest.fixture
def db_session(db_engine) -> Generator[Session, None, None]:
    factory = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    session = factory()
    yield session
    session.close()


@pytest.fixture
def client(db_engine) -> Generator[TestClient, None, None]:
    factory = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)

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
