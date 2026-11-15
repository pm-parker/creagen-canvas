"""resolve_model_pricing / resolve_cost：模型配置价优先，内置建议价回落。

spec: docs/superpowers/specs/2026-10-07-model-config.md §5
plan: docs/superpowers/plans/2026-10-07-model-config.md Task 3/4
"""

import asyncio

import pytest

from backend.app.services.cost_calculator import calculate_cost
from backend.app.services.model_pricing import resolve_cost, resolve_model_pricing
from backend.app.services.param_schema import merge_model_param_defaults


@pytest.fixture
def priced_channel(db_session):
    """按域建渠道 + 配置价格的模型行（渠道单域——G2 域过滤后测试数据须合规）。"""
    from backend.app.models import ProviderChannel, ProviderModel

    img = ProviderChannel(
        domain="image", name="priced", base_url="https://p.example.com/v1", key_encrypted=""
    )
    vid = ProviderChannel(
        domain="video", name="priced-vid", base_url="https://v.example.com/v1", key_encrypted=""
    )
    llm = ProviderChannel(
        domain="llm", name="priced-llm", base_url="https://l.example.com/v1", key_encrypted=""
    )
    aud = ProviderChannel(
        domain="audio", name="priced-aud", base_url="https://a.example.com/v1", key_encrypted=""
    )
    db_session.add_all([img, vid, llm, aud])
    db_session.flush()
    rows = [
        ProviderModel(
            channel_id=img.id, model_id="free-img", is_free=1,
            pricing_unit="per_image", price_input=9.9,  # is_free 优先，价格忽略
        ),
        ProviderModel(
            channel_id=img.id, model_id="img-0.5", pricing_unit="per_image", price_input=0.5
        ),
        ProviderModel(
            channel_id=img.id, model_id="img-free-priced", pricing_unit="per_image", price_input=0
        ),
        ProviderModel(
            channel_id=vid.id, model_id="vid-per-sec", pricing_unit="per_second", price_input=0.3
        ),
        ProviderModel(
            channel_id=aud.id, model_id="tts-per-char", pricing_unit="per_character",
            price_input=2.0,  # 元/千字符（spec §4 单位定义）
        ),
        ProviderModel(
            channel_id=llm.id, model_id="llm-token", pricing_unit="per_token_1m",
            price_input=2.0, price_output=8.0,
        ),
        ProviderModel(
            channel_id=img.id, model_id="fixed-req", pricing_unit="per_request", price_input=0.05
        ),
    ]
    db_session.add_all(rows)
    db_session.commit()
    return img


def _mk_second_channel(db_session, model_id: str, price: float):
    """第二个渠道同名不同价（Review Focus 4：跨渠道差异）。"""
    from backend.app.models import ProviderChannel, ProviderModel

    ch = ProviderChannel(
        domain="image", name="second", base_url="https://q.example.com/v1", key_encrypted=""
    )
    db_session.add(ch)
    db_session.flush()
    db_session.add(
        ProviderModel(
            channel_id=ch.id, model_id=model_id, pricing_unit="per_image", price_input=price
        )
    )
    db_session.commit()
    return ch


def test_resolve_returns_none_when_unconfigured(db_session):
    assert resolve_model_pricing(db_session, "no-such-model") is None


def test_resolve_channel_id_priority(db_session, priced_channel):
    """同名模型两渠道不同价：channel_id 精确优先，无 channel 归一取首个。"""
    _mk_second_channel(db_session, "img-0.5", 9.0)
    exact = resolve_model_pricing(db_session, "img-0.5", channel_id=priced_channel.id)
    assert exact == {
        "is_free": False, "unit": "per_image", "price_input": 0.5,
        "price_output": None, "meta": {},
    }
    # 归一：取任一配置（首个），只断言命中而非具体价
    norm = resolve_model_pricing(db_session, "img-0.5")
    assert norm is not None and norm["unit"] == "per_image"


def test_free_wins_over_unit_and_price(db_session, priced_channel):
    assert resolve_cost(db_session, "image", "free-img", {"image_count": 4}) == 0.0


def test_per_image_multiplies_count(db_session, priced_channel):
    assert resolve_cost(db_session, "image", "img-0.5", {"image_count": 2}) == 1.0
    assert resolve_cost(db_session, "image", "img-0.5", {}) == 0.5


def test_zero_price_is_valid_config(db_session, priced_channel):
    """Review Focus 3：price_input=0 是合法配置（白嫖渠道），返回 0。"""
    assert resolve_cost(db_session, "image", "img-free-priced", {}) == 0.0


def test_per_second_uses_duration(db_session, priced_channel):
    assert resolve_cost(db_session, "video", "vid-per-sec", {"duration": 10}) == 3.0
    assert resolve_cost(db_session, "video", "vid-per-sec", {}) == 1.8  # 默认 6s


def test_per_character_uses_text(db_session, priced_channel):
    text = "x" * 2000
    assert resolve_cost(db_session, "audio", "tts-per-char", {"text": text}) == pytest.approx(4.0)
    assert resolve_cost(db_session, "audio", "tts-per-char", {}) == 0.0


def test_per_token_1m_uses_usage(db_session, priced_channel):
    usage = {"prompt_tokens": 1_000_000, "completion_tokens": 500_000}
    assert resolve_cost(
        db_session, "llm", "llm-token", {}, usage=usage
    ) == pytest.approx(6.0)  # 2*1 + 8*0.5
    # 无 usage（预估阶段）→ 0，与现状一致
    assert resolve_cost(db_session, "llm", "llm-token", {}) == 0.0


def test_per_request_fixed(db_session, priced_channel):
    assert resolve_cost(db_session, "image", "fixed-req", {"image_count": 9}) == 0.05


def test_fallback_to_builtin_table(db_session):
    """未配置模型 → 内置建议价（决策 ④）。"""
    model = "Tongyi-MAI/Z-Image-Turbo"
    assert resolve_cost(db_session, "image", model, {}) == calculate_cost("image", model, {})
    # 再未命中内置表 → fallback 价
    assert resolve_cost(db_session, "image", "unknown-model-x", {}) == calculate_cost(
        "image", "unknown-model-x", {}
    )


# ---------- Task 4：计价调用点替换 + LLM 完成精确计价 ----------

REGISTER = {
    "username": "priceuser",
    "email": "price@example.com",
    "password": "password123",
    "invite_code": "letmein",
}


def _register(client):
    client.post("/api/auth/register", json=REGISTER)
    r = client.post(
        "/api/auth/login", json={"identifier": "priceuser", "password": "password123"}
    )
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_worker_uses_configured_price(client, worker_db, db_session):
    """配置价模型：estimated_cost 按配置（0.5/张），lifecycle 落账取该值。"""
    from backend.app.models import ProviderChannel, ProviderModel, Task
    from backend.worker import run_generation_task

    ch = ProviderChannel(
        domain="image", name="priced-w", base_url="https://pw.example.com/v1", key_encrypted=""
    )
    db_session.add(ch)
    db_session.flush()
    db_session.add(
        ProviderModel(
            channel_id=ch.id, model_id="cfg-price-img", pricing_unit="per_image", price_input=0.5
        )
    )
    db_session.commit()

    headers = _register(client)
    r = client.post(
        "/api/tasks",
        json={"type": "image", "prompt": "p", "model": "cfg-price-img", "channel_id": ch.id},
        headers=headers,
    )
    task_id = r.json()["task"]["id"]

    async def fake_gen(t, on_event=None):
        return {"status": "completed", "output_url": "x"}

    result = asyncio.run(run_generation_task({}, int(task_id), _gen_override=fake_gen))
    assert result["status"] == "completed"
    session = worker_db()
    task = session.get(Task, task_id)
    assert task is not None
    assert task.cost_amount == 0.5


def test_llm_usage_exact_cost(client, worker_db, db_session):
    """per_token_1m 配置：完成时按真实 usage 精确计价（2×1 + 8×0.5 = 6.0）。"""
    from backend.app.models import ProviderChannel, ProviderModel, Task
    from backend.worker import run_generation_task

    ch = ProviderChannel(
        domain="llm", name="llm-w", base_url="https://lw.example.com/v1", key_encrypted=""
    )
    db_session.add(ch)
    db_session.flush()
    db_session.add(
        ProviderModel(
            channel_id=ch.id,
            model_id="cfg-llm",
            pricing_unit="per_token_1m",
            price_input=2.0,
            price_output=8.0,
        )
    )
    db_session.commit()

    headers = _register(client)
    r = client.post(
        "/api/tasks",
        json={"type": "llm", "prompt": "p", "model": "cfg-llm", "channel_id": ch.id},
        headers=headers,
    )
    task_id = r.json()["task"]["id"]

    async def fake_gen(t, on_event=None):
        return {
            "status": "completed",
            "output_url": "x",
            "usage": {"prompt_tokens": 1_000_000, "completion_tokens": 500_000},
        }

    result = asyncio.run(run_generation_task({}, int(task_id), _gen_override=fake_gen))
    assert result["status"] == "completed"
    session = worker_db()
    task = session.get(Task, task_id)
    assert task is not None
    assert task.cost_amount == pytest.approx(6.0)


# ---------- Final review fix pass ----------

def test_per_character_uses_prompt_fallback(db_session, priced_channel):
    """Final Fix 1：per_character 计价基数从 prompt 取（TTS 文本在 prompt，非 params.text）。"""
    text = "x" * 2000
    assert resolve_cost(
        db_session, "audio", "tts-per-char", {}, prompt=text
    ) == pytest.approx(4.0)


def test_merge_never_overrides_reserved_keys(db_session):
    """Final Fix 2：params_json 不能劫持路由/协议保留键（channel_id/prompt_id）。"""
    import json as _json

    from backend.app.models import ProviderChannel, ProviderModel

    ch = ProviderChannel(
        domain="image", name="hijack-ch", base_url="https://h.example.com/v1", key_encrypted=""
    )
    db_session.add(ch)
    db_session.flush()
    db_session.add(
        ProviderModel(
            channel_id=ch.id,
            model_id="m-hijack",
            params_json=_json.dumps({"channel_id": 999, "prompt_id": 42, "size": "1024x1024"}),
        )
    )
    db_session.commit()
    merged = merge_model_param_defaults(db_session, "image", "m-hijack", {"seed": 1})
    assert "channel_id" not in merged
    assert "prompt_id" not in merged
    assert merged["size"] == "1024x1024"  # 普通键正常注入
    assert merged["seed"] == 1


# ---------- 复检二轮（spec 逐条对照）：查找回退 / 创建校验 / null 语义 ----------


def test_resolve_falls_back_to_cross_channel(db_session, priced_channel):
    """F1：指定渠道未配置 → 回退该 model_id 任意启用渠道（spec §5.1 查找顺序）。"""
    from backend.app.models import ProviderChannel

    bare = ProviderChannel(
        domain="image", name="bare-ch", base_url="https://bc.example.com/v1", key_encrypted=""
    )
    db_session.add(bare)
    db_session.flush()
    bare_id = int(bare.id)
    got = resolve_model_pricing(db_session, "img-0.5", channel_id=bare_id)  # bare 无该模型配置
    assert got is not None and got["unit"] == "per_image"
    assert got["meta"] == {}  # spec：Pricing 含 meta


def test_create_channel_models_validates_pricing(client, db_session):
    """F2：POST /channels 直配 models[] 同样校验计价（枚举/单价依赖）。"""
    from backend.app.services.provider_service import ChannelError, create_channel

    with pytest.raises(ChannelError):
        create_channel(
            db_session, user_id=1, domain="image", name="bad-unit",
            base_url="https://bu.example.com/v1", key_plain="",
            models=[{"model_id": "m", "pricing_unit": "per_gazillion"}],
        )
    with pytest.raises(ChannelError):
        create_channel(
            db_session, user_id=1, domain="image", name="no-price",
            base_url="https://np.example.com/v1", key_plain="",
            models=[{"model_id": "m", "pricing_unit": "per_image"}],
        )
    # 合法：免费无需单价
    ch = create_channel(
        db_session, user_id=1, domain="image", name="ok-free",
        base_url="https://ok.example.com/v1", key_plain="",
        models=[{"model_id": "m", "pricing_unit": "per_image", "is_free": True}],
    )
    assert ch.models[0].is_free == 1


def test_pricing_scopes_to_domain(db_session):
    """G2：计价按域过滤——llm 任务不吃 image 渠道同名模型的 per_image 价。"""

    from backend.app.models import ProviderChannel, ProviderModel

    img_ch = ProviderChannel(

        domain="image", name="img-p", base_url="https://ip.example.com/v1", key_encrypted=""


    )
    llm_ch = ProviderChannel(
        domain="llm", name="llm-p", base_url="https://lp.example.com/v1", key_encrypted=""
    )
    db_session.add_all([img_ch, llm_ch])
    db_session.flush()
    db_session.add_all([
        ProviderModel(
            channel_id=img_ch.id, model_id="dup-p", pricing_unit="per_image", price_input=9.9
        ),
        ProviderModel(
            channel_id=llm_ch.id, model_id="dup-p", pricing_unit="per_token_1m",
            price_input=2.0, price_output=8.0,
        ),
    ])
    db_session.commit()
    assert resolve_cost(db_session, "llm", "dup-p", {}) == 0.0  # 无 usage 的 llm 预估
    got = resolve_model_pricing(db_session, "dup-p", domain="llm")
    assert got is not None and got["unit"] == "per_token_1m"


def test_llm_completed_without_usage_estimates(client, worker_db, db_session):
    """G3：流式 LLM 无 usage——完成时按 len(prompt)*2 估算 input 侧计价（非恒 ¥0）。"""
    from backend.app.models import ProviderChannel, ProviderModel, Task
    from backend.worker import run_generation_task

    ch = ProviderChannel(
        domain="llm", name="llm-est", base_url="https://le.example.com/v1", key_encrypted=""
    )
    db_session.add(ch)
    db_session.flush()
    db_session.add(
        ProviderModel(
            channel_id=ch.id, model_id="cfg-llm-est",
            pricing_unit="per_token_1m", price_input=2.0, price_output=8.0,
        )
    )
    db_session.commit()

    headers = _register(client)
    prompt = "字" * 2000  # 估算 4000 tokens → 4000/1M*2 = 0.008
    r = client.post(
        "/api/tasks",
        json={"type": "llm", "prompt": prompt, "model": "cfg-llm-est", "channel_id": ch.id},
        headers=headers,
    )
    assert r.status_code in (200, 201), r.text
    task_id = int(r.json()["task"]["id"])

    async def fake_gen(t, on_event=None):  # 无 usage（模拟流式路径）
        return {"status": "completed", "output_url": "x"}

    result = asyncio.run(run_generation_task({}, task_id, _gen_override=fake_gen))
    assert result["status"] == "completed"
    task = worker_db().get(Task, task_id)
    assert task is not None
    assert task.cost_amount == pytest.approx(0.008)
