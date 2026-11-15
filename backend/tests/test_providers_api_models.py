"""模型级配置 API：POST channels 带 models + PATCH channels/{cid}/models/{mid}。

spec: docs/superpowers/specs/2026-10-07-model-config.md §7
plan: docs/superpowers/plans/2026-10-07-model-config.md Task 7
"""

import uuid


def _register(client):
    name = f"pm_{uuid.uuid4().hex[:8]}"
    client.post(
        "/api/auth/register",
        json={
            "username": name, "email": f"{name}@x.io",
            "password": "password123", "invite_code": "letmein",
        },
    )
    r = client.post("/api/auth/login", json={"identifier": name, "password": "password123"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _create_channel(client, headers, models=None):
    body = {
        "domain": "image", "name": "ch-models", "base_url": "https://cm.example.com/v1",
        "key": "sk-x",
    }
    if models is not None:
        body["models"] = models
    return client.post("/api/providers/channels", json=body, headers=headers)


def test_create_channel_with_models(client):
    """POST channels 带 models[]：落库 + 回显新字段默认值。"""
    headers = _register(client)
    r = _create_channel(
        client, headers,
        models=[{"model_id": "m-create", "display_name": "创建直配"}],
    )
    assert r.status_code == 201, r.text
    models = r.json()["models"]
    assert [m["model_id"] for m in models] == ["m-create"]
    assert models[0]["display_name"] == "创建直配"
    assert models[0]["params_json"] == {} and models[0]["is_free"] is False
    assert models[0]["pricing_unit"] is None


def test_patch_model_pricing_fields(client):
    """PATCH models/{mid}：价格/免费/参数生效并回显。"""
    headers = _register(client)
    r = _create_channel(client, headers, models=[{"model_id": "m-patch"}])
    ch_id = r.json()["id"]
    pk = r.json()["models"][0]["id"]
    r2 = client.patch(
        f"/api/providers/channels/{ch_id}/models/{pk}",
        json={
            "pricing_unit": "per_token_1m", "price_input": 2.0, "price_output": 8.0,
            "params_json": {"size": "1024x1024"}, "meta_json": {"vision": True},
        },
        headers=headers,
    )
    assert r2.status_code == 200, r2.text
    m = r2.json()["models"][0]
    assert m["pricing_unit"] == "per_token_1m"
    assert m["price_input"] == 2.0 and m["price_output"] == 8.0
    assert m["params_json"] == {"size": "1024x1024"}
    assert m["meta_json"] == {"vision": True}


def test_patch_model_invalid_unit_422(client):
    headers = _register(client)
    r = _create_channel(client, headers, models=[{"model_id": "m-bad-unit"}])
    ch_id = r.json()["id"]
    pk = r.json()["models"][0]["id"]
    r2 = client.patch(
        f"/api/providers/channels/{ch_id}/models/{pk}",
        json={"pricing_unit": "per_gazillion"},
        headers=headers,
    )
    assert r2.status_code == 422


def test_patch_model_bad_params_json_422(client):
    """params_json 必须是 JSON 对象（Review Focus 2 存量防线）。"""
    headers = _register(client)
    r = _create_channel(client, headers, models=[{"model_id": "m-bad-json"}])
    ch_id = r.json()["id"]
    pk = r.json()["models"][0]["id"]
    r2 = client.patch(
        f"/api/providers/channels/{ch_id}/models/{pk}",
        json={"params_json": "not-a-dict"},
        headers=headers,
    )
    assert r2.status_code == 422


def test_patch_model_price_required_when_priced(client):
    """pricing_unit 配置且非免费时 price_input 必填。"""
    headers = _register(client)
    r = _create_channel(client, headers, models=[{"model_id": "m-no-price"}])
    ch_id = r.json()["id"]
    pk = r.json()["models"][0]["id"]
    r2 = client.patch(
        f"/api/providers/channels/{ch_id}/models/{pk}",
        json={"pricing_unit": "per_image"},
        headers=headers,
    )
    assert r2.status_code == 422
    # is_free=1 时无需 price_input
    r3 = client.patch(
        f"/api/providers/channels/{ch_id}/models/{pk}",
        json={"is_free": True, "pricing_unit": "per_image"},
        headers=headers,
    )
    assert r3.status_code == 200


def test_patch_model_not_owner_404(client):
    """他人渠道 → 404（owner 语义沿用 get_channel_for_user）。"""
    h1 = _register(client)
    r = _create_channel(client, h1, models=[{"model_id": "m-owner"}])
    ch_id = r.json()["id"]
    pk = r.json()["models"][0]["id"]
    h2 = _register(client)
    r2 = client.patch(
        f"/api/providers/channels/{ch_id}/models/{pk}",
        json={"display_name": "劫持"},
        headers=h2,
    )
    assert r2.status_code == 404


def test_patch_model_not_found_404(client):
    headers = _register(client)
    r = _create_channel(client, headers, models=[{"model_id": "m-exists"}])
    ch_id = r.json()["id"]
    r2 = client.patch(
        f"/api/providers/channels/{ch_id}/models/999999",
        json={"display_name": "x"},
        headers=headers,
    )
    assert r2.status_code == 404


# ---------- 复检修复：合并态校验 + null 清除语义 ----------


def test_patch_model_merges_free_state_for_validation(client):
    """复检 Bug A：已免费模型补配置计价单位（不填价）应按合并态放行。"""
    headers = _register(client)
    r = _create_channel(client, headers, models=[{"model_id": "m-merge-free"}])
    ch_id = r.json()["id"]
    pk = r.json()["models"][0]["id"]
    r0 = client.patch(
        f"/api/providers/channels/{ch_id}/models/{pk}",
        json={"is_free": True},
        headers=headers,
    )
    assert r0.status_code == 200, r0.text
    r1 = client.patch(
        f"/api/providers/channels/{ch_id}/models/{pk}",
        json={"pricing_unit": "per_image"},
        headers=headers,
    )
    assert r1.status_code == 200, r1.text  # 合并态 is_free=1 → 单价非必填


def test_patch_model_can_clear_pricing_with_null(client):
    """复检 Bug B：显式 null 清除计价配置（前端「未配置」选项语义）。"""
    headers = _register(client)
    r = _create_channel(client, headers, models=[{"model_id": "m-clear"}])
    ch_id = r.json()["id"]
    pk = r.json()["models"][0]["id"]
    r0 = client.patch(
        f"/api/providers/channels/{ch_id}/models/{pk}",
        json={"pricing_unit": "per_image", "price_input": 0.5},
        headers=headers,
    )
    assert r0.status_code == 200
    r1 = client.patch(
        f"/api/providers/channels/{ch_id}/models/{pk}",
        json={"pricing_unit": None, "price_input": None},
        headers=headers,
    )
    assert r1.status_code == 200, r1.text
    m = r1.json()["models"][0]
    assert m["pricing_unit"] is None
    assert m["price_input"] is None


# ---------- 五遍复检：斜杠 ID 路由 / 跨域污染 / 流式估算 ----------


def test_patch_model_by_pk_supports_slashed_model_id(client):
    """G1（Critical）：SiliconFlow 风格含 '/' 的 model_id 必须可配置——按数字主键寻址。"""
    headers = _register(client)
    r = _create_channel(
        client, headers, models=[{"model_id": "Tongyi-MAI/Z-Image-Turbo", "display_name": "涡轮"}]
    )
    ch = r.json()
    pk = ch["models"][0]["id"]
    r2 = client.patch(
        f"/api/providers/channels/{ch['id']}/models/{pk}",
        json={"pricing_unit": "per_image", "price_input": 0.3},
        headers=headers,
    )
    assert r2.status_code == 200, r2.text
    m = r2.json()["models"][0]
    assert m["model_id"] == "Tongyi-MAI/Z-Image-Turbo"
    assert m["price_input"] == 0.3


def test_patch_model_null_clears_params_meta(client):
    """F3：params_json/meta_json 显式 null 同样清除（与价格字段语义一致）。"""
    headers = _register(client)
    r = client.post(
        "/api/providers/channels",
        json={
            "domain": "image", "name": "null-clear", "base_url": "https://nc.example.com/v1",
            "key": "sk-x",
            "models": [
                {"model_id": "m-nc", "params_json": {"size": "1x1"}, "meta_json": {"vision": True}},
            ],
        },
        headers=headers,
    )
    ch_id = r.json()["id"]
    pk = r.json()["models"][0]["id"]
    r1 = client.patch(
        f"/api/providers/channels/{ch_id}/models/{pk}",
        json={"params_json": None, "meta_json": None},
        headers=headers,
    )
    assert r1.status_code == 200, r1.text
    m = r1.json()["models"][0]
    assert m["params_json"] == {}
    assert m["meta_json"] == {}
