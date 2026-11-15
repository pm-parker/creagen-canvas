"""Agent Run v2 · M0 地基+止血：事件流 / engine 分派 / L2 记账 / 终态通知 / SSE 补发。

对应技术设计 §3 §4.2(L2) §8 §9 · TDD：先红后绿。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from backend.app.models import AgentRun, CanvasNode, Task
from backend.app.models.run_event import RunEvent
from backend.app.services import agent_events, agent_notify, agent_orchestrator
from backend.app.services import agent_run_store as store


@pytest.fixture
def user(db_session):  # noqa: ANN001
    from backend.app.models import User

    tag = id(db_session) % 10000
    u = User(username=f"m0_{tag}", email=f"m0_{tag}@t.local", password_hash="x")
    db_session.add(u)
    db_session.commit()
    return u


@pytest.fixture
def proj_run(db_session, user):  # noqa: ANN001 — (project, run) 工厂
    from backend.app.models import CanvasProject

    def _make(engine: str = "v1") -> tuple[CanvasProject, AgentRun]:
        proj = CanvasProject(title="t", user_id=user.id)
        db_session.add(proj)
        db_session.commit()
        run = store.create_run(
            db_session,
            user_id=int(user.id),
            project_id=int(proj.id),
            initial_message="m",
            engine=engine,
        )
        return proj, run

    return _make


def _mk_done_task(db_session, user, run_id: int) -> Task:  # noqa: ANN001
    t = Task(
        user_id=user.id,
        type="image",
        prompt="p",
        model="m",
        status="completed",
        output_url="/outputs/x.png",
        params=json.dumps({"agent_run_id": run_id}),
    )
    db_session.add(t)
    db_session.commit()
    return t


# ---------- 1) 事件流与投影一致性（设计 §3.1） ----------


def test_emit_appends_and_projects(db_session, user, proj_run):
    _, run = proj_run()
    ev1 = agent_events.emit(db_session, run.id, "run_created", {"message": "m"})
    ev2 = agent_events.emit(db_session, run.id, "assistant_turn", {"thought": "思考"})
    assert ev1.id < ev2.id, "事件 id 单调递增（SSE 补发游标前提）"

    fresh = store.get_run(db_session, run.id)
    assert fresh is not None
    assert fresh.last_event_id == ev2.id, "emit 同事务更新投影游标"

    tail = agent_events.list_events_since(db_session, int(run.id), since=int(ev1.id))
    assert [e.type for e in tail] == ["assistant_turn"]
    assert json.loads(str(tail[0].payload_json))["thought"] == "思考"

    assert agent_events.rebuild_projection(db_session, run.id) == ev2.id, "重建=投影"


def test_emit_payload_serialized_roundtrip(db_session, user, proj_run):
    _, run = proj_run()
    ev = agent_events.emit(db_session, run.id, "task_observed", {"items": [{"task_id": 7}]})
    got = db_session.query(RunEvent).filter(RunEvent.id == ev.id).first()
    assert json.loads(got.payload_json) == {"items": [{"task_id": 7}]}


# ---------- 2) engine 分派与迁移（设计 §3.3/3.4：flag 只定默认，run 定格） ----------


def test_engine_default_v1_and_explicit_v2(db_session, user, proj_run):
    _, run_v1 = proj_run()
    assert run_v1.engine == "v1", "缺省 engine=v1，存量行为不变"
    _, run_v2 = proj_run(engine="v2")
    assert run_v2.engine == "v2", "创建时定格，此后 flag 翻转不影响本 run"


def test_incomplete_is_valid_terminal(db_session, user, proj_run):
    _, run = proj_run()
    store.finish_run(db_session, run.id, "incomplete", error="HARD_CAP")
    inc = store.get_run(db_session, run.id)
    assert inc is not None and inc.status == "incomplete"


# ---------- 3) 终态通知：文字卡 + 用户 toast（设计 §9.2，止血核心） ----------


def test_finish_run_creates_text_card_and_user_toast(db_session, user, proj_run, monkeypatch):
    captured: list[tuple[int, dict[str, Any]]] = []
    monkeypatch.setattr(
        agent_notify, "_publish_user_event", lambda uid, payload: captured.append((uid, payload))
    )
    proj, run = proj_run()
    store.finish_run(db_session, run.id, "stopped", error="MAX_STEPS")

    cards = (
        db_session.query(CanvasNode)
        .filter(CanvasNode.project_id == proj.id, CanvasNode.type == "text")
        .all()
    )
    assert cards, "终态必须在画布落文字卡（画布即通知面）"
    blob = cards[0].title + (cards[0].metadata_json or "")
    assert "停止" in blob and "步数上限" in blob, "卡片文案必须是人话而非裸错误码"

    assert captured, "终态必须发用户级 toast 事件"
    uid, payload = captured[0]
    assert uid == user.id
    assert payload["data"]["kind"] in ("stopped", "completed", "failed", "incomplete")


def test_notify_never_breaks_terminal_write(db_session, user, proj_run, monkeypatch):
    def boom(*a: Any, **k: Any) -> None:
        raise RuntimeError("通知渠道炸了")

    monkeypatch.setattr(agent_notify, "_publish_user_event", boom)
    monkeypatch.setattr(agent_notify, "_create_text_card", boom)
    _, run = proj_run()
    store.finish_run(db_session, run.id, "failed", error="X")  # 不得抛
    fin = store.get_run(db_session, run.id)
    assert fin is not None and fin.status == "failed"


# ---------- 4) L2 记账：观察轮不计步（设计 §4.2/§6） ----------


def test_kick_v2_observation_round_zero_action_steps(db_session, user, proj_run, monkeypatch):
    from backend.app.services import agent_brain

    _, run = proj_run(engine="v2")
    task = _mk_done_task(db_session, user, run.id)
    store.set_pending(db_session, run.id, [{"type": "task", "id": task.id}])

    async def fake_call(db, *, user_id, messages, tool_manifest=None):  # noqa: ANN001
        return {
            "done": True,
            "summary": "完工",
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }

    monkeypatch.setattr(agent_brain, "call_brain", fake_call)
    import asyncio

    asyncio.run(agent_orchestrator.kick_run(db_session, run.id))

    fresh = store.get_run(db_session, run.id)
    assert fresh is not None
    assert fresh.status == "completed"
    assert fresh.action_steps == 0, "纯观察+done 轮零动作 → 不计步（L2）"
    assert fresh.brain_calls == 1
    assert fresh.tokens_used == 2, "usage 记账保留"

    types = [
        e.type
        for e in db_session.query(RunEvent).filter(RunEvent.run_id == run.id).order_by(RunEvent.id)
    ]
    assert "task_observed" in types and "run_terminal" in types, "v2 全程落事件"
    terminal = [
        e
        for e in db_session.query(RunEvent).filter(RunEvent.run_id == int(run.id))
        if e.type == "run_terminal"
    ]
    assert json.loads(terminal[0].payload_json)["kind"] == "completed"


def test_kick_v2_action_round_counts_once(db_session, user, proj_run, monkeypatch):
    """L1 语义下的 L2 记账：计划轮=动作轮计步；done 轮不计。"""
    from backend.app.services import agent_brain

    _, run = proj_run(engine="v2")
    task = _mk_done_task(db_session, user, run.id)

    calls = {"n": 0}

    async def fake_call(db, *, user_id, messages, tool_manifest=None):  # noqa: ANN001
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "thought": "先交计划",
                "actions": [
                    {
                        "tool": "submit_plan",
                        "args": {
                            "deliverables": [{"kind": "image", "label": "成图"}],
                            "est_steps": 2,
                        },
                    }
                ],
            }
        return {
            "done": True,
            "summary": "完工",
            "done_matches": [{"label": "成图", "task_ids": [int(task.id)]}],
        }

    import asyncio

    import backend.app.services.agent_tools as _tools

    async def _fake_exec_ok():  # noqa: ANN202
        return {"ok": True}

    async def _dispatch(ctx, action):  # noqa: ANN001
        if action.get("tool") in ("submit_plan", "plan_append"):
            return await _tools.execute_action(ctx, action)  # 计划工具走真实现
        return {"ok": True}

    monkeypatch.setattr(agent_brain, "call_brain", fake_call)
    monkeypatch.setattr(agent_orchestrator, "execute_action", _dispatch)

    asyncio.run(agent_orchestrator.kick_run(db_session, run.id))
    asyncio.run(agent_orchestrator.kick_run(db_session, run.id))

    fresh = store.get_run(db_session, run.id)
    assert fresh is not None
    assert fresh.action_steps == 1, "只有计划轮产出动作 → 计 1 步（done 轮不计）"
    assert fresh.brain_calls == 2
    assert fresh.status == "completed"


def test_kick_v1_unchanged_step_semantics(db_session, user, proj_run, monkeypatch):
    """v1 run 语义完全不变（engine 分派不回归）。"""
    from backend.app.services import agent_brain

    _, run = proj_run(engine="v1")
    task = _mk_done_task(db_session, user, run.id)
    store.set_pending(db_session, run.id, [{"type": "task", "id": task.id}])

    async def fake_call(db, *, user_id, messages, tool_manifest=None):  # noqa: ANN001
        return {"done": True, "summary": "ok"}

    monkeypatch.setattr(agent_brain, "call_brain", fake_call)
    import asyncio

    asyncio.run(agent_orchestrator.kick_run(db_session, run.id))

    fresh = store.get_run(db_session, run.id)
    assert fresh is not None
    assert fresh.status == "completed"
    events = db_session.query(RunEvent).filter(RunEvent.run_id == run.id).count()
    assert events == 0, "v1 run 不写事件流（engine 分派不回归）"


# ---------- 5) SSE 补发（设计 §9.1，服务端先行、旧客户端兼容） ----------


def test_sse_replay_since_for_terminal_v2_run(client, db_session, fake_arq_pool):
    h, u = _register_and_flag_v2(client, db_session)
    r = client.post("/api/agent-runs", json={"message": "回放测试"}, headers=h)
    assert r.status_code == 201
    run_id = r.json()["run_id"]

    ev1 = agent_events.emit(db_session, run_id, "task_observed", {"items": []})
    ev2 = agent_events.emit(db_session, run_id, "assistant_turn", {"thought": "x"})
    store.finish_run(db_session, run_id, "stopped", error="MAX_STEPS")

    resp = client.get(f"/api/agent-runs/{run_id}/events?since={ev1.id}", headers=h)
    assert resp.status_code == 200
    body = resp.text
    assert '"assistant_turn"' in body, "since 之后的事件必须补发"
    assert f"id: {ev2.id}" in body, "SSE 行携带事件 id（Last-Event-ID 契约）"


# ---------- helpers ----------


def _register_and_flag_v2(client, db_session):  # noqa: ANN001
    import uuid

    from backend.app.models import CapabilityConfig, User

    name = f"m0_{uuid.uuid4().hex[:8]}"
    client.post(
        "/api/auth/register",
        json={
            "username": name,
            "email": f"{name}@x.io",
            "password": "password123",
            "invite_code": "letmein",
        },
    )
    r = client.post("/api/auth/login", json={"identifier": name, "password": "password123"})
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    u = db_session.query(User).filter(User.username == name).first()
    db_session.add(CapabilityConfig(key="run_events_v2", value_json="true"))
    db_session.commit()
    return h, u


def test_notify_card_has_client_id_invariant(db_session, user, proj_run):
    """run#17 实证：服务端节点缺 client_id → 客户端保存产生幽灵复制。"""
    proj, run = proj_run()
    store.finish_run(db_session, run.id, "completed")
    from backend.app.models import CanvasNode

    cards = (
        db_session.query(CanvasNode)
        .filter(CanvasNode.project_id == proj.id, CanvasNode.type == "text")
        .all()
    )
    assert cards
    for c in cards:
        meta = json.loads(c.metadata_json or "{}")
        assert meta.get("client_id", "").startswith("notice-"), (
            "必须带 client_id（canvas.ts:175 不变量）"
        )
