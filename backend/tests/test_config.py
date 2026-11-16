"""Settings 配置加载：fail-fast 语义（Review Focus #3）+ 部署脚手架 footgun 封堵（pr3）。"""

import pytest


def _make_settings():
    from backend.app.core.config import Settings

    return Settings(_env_file=None)  # type: ignore[call-arg]


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


def test_validation_error_does_not_leak_secret(monkeypatch):
    """round 3：hide_input_in_errors=True —— 过短密钥的 ValidationError 不得回显密钥本身。"""
    from pydantic import ValidationError

    leaked = "leaky-s3cret"
    assert len(leaked) < 16
    monkeypatch.setenv("JWT_SECRET", leaked)
    monkeypatch.setenv("ENCRYPTION_KEY", "e" * 64)
    monkeypatch.chdir("/tmp")

    with pytest.raises(ValidationError, match="JWT_SECRET") as ei:
        _make_settings()
    assert leaked not in str(ei.value)
    assert leaked not in repr(ei.value)


def test_defaults_and_parsing(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x:y@localhost/z")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("JWT_SECRET", "s" * 32)
    monkeypatch.setenv("ENCRYPTION_KEY", "k" * 32)
    monkeypatch.delenv("REGISTER_MODE", raising=False)
    monkeypatch.delenv("ENABLE_FAKE_PROVIDER", raising=False)
    monkeypatch.chdir("/tmp")

    s = _make_settings()
    assert s.register_mode == "invite"  # 全局约束：默认邀请制
    assert s.enable_fake_provider is False


# ---------- pr3：部署/配置 footgun 封堵 ----------


def test_fake_provider_defaults_off(monkeypatch):
    """ENABLE_FAKE_PROVIDER 默认 False：不显式开启绝不回落 FakeProvider。"""

    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x:y@localhost/z")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("JWT_SECRET", "s" * 32)
    monkeypatch.setenv("ENCRYPTION_KEY", "k" * 32)
    monkeypatch.delenv("ENABLE_FAKE_PROVIDER", raising=False)
    monkeypatch.chdir("/tmp")

    assert _make_settings().enable_fake_provider is False


def test_explicit_fake_provider_on(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "s" * 32)
    monkeypatch.setenv("ENCRYPTION_KEY", "k" * 32)
    monkeypatch.setenv("ENABLE_FAKE_PROVIDER", "true")
    monkeypatch.chdir("/tmp")

    assert _make_settings().enable_fake_provider is True


class TestSecretValidation:
    """密钥校验：<16 拒绝；16~31 或占位值仅告警；强随机值无告警（round 2 放宽）。"""

    @pytest.mark.parametrize("var", ["JWT_SECRET", "ENCRYPTION_KEY"])
    @pytest.mark.parametrize(
        "bad",
        [
            "",  # 空
            "short-secret-1",  # < 16 字符
            "c" * 15,  # 差一位
        ],
    )
    def test_rejects_too_short_secrets(self, monkeypatch, var, bad):
        from pydantic import ValidationError

        monkeypatch.setenv("JWT_SECRET", "j" * 64)
        monkeypatch.setenv("ENCRYPTION_KEY", "e" * 64)
        monkeypatch.setenv(var, bad)
        monkeypatch.chdir("/tmp")

        with pytest.raises(ValidationError, match=var):
            _make_settings()

    def test_error_message_is_actionable(self, monkeypatch):
        from pydantic import ValidationError

        monkeypatch.setenv("JWT_SECRET", "too-short")
        monkeypatch.setenv("ENCRYPTION_KEY", "e" * 64)
        monkeypatch.chdir("/tmp")

        with pytest.raises(ValidationError) as ei:
            _make_settings()
        assert "JWT_SECRET" in str(ei.value)
        assert "openssl rand -hex 32" in str(ei.value)

    @pytest.mark.parametrize("var", ["JWT_SECRET", "ENCRYPTION_KEY"])
    @pytest.mark.parametrize(
        "short",
        [
            "s" * 16,  # 刚好达标下限，但低于推荐值
            "c" * 31,  # 差一位到 32
        ],
    )
    def test_short_but_valid_secrets_warn(self, monkeypatch, caplog, var, short):
        """16~31 字符：不拦截，但启动时打 WARNING（存量部署兼容）。"""
        monkeypatch.setenv("JWT_SECRET", "j" * 64)
        monkeypatch.setenv("ENCRYPTION_KEY", "e" * 64)
        monkeypatch.setenv(var, short)
        monkeypatch.chdir("/tmp")

        with caplog.at_level("WARNING", logger="creagen.config"):
            s = _make_settings()
        assert getattr(s, "jwt_secret" if var == "JWT_SECRET" else "encryption_key") == short
        assert any(var in r.message and "openssl rand -hex 32" in r.message for r in caplog.records)
        # 消息里不得回显密钥本身
        assert not any(short in r.message for r in caplog.records)

    @pytest.mark.parametrize(
        ("var", "placeholder"),
        [
            # .env.example 示例值
            ("JWT_SECRET", "change-me-please-generate-a-random-64-hex-string"),
            # 大小写/下划线变体
            ("JWT_SECRET", "Change_Me_Please_I_Am_Too_Lazy_To_Roll_A_Key_32"),
            # 文档占位措辞
            ("JWT_SECRET", "your-secret-please-generate-with-openssl-rand-hex-64"),
            ("ENCRYPTION_KEY", "please-change-this-insecure-default-secret-value"),
            ("ENCRYPTION_KEY", "placeholder-secret-change-me-please-generate-a-random-64-hex"),
        ],
    )
    def test_placeholder_secrets_warn_but_accepted(self, monkeypatch, caplog, var, placeholder):
        """占位值：不拦截，仅 WARNING（round 2：存量部署保持可启动）。"""
        monkeypatch.setenv("JWT_SECRET", "j" * 64)
        monkeypatch.setenv("ENCRYPTION_KEY", "e" * 64)
        monkeypatch.setenv(var, placeholder)
        monkeypatch.chdir("/tmp")

        with caplog.at_level("WARNING", logger="creagen.config"):
            s = _make_settings()
        assert getattr(s, "jwt_secret" if var == "JWT_SECRET" else "encryption_key") == placeholder
        assert any(var in r.message and "openssl rand -hex 32" in r.message for r in caplog.records)
        assert not any(placeholder in r.message for r in caplog.records)

    def test_encryption_key_warning_mentions_rotation_cost(self, monkeypatch, caplog):
        """ENCRYPTION_KEY 告警需提示轮换会使已存渠道密钥不可解密。"""
        monkeypatch.setenv("JWT_SECRET", "j" * 64)
        monkeypatch.setenv("ENCRYPTION_KEY", "please-change-me-insecure-64")
        monkeypatch.chdir("/tmp")

        with caplog.at_level("WARNING", logger="creagen.config"):
            _make_settings()
        msgs = [r.message for r in caplog.records]
        assert any("ENCRYPTION_KEY" in m and "解密" in m for m in msgs)
        # JWT_SECRET 的告警不应带渠道密钥轮换提示
        assert not any("JWT_SECRET" in m and "渠道密钥无法解密" in m for m in msgs)

    def test_accepts_random_hex_secrets(self, monkeypatch, caplog):
        """64 位 hex 强随机值：通过且无任何 WARNING。"""
        monkeypatch.setenv("JWT_SECRET", "a" * 64)
        monkeypatch.setenv("ENCRYPTION_KEY", "b" * 64)
        monkeypatch.chdir("/tmp")

        with caplog.at_level("WARNING", logger="creagen.config"):
            s = _make_settings()
        assert s.jwt_secret == "a" * 64
        assert s.encryption_key == "b" * 64
        assert not [r for r in caplog.records if r.levelname == "WARNING"]


class TestNoDefaultInviteCode:
    """无内置默认邀请码：invite 模式未配置 INVITE_CODES 时注册一律拒绝（fail-closed）。"""

    def test_settings_default_invite_codes_empty(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET", "s" * 32)
        monkeypatch.setenv("ENCRYPTION_KEY", "k" * 32)
        monkeypatch.delenv("INVITE_CODES", raising=False)
        monkeypatch.chdir("/tmp")

        s = _make_settings()
        assert s.invite_codes == ""
        assert s.invite_code_list == []

    def test_invite_mode_without_codes_refuses_registration(self, monkeypatch, client):
        """REGISTER_MODE=invite 且未配置 INVITE_CODES：任何候选码（含 letmein）都 403。"""
        from backend.app.core.config import get_settings

        monkeypatch.setenv("REGISTER_MODE", "invite")
        monkeypatch.setenv("INVITE_CODES", "")
        get_settings.cache_clear()
        try:
            for code in (None, "", "letmein", "anything-else"):
                body = {
                    "username": "noclient",
                    "email": "noclient@x.io",
                    "password": "password123",
                }
                if code is not None:
                    body["invite_code"] = code
                r = client.post("/api/auth/register", json=body)
                assert r.status_code == 403, f"invite_code={code!r} 应被拒绝，得到 {r.status_code}"
        finally:
            get_settings.cache_clear()

    def test_invite_mode_with_explicit_code_accepts_registration(self, monkeypatch, client):
        from backend.app.core.config import get_settings

        monkeypatch.setenv("REGISTER_MODE", "invite")
        monkeypatch.setenv("INVITE_CODES", "explicit-test-code")
        get_settings.cache_clear()
        try:
            r = client.post(
                "/api/auth/register",
                json={
                    "username": "invuser",
                    "email": "invuser@x.io",
                    "password": "password123",
                    "invite_code": "explicit-test-code",
                },
            )
            assert r.status_code == 200, r.text
        finally:
            get_settings.cache_clear()


def test_get_settings_is_cached():
    # conftest test_env 已设齐全变量并 cache_clear：两次调用应命中同一缓存对象
    from backend.app.core.config import get_settings

    a = get_settings()
    b = get_settings()
    assert a is b
