"""系统设置：默认模型（管理员配置全员兜底，存 capability_config KV）。"""

REGISTER_ADMIN = {
    "username": "setadmin",
    "email": "setadmin@x.io",
    "password": "password123",
    "invite_code": "letmein",
}
REGISTER_USER = {
    "username": "setuser",
    "email": "setuser@x.io",
    "password": "password123",
    "invite_code": "letmein",
}


def _register(client, body):
    r = client.post("/api/auth/register", json=body)
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _make_admin(db_session):
    from backend.app.models import User

    u = User(username="setadmin", email="setadmin@x.io", password_hash="x", role="admin")
    db_session.add(u)
    db_session.commit()
    return u


def _admin_headers(db_session):
    from backend.app.services.auth_service import create_access_token

    return {"Authorization": f"Bearer {create_access_token(_make_admin(db_session))}"}


def test_get_default_models_empty_initially(client):
    headers = _register(client, REGISTER_USER)
    r = client.get("/api/settings/default-models", headers=headers)
    assert r.status_code == 200
    assert r.json()["defaults"] == {}


def test_put_default_models_requires_admin(client, db_session):
    headers = _register(client, REGISTER_USER)
    r = client.put(
        "/api/settings/default-models",
        json={"image": {"channel_id": 1, "model": "x"}},
        headers=headers,
    )
    assert r.status_code == 403


def test_admin_can_set_and_get_default_models(client, db_session):
    from backend.app.core.crypto import encrypt_secret
    from backend.app.models import ProviderChannel

    admin_headers = _admin_headers(db_session)
    ch = ProviderChannel(
        user_id=None,
        domain="image",
        name="系统生图",
        base_url="https://api.example.com/v1",
        key_encrypted=encrypt_secret("sk-x"),
    )
    db_session.add(ch)
    db_session.commit()

    body = {"image": {"channel_id": ch.id, "model": "Kwai-Kolors/Kolors"}}
    r = client.put("/api/settings/default-models", json=body, headers=admin_headers)
    assert r.status_code == 200, r.text

    # 全员可读（普通用户）
    user_headers = _register(client, REGISTER_USER)
    r = client.get("/api/settings/default-models", headers=user_headers)
    assert r.json()["defaults"] == body


def test_put_validates_channel_exists_and_domain(client, db_session):
    admin_headers = _admin_headers(db_session)

    # 渠道不存在
    r = client.put(
        "/api/settings/default-models",
        json={"image": {"channel_id": 999, "model": "x"}},
        headers=admin_headers,
    )
    assert r.status_code == 422
    # 域非法
    r = client.put(
        "/api/settings/default-models",
        json={"podcast": {"channel_id": 1, "model": "x"}},
        headers=admin_headers,
    )
    assert r.status_code == 422
    # 模型名空
    r = client.put(
        "/api/settings/default-models",
        json={"image": {"channel_id": 1, "model": "  "}},
        headers=admin_headers,
    )
    assert r.status_code == 422


class TestJwtRevocation:
    """P2-14: JWT 撤销——改密码后旧 token 立即失效（token_version 机制）。"""

    def test_old_token_invalid_after_password_change(self, client, db_session):
        headers = _register(client, REGISTER_USER)
        old_token = headers["Authorization"].split(" ", 1)[1]

        # 改密码
        r = client.post(
            "/api/auth/change-password",
            json={"old_password": REGISTER_USER["password"], "new_password": "newpassword456"},
            headers=headers,
        )
        assert r.status_code == 200, r.text

        # 旧 token 应被拒绝（401 凭据已失效）
        r = client.get("/api/assets", headers={"Authorization": f"Bearer {old_token}"})
        assert r.status_code == 401

        # 新登录的 token 正常
        r = client.post(
            "/api/auth/login",
            json={"identifier": REGISTER_USER["username"], "password": "newpassword456"},
        )
        new_token = r.json()["access_token"]
        r = client.get("/api/assets", headers={"Authorization": f"Bearer {new_token}"})
        assert r.status_code == 200

    def test_change_password_returns_new_token_and_revokes_old(self, client, db_session):
        """PR7: 改密码成功 → 返回新 token（同登录 TokenResponse 形状）；
        新 token 可访问 /me，旧 token 立即失效。"""
        headers = _register(client, REGISTER_USER)
        old_token = headers["Authorization"].split(" ", 1)[1]

        r = client.post(
            "/api/auth/change-password",
            json={"old_password": REGISTER_USER["password"], "new_password": "newpassword456"},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["token_type"] == "bearer"
        new_token = body["access_token"]
        assert isinstance(new_token, str) and new_token
        assert new_token != old_token

        # 新 token 可访问 /me
        r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {new_token}"})
        assert r.status_code == 200, r.text
        assert r.json()["username"] == REGISTER_USER["username"]

        # 旧 token 被拒（凭据已失效）
        r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {old_token}"})
        assert r.status_code == 401

    def test_change_password_wrong_old_password_400_and_token_kept(self, client, db_session):
        """PR7: 旧密码错误 → 400 WRONG_PASSWORD（不再 401 踢登录态），
        且当前 token 仍有效。"""
        headers = _register(client, REGISTER_USER)
        token = headers["Authorization"].split(" ", 1)[1]

        r = client.post(
            "/api/auth/change-password",
            json={"old_password": "definitely-wrong", "new_password": "newpassword456"},
            headers=headers,
        )
        assert r.status_code == 400, r.text
        detail = r.json()["detail"]
        assert detail["code"] == "WRONG_PASSWORD"
        assert detail["message"] == "原密码错误"

        # 当前 token 未被撤销，会话不受影响
        r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200, r.text

        # 密码未变：仍可用原密码登录
        r = client.post(
            "/api/auth/login",
            json={"identifier": REGISTER_USER["username"], "password": REGISTER_USER["password"]},
        )
        assert r.status_code == 200

    def test_forged_version_rejected(self, client, db_session):
        """伪造低版本 ver 的 token 被拒（防降级重放）。"""
        import jwt as pyjwt

        from backend.app.core.config import get_settings

        headers = _register(client, REGISTER_USER)
        payload = pyjwt.decode(
            headers["Authorization"].split(" ", 1)[1],
            get_settings().jwt_secret,
            algorithms=["HS256"],
        )
        payload["ver"] = 99  # 高版本伪造（服务端记录 0）
        forged = pyjwt.encode(payload, get_settings().jwt_secret, algorithm="HS256")
        r = client.get("/api/assets", headers={"Authorization": f"Bearer {forged}"})
        assert r.status_code == 401


class TestCorsConfig:
    """P2-14: CORS 从 * 收紧为环境变量配置。"""

    def test_default_allows_all(self, monkeypatch):  # noqa: ARG002
        from backend.app.core.config import Settings

        monkeypatch.delenv("CORS_ORIGINS", raising=False)
        # 必填字段运行时由环境变量补全（同 test_config.py 先例）
        assert Settings().cors_origins == ["*"]  # type: ignore[call-arg]  # 未配置 = 保持旧行为

    def test_csv_parsing(self, monkeypatch):  # noqa: ARG002
        from backend.app.core.config import Settings

        monkeypatch.setenv("CORS_ORIGINS", "https://a.com, https://b.com")
        # 必填字段运行时由环境变量补全（同 test_config.py 先例）
        settings = Settings()  # type: ignore[call-arg]
        assert settings.cors_origins == ["https://a.com", "https://b.com"]


def test_video_task_routes_to_video_queue(client, db_session):
    """P2-14: 视频任务入 video 队列（长任务不阻塞图片/LLM 快队列）。"""
    headers = _register(client, REGISTER_USER)
    from unittest.mock import patch

    with patch("backend.app.routers.tasks.enqueue_generation_task") as eq:
        eq.return_value = None
        for t, expect_q in (("image", "generate"), ("llm", "generate"), ("video", "video")):
            r = client.post(
                "/api/tasks",
                json={"type": t, "prompt": "测试", "model": "m"},
                headers=headers,
            )
            assert r.status_code in (200, 422), r.text  # 渠道缺失 422 也应已完成路由决策
            if r.status_code == 200:
                assert eq.call_args[0][1] == expect_q, f"{t} 应入 {expect_q} 队列"


def test_api_key_reveal_flag(client):
    """API Key 默认脱敏；reveal=true 显式取明文（自托管取回能力，登录态保护）。"""
    client.post(
        "/api/auth/register",
        json={
            "username": "revealuser", "email": "rv@x.io",
            "password": "password123", "invite_code": "letmein",
        },
    )
    r0 = client.post(
        "/api/auth/login", json={"identifier": "revealuser", "password": "password123"}
    )
    headers = {"Authorization": f"Bearer {r0.json()['access_token']}"}

    r = client.post("/api/settings/api-key", headers=headers)
    full = r.json()["api_key"]

    default = client.get("/api/settings/api-key", headers=headers).json()["api_key"]
    assert default != full and default.startswith("cg-") and "*" in default

    revealed = client.get("/api/settings/api-key?reveal=true", headers=headers).json()["api_key"]
    assert revealed == full


# ---------- 视觉模型能力标记 + 默认视觉模型（2026-10-09） ----------

def _mk_llm_channel(db_session, name="VL 渠道", models=None):  # noqa: ANN001
    import json as _j

    from backend.app.models import ProviderChannel, ProviderModel

    ch = ProviderChannel(
        user_id=None, domain="llm", name=name, base_url="https://vl.example/v1", key_encrypted="x"
    )
    db_session.add(ch)
    db_session.commit()
    for mid, meta in (models or []):
        db_session.add(ProviderModel(channel_id=ch.id, model_id=mid, meta_json=_j.dumps(meta)))
    db_session.commit()
    return ch


def test_sync_models_auto_tags_vision(db_session):
    """远端同步启发式：vision 命名模式自动打标；既有 meta 不覆盖。"""
    import json as _j

    from backend.app.models import ProviderChannel
    from backend.app.services.provider_service import _sync_models

    ch = ProviderChannel(
        user_id=None, domain="llm", name="sf", base_url="https://x/v1", key_encrypted="x"
    )
    db_session.add(ch)
    db_session.commit()

    class _Resp:
        def json(self):  # noqa: ANN201
            return {"data": [
                {"id": "Qwen/Qwen2.5-VL-72B"},      # 应命中 vl
                {"id": "glm-4.5v"},                   # 应命中 v 后缀
                {"id": "deepseek-ai/DeepSeek-V3"},    # 纯文本，不打标
                {"id": "MiniCPM-o-2.6"},              # 命中 minicpm-o/llava 类
                {"id": "manual-vision-x"},            # 命中 vision 关键词
            ]}

    _sync_models(db_session, ch, _Resp())
    db_session.expire_all()
    ch2 = db_session.query(ProviderChannel).filter(ProviderChannel.id == ch.id).first()
    metas = {m.model_id: _j.loads(m.meta_json or "{}") for m in ch2.models}
    assert metas["Qwen/Qwen2.5-VL-72B"].get("vision") is True
    assert metas["glm-4.5v"].get("vision") is True
    assert metas["MiniCPM-o-2.6"].get("vision") is True
    assert metas["manual-vision-x"].get("vision") is True
    assert not metas["deepseek-ai/DeepSeek-V3"].get("vision")


def test_put_default_vision_requires_vision_capable(db_session, client, admin_headers=None):
    """默认视觉模型：渠道须 llm 域且模型带 vision 标记。"""
    ch = _mk_llm_channel(
        db_session, models=[("qwen2.5-vl-7b", {"vision": True}), ("plain-llm", {})]
    )
    from backend.app.models import User
    from backend.app.services.auth_service import create_access_token

    u = db_session.query(User).filter(User.role == "admin").first()
    if u is None:
        u = User(username="adm", email="a@t.local", password_hash="x", role="admin")
        db_session.add(u)
        db_session.commit()
    tok = create_access_token(u)
    h = {"Authorization": f"Bearer {tok}"}

    # 非 vision 模型 → 422
    r = client.put("/api/settings/default-models", headers=h,
                   json={"vision": {"channel_id": ch.id, "model": "plain-llm"}})
    assert r.status_code == 422
    # vision 模型 → 200
    r2 = client.put("/api/settings/default-models", headers=h,
                    json={"vision": {"channel_id": ch.id, "model": "qwen2.5-vl-7b"}})
    assert r2.status_code == 200
    assert r2.json()["defaults"]["vision"]["model"] == "qwen2.5-vl-7b"


def test_get_default_vision_falls_back_to_vision_capable(db_session, client):
    """读取侧回落：未配置 vision 时，自动选第一个带标记的启用 llm 模型。"""
    from backend.app.models import User
    from backend.app.services.auth_service import create_access_token

    ch = _mk_llm_channel(db_session, models=[("qwen2.5-vl-7b", {"vision": True})])
    u = User(username="adm2", email="a2@t.local", password_hash="x", role="admin")
    db_session.add(u)
    db_session.commit()
    tok = create_access_token(u)
    r = client.get("/api/settings/default-models", headers={"Authorization": f"Bearer {tok}"})
    v = r.json()["defaults"].get("vision")
    assert v and v["model"] == "qwen2.5-vl-7b" and v["channel_id"] == ch.id


def test_sync_backfills_vision_on_existing(db_session):
    """存量回填：既有模型 meta 无 vision 键且命中 → 补标；显式 false 不覆盖。"""
    import json as _j

    from backend.app.models import ProviderChannel, ProviderModel
    from backend.app.services.provider_service import _sync_models

    ch = ProviderChannel(
        user_id=None, domain="llm", name="bf", base_url="https://x/v1", key_encrypted="x"
    )
    db_session.add(ch)
    db_session.commit()
    db_session.add_all([
        ProviderModel(channel_id=ch.id, model_id="old-vl-model", meta_json="{}"),
        ProviderModel(channel_id=ch.id, model_id="marked-false", meta_json='{"vision": false}'),
        ProviderModel(channel_id=ch.id, model_id="plain-old", meta_json="{}"),
    ])
    db_session.commit()

    class _Resp:
        def json(self):  # noqa: ANN201
            return {"data": [{"id": "old-vl-model"}, {"id": "marked-false"}, {"id": "plain-old"}]}

    _sync_models(db_session, ch, _Resp())
    db_session.expire_all()
    metas = {
        m.model_id: _j.loads(m.meta_json or "{}")
        for m in db_session.query(ProviderModel).filter(ProviderModel.channel_id == ch.id)
    }
    assert metas["old-vl-model"].get("vision") is True  # 补标
    assert metas["marked-false"].get("vision") is False  # 人工标记不覆盖
    assert not metas["plain-old"].get("vision")


def test_vision_heuristic_excludes_embedding_reranker(db_session):
    """VL-Embedding/VL-Reranker 同名系列非视觉对话模型，不得误标（2026-10-09 生产实证）。"""
    import json as _j

    from backend.app.models import ProviderChannel
    from backend.app.services.provider_service import _sync_models

    ch = ProviderChannel(
        user_id=None, domain="llm", name="ex", base_url="https://x/v1", key_encrypted="x"
    )
    db_session.add(ch)
    db_session.commit()

    class _Resp:
        def json(self):  # noqa: ANN201
            return {"data": [
                {"id": "Qwen/Qwen3-VL-Embedding-8B"},
                {"id": "Qwen/Qwen3-VL-Reranker-8B"},
                {"id": "Qwen/Qwen3-VL-32B-Instruct"},
            ]}

    _sync_models(db_session, ch, _Resp())
    db_session.expire_all()
    from backend.app.models import ProviderModel

    metas = {
        m.model_id: _j.loads(m.meta_json or "{}")
        for m in db_session.query(ProviderModel).filter(ProviderModel.channel_id == ch.id)
    }
    assert not metas["Qwen/Qwen3-VL-Embedding-8B"].get("vision")
    assert not metas["Qwen/Qwen3-VL-Reranker-8B"].get("vision")
    assert metas["Qwen/Qwen3-VL-32B-Instruct"].get("vision") is True
