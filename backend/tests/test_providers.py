"""provider 渠道契约（Review Focus #1/#2：key 泄露与未配置提交）。"""

from datetime import UTC, datetime, timedelta

import httpx
import respx

REGISTER = {
    "username": "provuser",
    "email": "prov@example.com",
    "password": "password123",
    "invite_code": "letmein",
}
SECRET_KEY = "sk-live-abcd1234efgh5678"


def _headers(client) -> dict[str, str]:
    r = client.post("/api/auth/register", json=REGISTER)
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _create_channel(client, headers, **overrides):
    body = {
        "domain": "llm",
        "name": "my-llm",
        "base_url": "https://api.example.com/v1",
        "key": SECRET_KEY,
        **overrides,
    }
    return client.post("/api/providers/channels", json=body, headers=headers)


def test_key_never_leaks_in_response(client):
    """Review Focus #1：列表/详情响应不含明文 key，只含掩码。"""
    headers = _headers(client)
    r = _create_channel(client, headers)
    assert r.status_code == 201, r.text

    r = client.get("/api/providers/channels", headers=headers)
    raw = r.text
    assert SECRET_KEY not in raw, "响应泄露明文 key"
    assert "key_encrypted" not in raw, "响应不应回传密文"
    assert "key_masked" in raw
    masked = r.json()["channels"][0]["key_masked"]
    assert "abcd" not in masked, "掩码不应包含 key 中段"
    assert masked.endswith("5678")


def test_key_stored_encrypted(client, db_session):
    headers = _headers(client)
    _create_channel(client, headers)
    from backend.app.models import ProviderChannel

    ch = db_session.query(ProviderChannel).first()
    assert ch is not None
    assert ch.key_encrypted.startswith("v1:")
    assert ch.key_encrypted != SECRET_KEY
    assert SECRET_KEY not in ch.key_encrypted


def test_invalid_base_url_rejected(client):
    headers = _headers(client)
    r = _create_channel(client, headers, base_url="ftp://nope")
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "CHANNEL_URL_INVALID"


def test_invalid_domain_rejected(client):
    headers = _headers(client)
    r = _create_channel(client, headers, domain="podcast")
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "CHANNEL_DOMAIN_INVALID"


def test_create_channel_persists_capabilities(client, db_session):
    """BUG-A 回归：新建渠道时 capabilities 必须落库并回显（J4 edit 能力勾选依赖）。"""
    headers = _headers(client)
    r = _create_channel(client, headers, capabilities=["edit"])
    assert r.status_code == 201, r.text
    assert r.json()["capabilities"] == ["edit"]

    from backend.app.models import ProviderChannel

    ch = db_session.query(ProviderChannel).order_by(ProviderChannel.id.desc()).first()
    assert ch is not None
    assert ch._capabilities_list() == ["edit"]


def test_update_channel_rotates_key(client):
    headers = _headers(client)
    ch_id = _create_channel(client, headers).json()["id"]
    r = client.patch(
        f"/api/providers/channels/{ch_id}",
        json={"key": "sk-new-key-9876", "enabled": False},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["enabled"] is False
    assert "sk-new-key-9876" not in r.text


def test_delete_channel(client):
    headers = _headers(client)
    ch_id = _create_channel(client, headers).json()["id"]
    assert client.delete(f"/api/providers/channels/{ch_id}", headers=headers).status_code == 204
    assert client.get("/api/providers/channels", headers=headers).json()["channels"] == []


def test_channel_isolated_between_users(client):
    """用户隔离：alice 的渠道，mallory 不可见/不可改。"""
    import uuid

    r1 = client.post(
        "/api/auth/register",
        json={
            "username": "alice",
            "email": "alice@p.com",
            "password": "password123",
            "invite_code": "letmein",
        },
    )
    alice = {"Authorization": f"Bearer {r1.json()['access_token']}"}
    ch_id = _create_channel(client, alice).json()["id"]

    tag = uuid.uuid4().hex[:6]
    r2 = client.post(
        "/api/auth/register",
        json={
            "username": f"mallory{tag}",
            "email": f"m{tag}@p.com",
            "password": "password123",
            "invite_code": "letmein",
        },
    )
    mallory = {"Authorization": f"Bearer {r2.json()['access_token']}"}
    r = client.patch(f"/api/providers/channels/{ch_id}", json={"enabled": False}, headers=mallory)
    assert r.status_code == 404


def test_test_endpoint_unreachable_502(client):
    """不可达 base_url 的连通性测试 → ok=False + PROVIDER_UNREACHABLE。"""
    headers = _headers(client)
    ch_id = _create_channel(
        client,
        headers,
        base_url="http://127.0.0.1:9/v1",  # discard 端口，必拒绝
    ).json()["id"]
    r = client.post(f"/api/providers/channels/{ch_id}/test", headers=headers)
    assert r.status_code == 200  # 端点本身成功，测试结果体表达失败
    body = r.json()
    assert body["ok"] is False
    assert body["code"] == "PROVIDER_UNREACHABLE"


def test_submit_without_channel_422(client, monkeypatch):
    """Review Focus #2：无渠道且禁用 FakeProvider → 422 PROVIDER_NOT_CONFIGURED。"""
    headers = _headers(client)
    monkeypatch.setenv("ENABLE_FAKE_PROVIDER", "false")
    from backend.app.core.config import get_settings

    get_settings.cache_clear()
    r = client.post(
        "/api/tasks",
        json={"type": "image", "prompt": "p", "model": "whatever"},
        headers=headers,
    )
    get_settings.cache_clear()
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "PROVIDER_NOT_CONFIGURED"
    assert "hint" in r.json()["detail"]


def test_disabled_channel_rejected_on_submit(client, db_session, monkeypatch):
    """Review Focus #5：禁用渠道参与解析被跳过（Fake 关闭）→ 422。"""
    headers = _headers(client)
    ch_id = _create_channel(client, headers).json()["id"]
    client.patch(f"/api/providers/channels/{ch_id}", json={"enabled": False}, headers=headers)

    monkeypatch.setenv("ENABLE_FAKE_PROVIDER", "false")
    from backend.app.core.config import get_settings

    get_settings.cache_clear()
    try:
        r = client.post(
            "/api/tasks",
            json={"type": "llm", "prompt": "p", "model": "gpt-x"},
            headers=headers,
        )
    finally:
        get_settings.cache_clear()
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "PROVIDER_NOT_CONFIGURED"


@respx.mock
def test_connection_syncs_models_catalog(client, db_session, respx_mock):
    """连通测试成功后 /models 目录 upsert 到 provider_models。"""
    headers = _headers(client)
    ch_id = _create_channel(client, headers, base_url="https://img.example.com/v1").json()["id"]

    respx_mock.get("https://img.example.com/v1/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "img-1"}, {"id": "img-2"}]})
    )
    r = client.post(f"/api/providers/channels/{ch_id}/test", headers=headers)
    assert r.status_code == 200
    assert r.json()["models_found"] == 2

    from backend.app.models import ProviderModel

    models = db_session.query(ProviderModel).filter(ProviderModel.channel_id == ch_id).all()
    assert {m.model_id for m in models} == {"img-1", "img-2"}

    # 幂等：二次同步不重复
    client.post(f"/api/providers/channels/{ch_id}/test", headers=headers)
    models2 = db_session.query(ProviderModel).filter(ProviderModel.channel_id == ch_id).all()
    assert len(models2) == 2


def _admin_headers(client, db_session):
    from backend.app.models import User

    user = User(username="adminsys", email="adminsys@x.io", password_hash="x", role="admin")
    db_session.add(user)
    db_session.commit()
    from backend.app.services.auth_service import create_access_token

    return {"Authorization": f"Bearer {create_access_token(user)}"}


def test_admin_can_update_system_channel(client, db_session):
    """系统渠道（user_id NULL）管理员可改 key/能力——修复前 get_channel_for_user 直接 404。"""
    headers = _admin_headers(client, db_session)
    from backend.app.models import ProviderChannel

    ch = ProviderChannel(
        user_id=None,
        domain="llm",
        name="系统LLM",
        base_url="https://api.example.com/v1",
        key_encrypted="v1:xxx",
    )
    db_session.add(ch)
    db_session.commit()

    r = client.patch(
        f"/api/providers/channels/{ch.id}",
        json={"key": "sk-new-sys-key", "capabilities": ["edit"]},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["key_masked"].endswith("-key") or r.json()["key_masked"]
    assert r.json()["capabilities"] == ["edit"]


def test_non_admin_cannot_update_system_channel(client, db_session):
    headers = _headers(client)
    from backend.app.models import ProviderChannel

    ch = ProviderChannel(
        user_id=None,
        domain="llm",
        name="系统LLM2",
        base_url="https://api.example.com/v1",
        key_encrypted="v1:xxx",
    )
    db_session.add(ch)
    db_session.commit()

    r = client.patch(
        f"/api/providers/channels/{ch.id}", json={"name": "改名"}, headers=headers
    )
    assert r.status_code == 404


def test_channel_health_by_model(client, db_engine):
    """渠道健康度（0.7.1）：近 7 天按模型聚合 成功/失败/成功率。"""
    headers = _headers(client)
    from sqlalchemy import text
    from sqlalchemy.orm import sessionmaker

    now = datetime.now(UTC)
    cases = [
        ("m-good", "completed"),
        ("m-good", "completed"),
        ("m-good", "failed"),
        ("m-bad", "failed"),
    ]
    for i, (model, _status) in enumerate(cases):
        created = (now - timedelta(days=i % 3)).isoformat()
        r = client.post(
            "/api/tasks",
            json={"type": "image", "prompt": f"t{i}", "model": model, "created_at": created},
            headers=headers,
        )
        assert r.status_code == 200, r.text

    # 直接推进状态（测试库无 worker）
    s = sessionmaker(bind=db_engine)()
    s.execute(text("UPDATE tasks SET status = 'completed' WHERE model = 'm-good' AND id % 2 = 0"))
    s.execute(text("UPDATE tasks SET status = 'failed' WHERE model = 'm-good' AND id % 2 = 1"))
    s.execute(text("UPDATE tasks SET status = 'failed' WHERE model = 'm-bad'"))
    s.commit()
    s.close()

    r = client.get("/api/providers/health?days=7", headers=headers)
    assert r.status_code == 200, r.text
    data = r.json()["health"]
    assert isinstance(data, list) and len(data) >= 1
    entry = next(e for e in data if e["model"] == "m-good")
    assert entry["total"] == 3
    assert entry["failed"] == 2
    assert entry["completed"] == 1
    assert entry["success_rate"] is not None and 0 < entry["success_rate"] < 100


def test_health_excludes_fully_disabled_models(client, db_session):
    """健康度重设计：全渠道禁用的模型不再出现在健康面板（bge 类噪音）。"""
    import uuid as _uuid

    from backend.app.models import ProviderChannel, ProviderModel, Task

    name = f"hp_{_uuid.uuid4().hex[:8]}"
    client.post(
        "/api/auth/register",
        json={
            "username": name, "email": f"{name}@x.io",
            "password": "password123", "invite_code": "letmein",
        },
    )
    r = client.post("/api/auth/login", json={"identifier": name, "password": "password123"})
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    uid = r.json().get("user", {}).get("id") or 1

    ch = ProviderChannel(
        domain="image", name="hp-ch", base_url="https://hp.example.com/v1", key_encrypted=""
    )
    db_session.add(ch)
    db_session.flush()
    db_session.add_all([
        ProviderModel(channel_id=ch.id, model_id="hp-disabled", enabled=0),
        ProviderModel(channel_id=ch.id, model_id="hp-alive", enabled=1),
    ])
    db_session.commit()
    db_session.add_all([
        Task(user_id=uid, type="image", prompt="a", model="hp-disabled", status="failed"),
        Task(user_id=uid, type="image", prompt="b", model="hp-alive", status="completed"),
    ])
    db_session.commit()

    resp = client.get("/api/providers/health", headers=headers)
    models = [h["model"] for h in resp.json()["health"]]
    assert "hp-disabled" not in models
    assert "hp-alive" in models
