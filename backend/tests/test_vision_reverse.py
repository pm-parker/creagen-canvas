"""vision_reverse 单测：真·看图反推（vision content blocks + 默认视觉模型解析）。"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from backend.app.services import vision_reverse as vr


@pytest.fixture
def user(db_session):  # noqa: ANN001
    from backend.app.models import User

    u = User(username="vr_user", email="vr@t.local", password_hash="x")
    db_session.add(u)
    db_session.commit()
    return u


def _mk_vision_channel(db_session, tmp_path, write_img=True):  # noqa: ANN001
    """系统级 llm 渠道 + vision 模型 + 一张站内测试图。"""

    from backend.app.models import ProviderChannel, ProviderModel

    ch = ProviderChannel(
        user_id=None,
        domain="llm",
        name="VL",
        base_url="https://vl.example/v1",
        key_encrypted="enc:k",
    )
    db_session.add(ch)
    db_session.commit()
    db_session.add(ProviderModel(channel_id=ch.id, model_id="vl-32b", meta_json='{"vision": true}'))
    db_session.commit()
    if write_img:
        img_dir = Path(vr._outputs_root()) / "images"
        img_dir.mkdir(parents=True, exist_ok=True)
        (img_dir / "test_vr.png").write_bytes(b"\x89PNG fake")
        url = "/outputs/images/test_vr.png"
    else:
        url = None
    return ch, url


def test_reverse_no_vision_default_400(db_session, user):
    import asyncio

    with pytest.raises(vr.VisionReverseError) as ei:
        asyncio.run(vr.reverse_prompt(db_session, user=user, image_url="/outputs/images/x.png"))
    assert ei.value.code == "VISION_NOT_CONFIGURED"


def test_reverse_builds_image_block_and_parses(db_session, user, tmp_path, monkeypatch):
    """命中默认视觉模型 → 请求含 image_url data 块 + 结构化提示词；返回剥离 think 的结果。"""
    import asyncio

    ch, url = _mk_vision_channel(db_session, tmp_path)
    captured = {}

    from backend.app.providers.openai_compat import OpenAICompatProvider

    class _P(OpenAICompatProvider):
        def __init__(self):  # noqa: ANN204
            self.model = "vl-32b"
            self.base_url = "https://vl.example/v1"
            self.key_plain = "sk-x"

    monkeypatch.setattr(
        vr, "_resolve_vision_provider", lambda db, uid: (_P(), ch)
    )

    class _Resp:
        status_code = 200

        def json(self):  # noqa: ANN201
            return {
                "choices": [
                    {"message": {"content": "<think>看图…</think>一位少女在窗边，暖色晨光……"}}
                ],
                "usage": {"prompt_tokens": 900, "completion_tokens": 80},
            }

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):  # noqa: ANN002
            return False

        async def post(self, u, **kw):  # noqa: ANN001
            captured.update(kw)
            return _Resp()

    monkeypatch.setattr(vr.httpx, "AsyncClient", lambda **kw: _Client())

    r = asyncio.run(vr.reverse_prompt(db_session, user=user, image_url=url))
    assert "少女" in r["reversed"] and "think" not in r["reversed"]
    assert r["model"] == "vl-32b"
    # 请求体：多模态 content blocks（text + image_url data:base64）
    body = captured["json"]
    content = body["messages"][1]["content"]
    kinds = [c["type"] for c in content]
    assert kinds == ["text", "image_url"]
    b64 = content[1]["image_url"]["url"]
    assert b64.startswith("data:image/")
    assert base64.b64decode(b64.split(",", 1)[1]) == b"\x89PNG fake"
    # 六要素引导在 system 提示词里
    system_text = body["messages"][0]["content"]
    assert "主体" in system_text and "光线" in system_text and "不编造" in system_text
    # 预算记账
    from backend.app.services.llm_budget import budget_status

    assert budget_status(db_session, f"llm:{user.id}")["used"] == 980


def test_reverse_rejects_path_traversal(db_session, user):
    import asyncio

    with pytest.raises(vr.VisionReverseError) as ei:
        asyncio.run(
            vr.reverse_prompt(db_session, user=user, image_url="/outputs/../etc/passwd")
        )
    assert ei.value.code in ("IMAGE_INVALID", "VISION_NOT_CONFIGURED")


def test_reverse_endpoint_contract(client, db_session, user, monkeypatch):
    """REST 契约：成功路径 + VISION_NOT_CONFIGURED 400（前端据此回落 v1）。"""


    # 未配置 → 400
    r = client.post(
        "/api/prompts/reverse", headers=_h(client, user), json={"url": "/outputs/images/a.png"}
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "VISION_NOT_CONFIGURED"

    # 成功路径（mock 服务层）
    async def fake(db, *, user, image_url):  # noqa: ANN001
        return {"reversed": "一位少女…", "model": "vl", "channel_id": 9}

    monkeypatch.setattr("backend.app.services.vision_reverse.reverse_prompt", fake)
    r2 = client.post(
        "/api/prompts/reverse", headers=_h(client, user), json={"url": "/outputs/images/a.png"}
    )
    assert r2.status_code == 200 and r2.json()["reversed"].startswith("一位少女")


def _h(client, user):  # noqa: ANN001
    from backend.app.services.auth_service import create_access_token

    return {"Authorization": f"Bearer {create_access_token(user)}"}


def test_vision_alive_rejects_foreign_personal_channel(db_session, user, tmp_path):
    """个人渠道配置的视觉默认对他人无效（回落系统级）——walker 实证防回归。"""


    from backend.app.models import ProviderChannel, ProviderModel
    from backend.app.routers.settings import _vision_alive

    owner_ch = ProviderChannel(
        user_id=999, domain="llm", name="私人VL", base_url="https://x/v1", key_encrypted="x"
    )
    db_session.add(owner_ch)
    db_session.commit()
    db_session.add(
        ProviderModel(channel_id=owner_ch.id, model_id="priv-vl", meta_json='{"vision": true}')
    )
    db_session.commit()
    db_session.expire_all()
    cfg = {"channel_id": owner_ch.id, "model": "priv-vl"}

    assert _vision_alive(db_session, cfg, user_id=999) is True  # 本人可用
    assert _vision_alive(db_session, cfg, user_id=user.id) is False  # 他人不可见 → 回落
