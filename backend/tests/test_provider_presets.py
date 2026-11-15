"""中立模型预设目录（BYOK）：常用开放服务商的接入配置，用户自带 Key。"""

REGISTER = {
    "username": "presetuser",
    "email": "preset@example.com",
    "password": "password123",
    "invite_code": "letmein",
}


def _headers(client) -> dict[str, str]:
    r = client.post("/api/auth/register", json=REGISTER)
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_presets_requires_auth(client):
    assert client.get("/api/providers/presets").status_code == 401


def test_presets_catalog_structure_and_neutrality(client):
    """目录契约：字段齐全、含主流开放服务商（中立平台不绑单一商业 API）、域合法。"""
    from backend.app.models.provider import PROVIDER_DOMAINS
    from backend.app.routers.providers import PROVIDER_PRESETS

    r = client.get("/api/providers/presets", headers=_headers(client))
    assert r.status_code == 200
    presets = r.json()["presets"]
    assert len(presets) >= 8

    keys = set()
    for p in presets:
        # 字段契约（同一服务商可跨域多条：id+domain 唯一）
        k = (p["id"], p["domain"])
        assert p["id"] and k not in keys, p
        keys.add(k)
        assert p["displayName"]
        assert p["baseUrl"].startswith(("http://", "https://"))
        assert p["domain"] in PROVIDER_DOMAINS
        assert isinstance(p["defaultModels"], list) and p["defaultModels"]
        assert isinstance(p["capabilities"], list)
        assert p["keyUrl"].startswith("http")
        assert isinstance(p["requiresKey"], bool)
        assert p["note"] == "" or isinstance(p["note"], str)

    # 中立性：主流开放服务商覆盖（LLM 至少 5 家、含 DeepSeek）
    llm = {p["id"] for p in presets if p["domain"] == "llm"}
    assert {"deepseek", "siliconflow", "moonshot", "zhipu", "openai"} <= llm, llm
    # image/video 域也有预设
    assert any(p["domain"] == "image" for p in presets)
    assert any(p["domain"] == "video" for p in presets)
    # 本地推理（无需 Key）在目录中
    assert any(not p["requiresKey"] for p in presets)

    # 与模块内常量一致
    assert len(PROVIDER_PRESETS) == len(presets)


def test_presets_capability_hints(client):
    """能力提示：SiliconFlow 图片预设带 edit_ref（参考图编辑协议）。"""
    r = client.get("/api/providers/presets", headers=_headers(client))
    sf_image = next(
        p for p in r.json()["presets"] if p["id"] == "siliconflow" and p["domain"] == "image"
    )
    assert "edit_ref" in sf_image["capabilities"]


def test_create_channel_with_empty_key_for_local(client, db_session):
    """本地推理（Ollama）允许空 Key：创建成功且掩码为空。"""
    headers = _headers(client)
    r = client.post(
        "/api/providers/channels",
        json={
            "domain": "llm",
            "name": "本地 Ollama",
            "base_url": "http://localhost:11434/v1",
            "key": "",
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    assert r.json()["key_masked"] == ""

    # 列表可见
    r = client.get("/api/providers/channels", headers=headers)
    assert any(c["name"] == "本地 Ollama" for c in r.json()["channels"])
