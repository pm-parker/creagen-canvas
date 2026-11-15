"""参数域 schema：四域语义键 + 透传声明 + 占位示例。

spec: docs/superpowers/specs/2026-10-07-model-config.md §6.1
plan: docs/superpowers/plans/2026-10-07-model-config.md Task 2
"""

from backend.app.services.param_schema import (
    PARAM_SCHEMA,
    merge_model_param_defaults,
    param_placeholder,
    passthrough_keys,
)


def test_schema_covers_four_domains_and_legacy_keys():
    assert set(PARAM_SCHEMA) == {"image", "video", "llm", "audio"}
    # 原三键透传行为保持（Global Constraints 兼容承诺）
    assert {"size", "negative_prompt", "i2i_denoise"} <= set(passthrough_keys("image"))
    assert set(passthrough_keys("llm")) >= {"temperature", "max_tokens", "top_p"}
    assert set(passthrough_keys("video")) >= {"duration", "resolution"}
    assert set(passthrough_keys("audio")) >= {"voice", "speed", "format"}


def test_spec_shape_and_falsy_safe():
    # 每个键都有 type/passthrough/placeholder（驱动 UI 占位与透传白名单）
    for _domain, keys in PARAM_SCHEMA.items():
        for _key, spec in keys.items():
            assert spec["type"] in {"str", "int", "float", "bool"}
            assert isinstance(spec["passthrough"], bool)
            assert isinstance(spec["placeholder"], str)
    # image_count 是内部乘数，不上游
    assert PARAM_SCHEMA["image"]["image_count"]["passthrough"] is False
    # seed 可为 0（falsy 合法值，透传判定用 is not None 而非 truthy）
    assert PARAM_SCHEMA["image"]["seed"]["passthrough"] is True


def test_param_placeholder_shape():
    ph = param_placeholder("video")
    assert "duration" in ph and "resolution" in ph
    assert param_placeholder("unknown-domain") == {}


def test_merge_defaults_explicit_wins(db_session):
    import json

    from backend.app.models import ProviderChannel, ProviderModel

    ch = ProviderChannel(
        domain="image", name="ch", base_url="https://x.example.com/v1", key_encrypted=""
    )
    db_session.add(ch)
    db_session.flush()
    db_session.add(
        ProviderModel(
            channel_id=ch.id,
            model_id="m-merge",
            params_json=json.dumps({"size": "1024x1024", "seed": 7}),
        )
    )
    db_session.commit()

    # 默认注入
    assert merge_model_param_defaults(db_session, "image", "m-merge", None) == {
        "size": "1024x1024",
        "seed": 7,
    }
    # 显式覆盖默认（浅合并）
    assert merge_model_param_defaults(db_session, "image", "m-merge", {"size": "2048x2048"}) == {
        "size": "2048x2048",
        "seed": 7,
    }
    # 未配置模型 → explicit 原样
    assert merge_model_param_defaults(db_session, "image", "no-such-model", {"a": 1}) == {"a": 1}
    assert merge_model_param_defaults(db_session, "image", "no-such-model", None) == {}
    # seed=0 显式传入不被丢弃（falsy 覆盖默认 7）
    assert merge_model_param_defaults(db_session, "image", "m-merge", {"seed": 0})["seed"] == 0


def test_merge_tolerates_bad_json(db_session):
    from backend.app.models import ProviderChannel, ProviderModel

    ch = ProviderChannel(
        domain="image", name="ch2", base_url="https://y.example.com/v1", key_encrypted=""
    )
    db_session.add(ch)
    db_session.flush()
    db_session.add(ProviderModel(channel_id=ch.id, model_id="m-bad", params_json="not-json{"))
    db_session.commit()
    # 坏 JSON 容错：回 explicit 原样（Review Focus 2 读取侧）
    assert merge_model_param_defaults(db_session, "image", "m-bad", {"b": 2}) == {"b": 2}


# ---------- Task 5：任务创建入口注入默认参数 ----------


def _mk_client_channel(db_session, model_id: str, params: dict, domain: str = "image"):
    import json as _json

    from backend.app.models import ProviderChannel, ProviderModel

    ch = ProviderChannel(
        domain=domain, name="inject-ch", base_url="https://inj.example.com/v1", key_encrypted=""
    )
    db_session.add(ch)
    db_session.flush()
    db_session.add(
        ProviderModel(channel_id=ch.id, model_id=model_id, params_json=_json.dumps(params))
    )
    db_session.commit()
    return ch


def test_task_creation_injects_model_defaults(client, worker_db, db_session):
    """POST /api/tasks：默认参数注入 + 显式覆盖（显式 seed=0 覆盖默认 7）。"""
    ch = _mk_client_channel(db_session, "cfg-inject", {"size": "2048x2048", "seed": 7})
    client.post(
        "/api/auth/register",
        json={
            "username": "injectuser", "email": "inj@x.io",
            "password": "password123", "invite_code": "letmein",
        },
    )
    r = client.post("/api/auth/login", json={"identifier": "injectuser", "password": "password123"})
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

    r = client.post(
        "/api/tasks",
        json={
            "type": "image", "prompt": "p", "model": "cfg-inject",
            "channel_id": ch.id, "params": {"seed": 0},
        },
        headers=headers,
    )
    assert r.status_code in (200, 201), r.text
    params = r.json()["task"]["params"]
    assert params["size"] == "2048x2048"  # 默认注入
    assert params["seed"] == 0  # 显式覆盖（falsy 合法）


def test_task_creation_unconfigured_model_no_injection(client, worker_db, db_session):
    """未配置模型：params 与请求原样一致（兼容承诺）。"""
    client.post(
        "/api/auth/register",
        json={
            "username": "injectuser2", "email": "inj2@x.io",
            "password": "password123", "invite_code": "letmein",
        },
    )
    r = client.post(
        "/api/auth/login", json={"identifier": "injectuser2", "password": "password123"}
    )
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    r = client.post(
        "/api/tasks",
        json={"type": "image", "prompt": "p", "model": "plain-model", "params": {"a": 1}},
        headers=headers,
    )
    assert r.json()["task"]["params"] == {"a": 1}


def test_merge_scopes_to_domain(db_session):
    """G2：同名模型跨域不污染——llm 任务不合并 image 渠道同名模型的参数。"""
    import json as _json

    from backend.app.models import ProviderChannel, ProviderModel

    img_ch = ProviderChannel(

        domain="image", name="img-d", base_url="https://i.example.com/v1", key_encrypted=""


    )
    llm_ch = ProviderChannel(
        domain="llm", name="llm-d", base_url="https://l.example.com/v1", key_encrypted=""
    )
    db_session.add_all([img_ch, llm_ch])
    db_session.flush()
    db_session.add_all([
        ProviderModel(
            channel_id=img_ch.id, model_id="dup-x", params_json=_json.dumps({"size": "1x1"})
        ),
        ProviderModel(
            channel_id=llm_ch.id, model_id="dup-x", params_json=_json.dumps({"temperature": 0.5})
        ),
    ])
    db_session.commit()
    assert merge_model_param_defaults(db_session, "llm", "dup-x", None) == {"temperature": 0.5}
    assert merge_model_param_defaults(db_session, "image", "dup-x", None) == {"size": "1x1"}
