"""Agent Run v2 · L3 自动接续：预算护栏谓词化 / resume API / worker boot 扫描。

对应技术设计 §4.3（护栏）与 §8（恢复与续跑，含 §14 审查修复）。
TDD：先红后绿。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import pytest

from backend.app.models import CanvasProject, Task, User
from backend.app.models.run_event import RunEvent
from backend.app.services import agent_brain, agent_orchestrator, agent_tools
from backend.app.services import agent_run_store as store


@pytest.fixture
def user(db_session):  # noqa: ANN001
    u = User(username="l3user", email="l3@t.local", password_hash="x")
    db_session.add(u)
    db_session.commit()
    return u


@pytest.fixture
def v2_run(db_session, user):  # noqa: ANN001
    proj = CanvasProject(title="l3", user_id=user.id)
    db_session.add(proj)
    db_session.commit()
    run = store.create_run(
        db_session, user_id=int(user.id), project_id=int(proj.id), initial_message="m", engine="v2"
    )
    return run


def _ctx(db_session, user, run):  # noqa: ANN001
    from backend.app.services.agent_tools import ToolContext

    return ToolContext(
        db=db_session,
        user=user,
        run_id=int(run.id),
        project_id=int(run.project_id),
        reference_whitelist=[],
        pinned_model=None,
    )


def _mk_task(db_session, user, run_id: int) -> Task:  # noqa: ANN001
    t = Task(
        user_id=user.id,
        type="video",
        prompt="p",
        model="m",
        status="completed",
        output_url="/outputs/v.mp4",
        params=json.dumps({"agent_run_id": run_id}),
    )
    db_session.add(t)
    db_session.commit()
    return t


def _brain_with_plan_then(done_after: bool = True):  # noqa: ANN202
    state = {"n": 0}

    async def fake(db, *, user_id, messages, tool_manifest=None):  # noqa: ANN001
        state["n"] += 1
        if state["n"] == 1:
            return {
                "thought": "计划",
                "actions": [
                    {
                        "tool": "submit_plan",
                        "args": {
                            "deliverables": [{"kind": "video", "label": "镜头1"}],
                            "est_steps": 2,
                        },
                    }
                ],
            }
        return {"thought": "t", "done": True, "summary": "s"}

    return fake


async def _real_plan_dispatch(ctx, action):  # noqa: ANN001
    if action.get("tool") in ("submit_plan", "plan_append"):
        return await agent_tools.execute_action(ctx, action)
    return {"ok": True}


# ---------- 1) soft cap：自动扩额并继续（设计 §4.3） ----------


def test_soft_cap_auto_extends_and_continues(db_session, user, v2_run, monkeypatch):
    monkeypatch.setattr(agent_brain, "call_brain", _brain_with_plan_then())
    monkeypatch.setattr(agent_orchestrator, "execute_action", _real_plan_dispatch)
    asyncio.run(agent_orchestrator.kick_run(db_session, v2_run.id))  # 提交计划 → max_steps=4

    # 模拟预算耗尽：action_steps 顶到 max_steps，观察护栏行为
    run = store.get_run(db_session, v2_run.id)
    assert run is not None
    run.action_steps = run.max_steps
    db_session.commit()

    async def brain_more(db, *, user_id, messages, tool_manifest=None):  # noqa: ANN001
        return {
            "thought": "继续干活",
            "actions": [{"tool": "create_text_node", "args": {"title": "x"}}],
        }

    monkeypatch.setattr(agent_brain, "call_brain", brain_more)
    result = asyncio.run(agent_orchestrator.kick_run(db_session, v2_run.id))

    fresh = store.get_run(db_session, v2_run.id)
    assert fresh is not None
    assert result.get("status") != "stopped", "触 soft cap 不得处决——自动扩额后继续"
    assert fresh.max_steps > 4, "max_steps 已扩（+max(8, 剩余×2)）"
    grants = [
        e
        for e in db_session.query(RunEvent).filter(RunEvent.run_id == int(v2_run.id))
        if e.type == "budget_granted"
    ]
    assert any(json.loads(g.payload_json).get("reason") == "auto" for g in grants), "扩额事件留痕"


# ---------- 2) HARD_CAP / 扩额上限 → incomplete（可续） ----------


def test_hard_cap_goes_incomplete(db_session, user, v2_run, monkeypatch, fake_arq_pool):
    run = store.get_run(db_session, v2_run.id)
    assert run is not None
    db_session.query(type(run)).filter(type(run).id == int(run.id)).update(
        {"max_steps": 60, "action_steps": 60}
    )
    db_session.commit()
    db_session.expire_all()

    async def brain_any(db, *, user_id, messages, tool_manifest=None):  # noqa: ANN001
        return {"thought": "x", "actions": [{"tool": "create_text_node", "args": {"title": "x"}}]}

    monkeypatch.setattr(agent_brain, "call_brain", brain_any)
    monkeypatch.setattr(agent_orchestrator, "execute_action", _real_plan_dispatch)
    asyncio.run(agent_orchestrator.kick_run(db_session, v2_run.id))

    fresh = store.get_run(db_session, v2_run.id)
    assert fresh is not None
    assert fresh.status == "incomplete", "HARD_CAP → 显式未完成而非 stopped"
    assert fresh.error == "HARD_CAP"
    terminals = [
        e
        for e in db_session.query(RunEvent).filter(RunEvent.run_id == int(v2_run.id))
        if e.type == "run_terminal"
    ]
    assert terminals and json.loads(terminals[0].payload_json)["kind"] == "incomplete"


def test_extension_limit_goes_incomplete(db_session, user, v2_run, monkeypatch, fake_arq_pool):
    for _ in range(3):
        store.bump_ext(db_session, v2_run.id, "auto_extend_count")
    run = store.get_run(db_session, v2_run.id)
    assert run is not None
    run.action_steps = run.max_steps  # 触顶但 max_steps < HARD_CAP
    db_session.commit()

    async def brain_any(db, *, user_id, messages, tool_manifest=None):  # noqa: ANN001
        return {"thought": "x", "actions": [{"tool": "create_text_node", "args": {"title": "x"}}]}

    monkeypatch.setattr(agent_brain, "call_brain", brain_any)
    asyncio.run(agent_orchestrator.kick_run(db_session, v2_run.id))
    fresh = store.get_run(db_session, v2_run.id)
    assert fresh is not None
    assert fresh.status == "incomplete" and fresh.error == "EXTENSION_LIMIT"


# ---------- 3) resume API（设计 §8 + §14：单活跃护栏） ----------


def _register(client, db_session):  # noqa: ANN001
    name = f"l3_{uuid.uuid4().hex[:8]}"
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
    from backend.app.models import User

    u = db_session.query(User).filter(User.username == name).first()
    return {"Authorization": f"Bearer {r.json()['access_token']}"}, u


def _make_incomplete_run(db_session, user) -> Any:  # noqa: ANN001
    """两镜计划：镜头1 已交付（映射+完成任务），镜头2 未交付 → incomplete。"""
    proj = CanvasProject(title="r", user_id=user.id)
    db_session.add(proj)
    db_session.commit()
    run = store.create_run(
        db_session,
        user_id=int(user.id),
        project_id=int(proj.id),
        initial_message="续跑测试",
        engine="v2",
    )
    ctx = _ctx(db_session, user, run)
    asyncio.run(
        agent_tools.execute_action(
            ctx,
            {
                "tool": "submit_plan",
                "args": {
                    "deliverables": [
                        {"kind": "video", "label": "镜头1"},
                        {"kind": "video", "label": "镜头2"},
                    ],
                    "est_steps": 4,
                },
            },
        )
    )
    t = _mk_task(db_session, user, int(run.id))
    store.map_deliverable_task(db_session, int(run.id), "镜头1", int(t.id))
    store.finish_run(db_session, int(run.id), "incomplete", error="HARD_CAP")
    return run, t


def test_resume_creates_child_run_with_remaining(client, db_session, fake_arq_pool, monkeypatch):
    h, u = _register(client, db_session)
    run, _t = _make_incomplete_run(db_session, u)

    r = client.post(f"/api/agent-runs/{int(run.id)}/resume", headers=h)
    assert r.status_code == 201, r.text
    body = r.json()
    child_id = body["run_id"]
    assert child_id != run.id

    child = store.get_run(db_session, child_id)
    assert child is not None
    assert child.parent_run_id == run.id, "谱系可追溯"
    assert child.engine == "v2"
    plan = child.plan()
    assert plan is not None
    labels = [d["label"] for d in plan["deliverables"]]
    assert labels == ["镜头2"], "只继承未完成交付物"
    assert child.status in ("queued", "waiting", "planning")
    # 上下文/白名单/项目继承
    assert child.project_id == run.project_id
    assert store.get_messages(db_session, child_id), "消息历史继承（脑上下文）"
    grants = [
        e for e in db_session.query(RunEvent).filter(RunEvent.run_id == child_id)
        if e.type == "budget_granted"
    ]
    assert grants and json.loads(grants[0].payload_json).get("reason") == "resume"
    created = [
        e for e in db_session.query(RunEvent).filter(RunEvent.run_id == child_id)
        if e.type == "run_created"
    ]
    assert created and json.loads(created[0].payload_json).get("trigger") == "resume"


def test_resume_rejects_completed_and_conflicts_active(client, db_session, fake_arq_pool):
    h, u = _register(client, db_session)
    run, _t = _make_incomplete_run(db_session, u)

    # completed 不可续
    store.finish_run(db_session, run.id, "completed")
    r = client.post(f"/api/agent-runs/{run.id}/resume", headers=h)
    assert r.status_code == 409

    # 回到 incomplete，但已有活跃 run → ACTIVE_RUN_EXISTS
    store.set_status(db_session, run.id, "incomplete")
    proj2 = CanvasProject(title="x", user_id=u.id)
    db_session.add(proj2)
    db_session.commit()
    store.create_run(
        db_session,
        user_id=int(u.id),
        project_id=int(proj2.id),
        initial_message="活跃中",
        engine="v2",
    )
    r = client.post(f"/api/agent-runs/{run.id}/resume", headers=h)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "ACTIVE_RUN_EXISTS"


# ---------- 4) worker boot 扫描（设计 §8/B4：复用 sweeper 不二写） ----------


def test_worker_boot_calls_sweep(monkeypatch):
    from backend import worker

    called = {"n": 0}

    def fake_sweep(db):  # noqa: ANN001
        called["n"] += 1
        return []

    monkeypatch.setattr(worker, "sweep_stale_runs", fake_sweep)
    import asyncio

    asyncio.run(worker.worker_startup({}))
    assert called["n"] == 1, "boot 时调用一次 sweeper"


def test_resume_nothing_left_rectifies_parent(client, db_session, fake_arq_pool):
    """投影已全完成时 resume：父 run 正名 completed，返回 NOTHING_TO_RESUME。"""
    h, u = _register(client, db_session)
    run, _t = _make_incomplete_run(db_session, u)
    # 补齐第二个交付物 → 投影全满足
    t2 = _mk_task(db_session, u, int(run.id))
    store.map_deliverable_task(db_session, int(run.id), "镜头2", int(t2.id))

    r = client.post(f"/api/agent-runs/{int(run.id)}/resume", headers=h)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "NOTHING_TO_RESUME"
    db_session.expire_all()  # API 会话已提交，本会话身份映射需失效（仓库既有模式）
    fresh = store.get_run(db_session, run.id)
    assert fresh is not None and fresh.status == "completed"


def test_resume_transfers_lineage_evidence(client, db_session, fake_arq_pool):
    """run#14 实证：祖先有产物但 map 无条目（修复前生成的 run）→ resume 时证据转移落子 map。"""
    import asyncio

    from backend.app.models.run_event import RunEvent

    h, u = _register(client, db_session)
    run, t = _make_incomplete_run(db_session, u)  # 镜头1 已映射完成；镜头2 缺
    # 模拟父代 compose：kind=compose 交付物 + tool_result 事件（map 无条目——旧代码产物）
    from backend.app.services import agent_tools
    from backend.app.services.agent_tools import ToolContext

    ctx = ToolContext(
        db=db_session, user=u, run_id=int(run.id), project_id=int(run.project_id),
        reference_whitelist=[], pinned_model=None,
    )
    # R4 新合约：已有计划走 plan_append 演化（重复 submit_plan 已被拒）
    asyncio.run(
        agent_tools.execute_action(
            ctx,
            {
                "tool": "plan_append",
                "args": {
                    "deliverables": [{"kind": "compose", "label": "成片"}],
                    "est_delta": 2,
                },
            },
        )
    )
    db_session.add(
        RunEvent(
            run_id=int(run.id),
            type="tool_result",
            payload_json=(
                '{"tool": "compose_videos", "ok": true, "url": "/outputs/media/legacy.mp4"}'
            ),
        )
    )
    db_session.commit()

    r = client.post(f"/api/agent-runs/{int(run.id)}/resume", headers=h)
    assert r.status_code == 201
    child = store.get_run(db_session, r.json()["run_id"])
    assert child is not None
    m = child.deliverables_map()
    evs = m.get("镜头2", [])
    mapped = m.get("成片", [])
    assert any("legacy.mp4" in str(x) for x in mapped), "compose-kind 标签自动挂祖先成片 url"
    assert any(str(x) == f"task:{int(t.id)}" for x in evs) or "镜头2" not in m, "祖先 map 证据继承"
    verdict_map = {k: v for k, v in m.items()}
    assert "镜头1" not in verdict_map, "已完成项不转移"
