"""agent_brain 单测：JSON 动作协议解析容错 / 脑调用 / 预算。"""

from __future__ import annotations

import pytest

from backend.app.models import User
from backend.app.providers.fake import FakeProvider as _RealFake
from backend.app.providers.openai_compat import OpenAICompatProvider
from backend.app.services.agent_brain import (
    BrainChannelError,
    BrainProtocolError,
    call_brain,
    parse_action,
    strip_think,
)

# ---------- parse_action：容错矩阵 ----------

def test_parse_plain_json():
    raw = (
        '{"thought": "出图", "actions": '
        '[{"tool": "generate_image", "args": {"message": "a cat"}}]}'
    )
    r = parse_action(raw)
    assert r["thought"] == "出图"
    assert r["actions"][0]["tool"] == "generate_image"


def test_parse_done_variant():
    r = parse_action('{"thought": "完成", "done": true, "summary": "三图一视频已就绪"}')
    assert r.get("done") is True and r["summary"] == "三图一视频已就绪"


def test_parse_strips_think():
    raw = "<think>用户要猫，我计划分三步……</think>\n{\"thought\": \"s\", \"actions\": []}"
    r = parse_action(raw)
    assert r["thought"] == "s"


def test_parse_json_fence():
    raw = "好的，我的计划如下：\n```json\n{\"thought\": \"f\", \"actions\": []}\n```\n请执行"
    r = parse_action(raw)
    assert r["thought"] == "f"


def test_parse_prose_braces_heuristic():
    raw = '分析完毕。{"thought": "p", "done": true, "summary": "ok"} 以上。'
    r = parse_action(raw)
    assert r.get("done") is True


def test_parse_invalid_raises():
    with pytest.raises(BrainProtocolError):
        parse_action("我不会输出 JSON，抱歉")
    with pytest.raises(BrainProtocolError):
        parse_action("[1,2,3]")  # 数组不是动作对象


def test_parse_actions_must_be_list():
    with pytest.raises(BrainProtocolError):
        parse_action('{"thought": "x", "actions": "generate_image"}')


def test_strip_think_variants():
    assert strip_think("<think>a</think>正文") == "正文"
    assert strip_think("无思考") == "无思考"


# ---------- call_brain ----------


class _FakeProvider(_RealFake):
    """测试替身：继承真 FakeProvider 使 isinstance 判定成立。"""

    def __init__(self):  # noqa: ANN204
        super().__init__("fake-llm")
        self.base_url = "https://llm.example"
        self.key_plain = "sk-x"
        self.task_type = "llm"


def _mk_user(db_session):  # noqa: ANN001
    u = User(username="alice", email="alice@t.local", password_hash="x")
    db_session.add(u)
    db_session.commit()
    return u


@pytest.fixture
def fake_resolve(monkeypatch, db_session):
    """脑渠道解析 → FakeProvider（非 OpenAICompat 路径：走内置合成响应）。"""
    from backend.app.services import agent_brain

    monkeypatch.setattr(
        agent_brain, "_resolve_brain_provider", lambda db, user_id: (_FakeProvider(), None)
    )


def test_call_brain_no_channel(db_session, monkeypatch):
    from backend.app.core.errors import ProviderNotConfiguredError
    from backend.app.services import provider_service

    def boom(db, user_id, domain, model, channel_id=None):  # noqa: ANN001
        raise ProviderNotConfiguredError("未配置 LLM 渠道")  # noqa: EM101

    monkeypatch.setattr(provider_service, "resolve_provider_for_task", boom)
    u = _mk_user(db_session)
    with pytest.raises(BrainChannelError) as ei:
        import asyncio

        asyncio.run(
            call_brain(db_session, user_id=u.id, messages=[{"role": "user", "content": "hi"}])
        )
    assert ei.value.code == "LLM_CHANNEL_REQUIRED"


def test_call_brain_fake_provider_done(db_session, fake_resolve):
    import asyncio

    u = _mk_user(db_session)
    r = asyncio.run(
        call_brain(db_session, user_id=u.id, messages=[{"role": "user", "content": "画三张猫"}])
    )
    assert r.get("done") is True  # Fake 路径：合成完成响应，流程可走通


def test_call_brain_http_success(db_session, fake_resolve, monkeypatch):
    """OpenAICompat 形态：mock httpx 返回合法动作 + usage 记账。"""
    import asyncio

    from backend.app.services import agent_brain

    u = _mk_user(db_session)

    class _P(OpenAICompatProvider):
        """OpenAICompat 形态替身（httpx 全程 mock，无真实网络）。"""

        def __init__(self):  # noqa: ANN204
            self.model = "test-llm"
            self.base_url = "https://llm.example"
            self.key_plain = "sk-x"
            self.task_type = "llm"

    class _Resp:
        status_code = 200

        def json(self):  # noqa: ANN201
            return {
                "choices": [{"message": {"content": '{"thought": "t", "actions": []}'}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            }

    captured: dict = {}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):  # noqa: ANN002
            return False

        async def post(self, url, **kw):  # noqa: ANN001
            captured.update(kw)
            return _Resp()

    monkeypatch.setattr(agent_brain.httpx, "AsyncClient", lambda **kw: _Client())
    monkeypatch.setattr(
        agent_brain, "_resolve_brain_provider", lambda db, user_id: (_P(), None)
    )

    r = asyncio.run(
        call_brain(
            db_session,
            user_id=u.id,
            messages=[{"role": "user", "content": "hi"}],
            tool_manifest=[{"name": "generate_image", "description": "生成图片", "params": {}}],
        )
    )
    assert r["thought"] == "t"
    # system 提示词注入了工具清单
    sent = captured["json"]["messages"]
    assert sent[0]["role"] == "system" and "generate_image" in sent[0]["content"]
    # usage 记入 agent run 无关，但预算记账发生（llm:<uid> 行存在）
    from backend.app.services.llm_budget import budget_status

    assert budget_status(db_session, f"llm:{u.id}")["used"] == 15


def test_call_brain_budget_exhausted(db_session, fake_resolve, monkeypatch):
    import asyncio

    from backend.app.services.llm_budget import LLMBudget

    u = _mk_user(db_session)
    from datetime import date

    db_session.add(
        LLMBudget(
            scope=f"llm:{u.id}", day=date.today(), tokens_used=9_999_999, tokens_limit=9_999_999
        )
    )
    db_session.commit()


    with pytest.raises(BrainChannelError) as ei:
        asyncio.run(
            call_brain(db_session, user_id=u.id, messages=[{"role": "user", "content": "hi"}])
        )
    assert ei.value.code == "BUDGET_EXCEEDED"
