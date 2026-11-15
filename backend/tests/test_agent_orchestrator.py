"""agent_orchestrator 单测：提交 / 迭代 / 终态钩子 / 停止 / sweeper / 幂等。

脑与生成渠道全部 mock——验证状态机行为而非模型质量。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.app.models import AgentRun, CanvasProject, Task, User
from backend.app.services import agent_orchestrator as orch
from backend.app.services import agent_run_store as store


@pytest.fixture
def user(db_session):  # noqa: ANN001
    u = User(username="alice", email="alice@t.local", password_hash="x")
    db_session.add(u)
    db_session.commit()
    return u


@pytest.fixture
def brain_done(monkeypatch):
    """脑合成：直接宣布完成。"""
    from backend.app.services import agent_brain

    async def fake_call(db, *, user_id, messages, tool_manifest=None):  # noqa: ANN001
        return {"thought": "一步到位", "done": True, "summary": "完成", "usage": None}

    monkeypatch.setattr(agent_brain, "call_brain", fake_call)


def _mk_task(  # noqa: ANN001
    db_session,
    user,
    *,
    status="completed",
    output_url="/outputs/images/a.png",
    run_id=None,
):
    params = {"agent_run_id": run_id} if run_id else {}
    t = Task(
        user_id=user.id,
        type="image",
        prompt="p",
        model="fake-image",
        status=status,
        output_url=output_url if status == "completed" else None,
        params=json.dumps(params),
    )
    db_session.add(t)
    db_session.commit()
    return t


# ---------- submit ----------

def test_submit_creates_project_and_run(db_session, user, brain_done, fake_arq_pool):
    mentions = []
    run = asyncio.run(
        orch.submit_run(db_session, user=user, message="画三张赛博朋克猫", mentions=mentions)
    )
    assert run.status == "queued"
    proj = db_session.get(CanvasProject, run.project_id)
    assert proj is not None and "赛博朋克猫" in proj.title
    # kick 已入队
    assert any(j[0] == "run_agent_step" for j in fake_arq_pool)


def test_submit_single_active_run(db_session, user, brain_done):
    asyncio.run(orch.submit_run(db_session, user=user, message="第一个任务", mentions=[]))
    with pytest.raises(orch.AgentRunError) as ei:
        asyncio.run(orch.submit_run(db_session, user=user, message="第二个任务", mentions=[]))
    assert ei.value.code == "ACTIVE_RUN_EXISTS"


def test_submit_mentions_resolve(db_session, user, brain_done):
    """素材 mention → 白名单 + 上下文块；模型 mention → pin。"""
    asset = _mk_task(db_session, user)
    from backend.app.models import CharacterAsset

    ch = CharacterAsset(
        user_id=user.id, name="小雨", description="blue hair, red jacket, 18yo girl"
    )
    db_session.add(ch)
    db_session.commit()

    run = asyncio.run(
        orch.submit_run(
            db_session,
            user=user,
            message="给小雨出三张设定图",
            mentions=[
                {"type": "asset", "task_id": asset.id},
                {"type": "character", "id": ch.id},
                {"type": "model", "channel_id": 3, "model": "z-image-turbo"},
            ],
        )
    )
    msgs = store.get_messages(db_session, run.id)
    ctx = msgs[0]["content"]
    assert "[@角色:小雨]" in ctx and "blue hair" in ctx  # 同构标签 + 外观展开
    assert "[@素材:" in ctx and asset.output_url in ctx  # 素材带标签 + URL 注入
    assert asset.output_url in store.get_reference_whitelist(db_session, run.id)
    assert json.loads(run.pinned_model_json)["model"] == "z-image-turbo"


# ---------- kick：迭代主流程 ----------

def test_kick_done_finishes_run(db_session, user, brain_done):
    run = asyncio.run(orch.submit_run(db_session, user=user, message="直接完成", mentions=[]))
    r = asyncio.run(orch.kick_run(db_session, run.id))
    assert r["status"] == "completed"
    steps = store.list_steps(db_session, run.id)
    assert len(steps) == 1 and steps[0].thought == "一步到位"


def test_kick_double_idempotent(db_session, user, brain_done):
    run = asyncio.run(orch.submit_run(db_session, user=user, message="x", mentions=[]))
    a = asyncio.run(orch.kick_run(db_session, run.id))
    b = asyncio.run(orch.kick_run(db_session, run.id))  # 已终态，CAS 失败直接返回
    assert a["status"] == "completed" and b.get("skipped")


def test_kick_dispatch_sets_waiting(db_session, user, monkeypatch, fake_arq_pool):
    """脑派发 generate → pending 落库 → waiting。"""
    from backend.app.services import agent_brain

    async def fake_call(db, *, user_id, messages, tool_manifest=None):  # noqa: ANN001
        return {
            "thought": "先出图",
            "actions": [{"tool": "generate_image", "args": {"message": "猫"}}],
            "usage": None,
        }

    monkeypatch.setattr(agent_brain, "call_brain", fake_call)

    from backend.app.routers import canvas as canvas_router

    async def fake_core(db, u, project_id, body, agent_run_id=None):  # noqa: ANN001
        return {"task_id": 99, "node_client_id": "agent-1", "model": "m", "status": "queued"}

    monkeypatch.setattr(canvas_router, "_agent_generate_core", fake_core)

    run = asyncio.run(orch.submit_run(db_session, user=user, message="画猫", mentions=[]))
    r = asyncio.run(orch.kick_run(db_session, run.id))
    assert r["status"] == "waiting"
    assert [p["id"] for p in store.get_pending(db_session, run.id)] == [99]


def test_kick_pending_not_terminal_returns_to_waiting(db_session, user, monkeypatch, fake_arq_pool):
    """自愈：pending 有未终态任务 → 不调脑直接回 waiting。"""
    from backend.app.services import agent_brain

    called = {"n": 0}

    async def fake_call(db, *, user_id, messages, tool_manifest=None):  # noqa: ANN001
        called["n"] += 1
        return {"thought": "x", "done": True, "summary": "", "usage": None}

    monkeypatch.setattr(agent_brain, "call_brain", fake_call)

    run = asyncio.run(orch.submit_run(db_session, user=user, message="y", mentions=[]))
    t = _mk_task(db_session, user, status="generating", run_id=run.id)
    store.set_status(db_session, run.id, "waiting")
    store.set_pending(db_session, run.id, [{"type": "task", "id": t.id}])

    r = asyncio.run(orch.kick_run(db_session, run.id))
    assert r["status"] == "waiting" and called["n"] == 0


def test_kick_observations_and_whitelist_growth(db_session, user, monkeypatch, fake_arq_pool):
    """任务完成后 wake：观察注入消息 + 产物 URL 入白名单。"""
    from backend.app.services import agent_brain

    captured: dict = {}

    async def fake_call(db, *, user_id, messages, tool_manifest=None):  # noqa: ANN001
        captured["messages"] = messages
        return {"thought": "done", "done": True, "summary": "ok", "usage": None}

    monkeypatch.setattr(agent_brain, "call_brain", fake_call)

    run = asyncio.run(orch.submit_run(db_session, user=user, message="z", mentions=[]))
    t = _mk_task(db_session, user, run_id=run.id)  # completed + output
    store.set_status(db_session, run.id, "waiting")
    store.set_pending(db_session, run.id, [{"type": "task", "id": t.id}])

    asyncio.run(orch.kick_run(db_session, run.id))
    # 观察消息包含产物
    msgs = store.get_messages(db_session, run.id)
    obs = [m for m in msgs if "任务结果" in str(m.get("content"))]
    assert obs and t.output_url in obs[0]["content"]
    assert t.output_url in store.get_reference_whitelist(db_session, run.id)


def test_kick_max_steps_stops(db_session, user, monkeypatch, fake_arq_pool):
    """步数触顶 → stopped(MAX_STEPS)，不再调脑。"""
    from backend.app.services import agent_brain

    async def fake_call(db, *, user_id, messages, tool_manifest=None):  # noqa: ANN001
        return {"thought": "x", "done": True, "summary": "", "usage": None}

    monkeypatch.setattr(agent_brain, "call_brain", fake_call)
    run = asyncio.run(orch.submit_run(db_session, user=user, message="m", mentions=[]))
    db_session.query(AgentRun).filter(AgentRun.id == run.id).update({"step_count": 12})
    db_session.commit()
    r = asyncio.run(orch.kick_run(db_session, run.id))
    assert r["status"] == "stopped" and "MAX_STEPS" in (r.get("reason") or "")


def test_kick_protocol_repair_then_fail(db_session, user, monkeypatch):
    """协议解析失败 → 修复重试一次 → 仍失败 → failed(PROTOCOL_ERROR)。"""
    from backend.app.services import agent_brain

    calls = {"n": 0}

    async def garbage(db, *, user_id, messages, tool_manifest=None):  # noqa: ANN001
        calls["n"] += 1
        raise agent_brain.BrainProtocolError("bad json", "garbage")

    monkeypatch.setattr(agent_brain, "call_brain", garbage)
    run = asyncio.run(orch.submit_run(db_session, user=user, message="p", mentions=[]))
    r = asyncio.run(orch.kick_run(db_session, run.id))
    assert r["status"] == "failed" and "PROTOCOL_ERROR" in (r.get("reason") or "")
    assert calls["n"] == 2  # 一次原始 + 一次修复重试


def test_kick_repair_recovers(db_session, user, monkeypatch):
    from backend.app.services import agent_brain

    calls = {"n": 0}

    async def flaky(db, *, user_id, messages, tool_manifest=None):  # noqa: ANN001
        calls["n"] += 1
        if calls["n"] == 1:
            raise agent_brain.BrainProtocolError("bad", "x")
        return {"thought": "recovered", "done": True, "summary": "ok", "usage": None}

    monkeypatch.setattr(agent_brain, "call_brain", flaky)
    run = asyncio.run(orch.submit_run(db_session, user=user, message="q", mentions=[]))
    r = asyncio.run(orch.kick_run(db_session, run.id))
    assert r["status"] == "completed" and calls["n"] == 2


def test_kick_self_requeues_when_no_pending(db_session, user, monkeypatch, fake_arq_pool):
    """无 pending 且未 done（纯同步工具轮）→ 自动 re-kick 下一轮。"""
    from backend.app.services import agent_brain

    async def plan(db, *, user_id, messages, tool_manifest=None):  # noqa: ANN001
        return {
            "thought": "先写计划卡",
            "actions": [{"tool": "create_text_node", "args": {"title": "计划", "prompt": "三步"}}],
            "usage": None,
        }

    monkeypatch.setattr(agent_brain, "call_brain", plan)
    run = asyncio.run(orch.submit_run(db_session, user=user, message="计划一下", mentions=[]))
    r = asyncio.run(orch.kick_run(db_session, run.id))
    assert r["status"] == "waiting"
    assert any(j[0] == "run_agent_step" for j in fake_arq_pool)


# ---------- 终态钩子 / 停止 / sweeper ----------

def test_advance_on_task_terminal_kicks_run(db_session, user, fake_arq_pool):
    run = asyncio.run(orch.submit_run(db_session, user=user, message="w", mentions=[]))
    t = _mk_task(db_session, user, run_id=run.id)
    asyncio.run(orch.advance_on_task_terminal(db_session, t))
    assert any(j[0] == "run_agent_step" and j[1] == (run.id,) for j in fake_arq_pool)


def test_advance_ignores_unlinked_task(db_session, user, fake_arq_pool):
    t = _mk_task(db_session, user, run_id=None)  # 无 agent_run_id
    asyncio.run(orch.advance_on_task_terminal(db_session, t))
    assert not any(j[0] == "run_agent_step" for j in fake_arq_pool)


def test_stop_run_cancels_pending(db_session, user, monkeypatch):
    run = asyncio.run(orch.submit_run(db_session, user=user, message="s", mentions=[]))
    t = _mk_task(db_session, user, status="generating", run_id=run.id)
    store.set_status(db_session, run.id, "waiting")
    store.set_pending(db_session, run.id, [{"type": "task", "id": t.id}])

    ok = asyncio.run(orch.stop_run(db_session, user=user, run_id=run.id))
    assert ok
    assert db_session.get(AgentRun, run.id).status == "stopped"
    assert db_session.get(Task, t.id).cancel_requested_at is not None


def test_stop_run_ownership(db_session, user):
    other = User(username="bob", email="bob@t.local", password_hash="x")
    db_session.add(other)
    db_session.commit()
    run = asyncio.run(orch.submit_run(db_session, user=user, message="o", mentions=[]))
    with pytest.raises(orch.AgentRunError):
        asyncio.run(orch.stop_run(db_session, user=other, run_id=run.id))


def test_sweep_stale_waiting(db_session, user, brain_done, fake_arq_pool):
    """waiting 且全终态（updated_at 陈旧）→ re-kick。"""
    from datetime import timedelta


    run = asyncio.run(orch.submit_run(db_session, user=user, message="sw", mentions=[]))
    t = _mk_task(db_session, user, run_id=run.id)
    store.set_status(db_session, run.id, "waiting")
    store.set_pending(db_session, run.id, [{"type": "task", "id": t.id}])
    # 与生产同源：DB now 回拨（SQLite 返回串需归一——与实现同款 helper）
    from backend.app.services.agent_orchestrator import _db_now

    db_now = _db_now(db_session)
    db_session.query(AgentRun).filter(AgentRun.id == run.id).update(
        {"updated_at": db_now - timedelta(minutes=20)}
    )
    db_session.commit()

    kicked = orch.sweep_stale_runs(db_session)
    assert run.id in kicked
    assert any(j[0] == "run_agent_step" and j[1] == (run.id,) for j in fake_arq_pool)


def test_sweep_stale_planning_resets(db_session, user, fake_arq_pool):
    """planning 卡死 → 复位 waiting 再 kick。"""
    from datetime import timedelta


    run = asyncio.run(orch.submit_run(db_session, user=user, message="sp", mentions=[]))
    from backend.app.services.agent_orchestrator import _db_now

    db_now = _db_now(db_session)
    db_session.query(AgentRun).filter(AgentRun.id == run.id).update(
        {"status": "planning", "updated_at": db_now - timedelta(minutes=30)}
    )
    db_session.commit()
    kicked = orch.sweep_stale_runs(db_session)
    assert run.id in kicked
    assert db_session.get(AgentRun, run.id).status == "waiting"


def test_stop_run_does_not_hit_prefix_siblings(db_session, user):
    """停止 run 7 不得误伤 run 71/77 的任务（LIKE 前缀子串陷阱）。"""
    import json as _json

    from backend.app.models import Task as T

    t7 = T(
        user_id=user.id, type="image", prompt="a", model="m", status="generating",
        params=_json.dumps({"agent_run_id": 7}),
    )
    t71 = T(
        user_id=user.id, type="image", prompt="b", model="m", status="generating",
        params=_json.dumps({"agent_run_id": 71}),
    )
    db_session.add_all([t7, t71])
    db_session.commit()

    from backend.app.models import CanvasProject

    proj = CanvasProject(user_id=user.id, title="p")
    db_session.add(proj)
    db_session.commit()
    from backend.app.services import agent_run_store as st

    run = st.create_run(db_session, user_id=user.id, project_id=proj.id,
                        initial_message="x", mentions=[])
    db_session.query(AgentRun).filter(AgentRun.id == run.id).update({"id": 7})
    db_session.commit()

    asyncio.run(orch.stop_run(db_session, user=user, run_id=7))
    assert db_session.get(Task, t7.id).cancel_requested_at is not None
    assert db_session.get(Task, t71.id).cancel_requested_at is None  # 71 不被误伤


def test_submit_mentions_shared_prompt_resolves(db_session, user, brain_done):
    """共享提示词（user_id NULL，种子库）@ 引用必须可解析——与面板列表口径一致。"""
    from backend.app.models import Prompt

    shared = Prompt(user_id=None, title="赛博猫", content="cyberpunk cat, neon")
    db_session.add(shared)
    db_session.commit()

    run = asyncio.run(
        orch.submit_run(
            db_session, user=user, message="照这个来",
            mentions=[{"type": "prompt", "id": shared.id}],
        )
    )
    msgs = store.get_messages(db_session, run.id)
    assert "cyberpunk cat, neon" in msgs[0]["content"]


def test_sweeper_rescues_queued_orphan(db_session, user, brain_done, fake_arq_pool):
    """提交时入队失败遗留的 queued 孤儿，sweeper 必须能救（否则永无唤醒）。"""
    from datetime import datetime, timedelta

    run = asyncio.run(orch.submit_run(db_session, user=user, message="孤儿", mentions=[]))
    db_session.query(AgentRun).filter(AgentRun.id == run.id).update(
        {"updated_at": datetime.utcnow() - timedelta(minutes=20)}
    )
    db_session.commit()
    kicked = orch.sweep_stale_runs(db_session)
    assert run.id in kicked


def test_brain_channel_id_recorded(db_session, user, monkeypatch):
    """脑渠道 id 首轮写入 run（抽屉展示"用的哪个脑模型"的 spec 承诺）。"""
    from backend.app.services import agent_brain

    async def fake_call(db, *, user_id, messages, tool_manifest=None):  # noqa: ANN001
        return {
            "thought": "x", "done": True, "summary": "ok",
            "usage": None, "channel_id": 12,
        }

    monkeypatch.setattr(agent_brain, "call_brain", fake_call)
    run = asyncio.run(orch.submit_run(db_session, user=user, message="bc", mentions=[]))
    asyncio.run(orch.kick_run(db_session, run.id))
    assert db_session.get(AgentRun, run.id).brain_channel_id == 12
