"""Settings 配置加载：fail-fast 语义（Review Focus #3）。"""


def test_missing_required_var_fails_fast(monkeypatch):
    for var in ("DATABASE_URL", "REDIS_URL", "JWT_SECRET", "ENCRYPTION_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir("/tmp")  # 远离仓库根，避免读到 .env
    from pydantic import ValidationError

    from backend.app.core.config import Settings

    try:
        Settings(_env_file=None)  # type: ignore[call-arg]
        raise AssertionError("Settings() 应当因缺必填变量而失败")
    except ValidationError as e:
        assert "JWT_SECRET" in str(e)


def test_defaults_and_parsing(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x:y@localhost/z")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("JWT_SECRET", "s" * 32)
    monkeypatch.setenv("ENCRYPTION_KEY", "k" * 32)
    monkeypatch.delenv("REGISTER_MODE", raising=False)
    monkeypatch.delenv("ENABLE_FAKE_PROVIDER", raising=False)
    monkeypatch.chdir("/tmp")
    from backend.app.core.config import Settings

    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.register_mode == "invite"  # 全局约束：默认邀请制
    assert s.enable_fake_provider is False


def test_get_settings_is_cached():
    # conftest test_env 已设齐全变量并 cache_clear：两次调用应命中同一缓存对象
    from backend.app.core.config import get_settings

    a = get_settings()
    b = get_settings()
    assert a is b
