"""Agent Run v2 · L1 计划合约：submit_plan / 预算协商 / validate_done / plan_append / 交付物映射。

对应技术设计 §5（含 §14 审查修复：done 拒收有界重试 / deliverable_label 中途投影）。
TDD：先红后绿。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.app.models import CanvasProject, Task, User
from backend.app.models.run_event import RunEvent
from backend.app.services import agent_brain, agent_orchestrator, agent_tools
from backend.app.services import agent_run_store as store

HARD_CAP = 60


@pytest.fixture
def user(db_session):  # noqa: ANN001
    u = User(username="l1user", email="l1@t.local", password_hash="x")
    db_session.add(u)
    db_session.commit()
    return u


@pytest.fixture
def v2_run(db_session, user):  # noqa: ANN001
    proj = CanvasProject(title="l1", user_id=user.id)
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


def _mk_task(db_session, user, run_id: int, label_suffix: str = "1") -> Task:  # noqa: ANN001
    t = Task(
        user_id=user.id,
        type="video",
        prompt=f"p{label_suffix}",
        model="m",
        status="completed",
        output_url=f"/outputs/v{label_suffix}.mp4",
        params=json.dumps({"agent_run_id": run_id}),
    )
    db_session.add(t)
    db_session.commit()
    return t


def _submit_plan(ctx, labels: list[str], est: int = 5) -> dict:  # noqa: ANN001
    return asyncio.run(
        agent_tools.execute_action(
            ctx,
            {
                "tool": "submit_plan",
                "args": {
                    "deliverables": [{"kind": "video", "label": x} for x in labels],
                    "est_steps": est,
                },
            },
        )
    )


# ---------- 1) submit_plan：落库 + 预算协商（设计 §5.1/5.2） ----------


def test_submit_plan_stores_and_negotiates(db_session, user, v2_run):
    ctx = _ctx(db_session, user, v2_run)
    result = _submit_plan(ctx, ["镜头1", "镜头2"], est=5)
    assert result.get("ok") is True

    fresh = store.get_run(db_session, v2_run.id)
    assert fresh is not None
    plan = fresh.plan()
    assert plan is not None and len(plan["deliverables"]) == 2
    assert fresh.max_steps == 7, "est+2 协商"

    types = [
        e.type for e in db_session.query(RunEvent).filter(RunEvent.run_id == int(v2_run.id))
    ]
    assert "plan_submitted" in types and "budget_granted" in types


def test_submit_plan_validates(db_session, user, v2_run):
    ctx = _ctx(db_session, user, v2_run)
    bad = asyncio.run(
        agent_tools.execute_action(
            ctx, {"tool": "submit_plan", "args": {"deliverables": [], "est_steps": 5}}
        )
    )
    assert bad.get("ok") is False, "空交付物清单拒收"


# ---------- 2) plan_append：计划演化（§5.4） ----------


def test_plan_append_extends_budget(db_session, user, v2_run):
    ctx = _ctx(db_session, user, v2_run)
    _submit_plan(ctx, ["镜头1"], est=5)
    result = asyncio.run(
        agent_tools.execute_action(
            ctx,
            {
                "tool": "plan_append",
                "args": {"deliverables": [{"kind": "video", "label": "镜头2"}], "est_delta": 3},
            },
        )
    )
    assert result.get("ok") is True
    fresh = store.get_run(db_session, v2_run.id)
    assert fresh is not None
    assert fresh.max_steps == 10, "5+2 → +3+2 追加协商"
    plan = fresh.plan()
    assert plan is not None and len(plan["deliverables"]) == 2


# ---------- 3) 首轮必须交计划（§5.2 PLAN_MISSING） ----------


def test_first_turn_plan_required_then_failed(db_session, user, v2_run, monkeypatch, fake_arq_pool):
    calls = {"n": 0}

    async def fake_call(db, *, user_id, messages, tool_manifest=None):  # noqa: ANN001
        calls["n"] += 1
        return {
            "thought": "先干活",
            "actions": [{"tool": "create_text_node", "args": {"title": "x"}}],
        }

    executed: list[dict] = []

    async def fake_exec(ctx, action):  # noqa: ANN001
        executed.append(action)
        return {"ok": True}

    monkeypatch.setattr(agent_brain, "call_brain", fake_call)
    monkeypatch.setattr(agent_orchestrator, "execute_action", fake_exec)

    asyncio.run(agent_orchestrator.kick_run(db_session, v2_run.id))
    fresh = store.get_run(db_session, v2_run.id)
    assert fresh is not None
    assert fresh.status == "waiting", "首轮无 plan：不执行动作、注入要求后 re-kick"
    assert not executed, "未交计划前动作不得执行"
    assert any(
        "submit_plan" in (m.get("content") or "") for m in store.get_messages(db_session, v2_run.id)
    ), "注入的计划要求必须入消息"

    asyncio.run(agent_orchestrator.kick_run(db_session, v2_run.id))
    fresh = store.get_run(db_session, v2_run.id)
    assert fresh is not None
    assert fresh.status == "failed" and fresh.error == "PLAN_MISSING", "二次仍无 plan → 显式失败"


# ---------- 4) validate_done：通过 / 拒收 / 有界重试（§5.3 + §14 修复） ----------


def test_validate_done_ok(db_session, user, v2_run, monkeypatch, fake_arq_pool):
    ctx = _ctx(db_session, user, v2_run)
    _submit_plan(ctx, ["镜头1", "镜头2"], est=4)
    t1 = _mk_task(db_session, user, int(v2_run.id), "1")
    t2 = _mk_task(db_session, user, int(v2_run.id), "2")

    async def fake_call(db, *, user_id, messages, tool_manifest=None):  # noqa: ANN001
        return {
            "thought": "交付",
            "done": True,
            "summary": "两镜交付",
            "done_matches": [
                {"label": "镜头1", "task_ids": [int(t1.id)]},
                {"label": "镜头2", "task_ids": [int(t2.id)]},
            ],
        }

    monkeypatch.setattr(agent_brain, "call_brain", fake_call)
    asyncio.run(agent_orchestrator.kick_run(db_session, v2_run.id))

    fresh = store.get_run(db_session, v2_run.id)
    assert fresh is not None and fresh.status == "completed"
    terminals = [
        e
        for e in db_session.query(RunEvent).filter(RunEvent.run_id == int(v2_run.id))
        if e.type == "run_terminal"
    ]
    assert terminals and json.loads(terminals[0].payload_json)["kind"] == "completed"


def test_validate_done_rejects_foreign_and_missing(
    db_session, user, v2_run, monkeypatch, fake_arq_pool
):
    """对抗用例（守 7/71 教训）：他 run 的 task_id 不算数；缺标签拒收；三次后 incomplete。"""
    ctx = _ctx(db_session, user, v2_run)
    _submit_plan(ctx, ["镜头1", "镜头2"], est=4)
    t1 = _mk_task(db_session, user, int(v2_run.id), "1")
    # 他 run 的任务（id 相近也不许串）
    other = store.create_run(
        db_session,
        user_id=int(user.id),
        project_id=int(v2_run.project_id),
        initial_message="m2",
        engine="v2",
    )
    t_foreign = _mk_task(db_session, user, int(other.id), "9")

    attempts = {"n": 0}

    def make_brain():  # noqa: ANN202
        async def fake_call(db, *, user_id, messages, tool_manifest=None):  # noqa: ANN001
            attempts["n"] += 1
            return {
                "thought": "交差",
                "done": True,
                "summary": "x",
                "done_matches": [
                    {"label": "镜头1", "task_ids": [int(t1.id)]},
                    {"label": "镜头2", "task_ids": [int(t_foreign.id)]},  # 外来任务冒充
                ],
            }

        return fake_call

    monkeypatch.setattr(agent_brain, "call_brain", make_brain())
    asyncio.run(agent_orchestrator.kick_run(db_session, v2_run.id))
    fresh = store.get_run(db_session, v2_run.id)
    assert fresh is not None
    assert fresh.status != "completed", "外来任务+缺真实交付 → done 必须被拒"
    assert fresh.done_rejections == 1
    msgs = store.get_messages(db_session, v2_run.id)
    assert any("镜头2" in (m.get("content") or "") for m in msgs), "拒收消息必须点名缺什么"

    asyncio.run(agent_orchestrator.kick_run(db_session, v2_run.id))
    asyncio.run(agent_orchestrator.kick_run(db_session, v2_run.id))
    fresh = store.get_run(db_session, v2_run.id)
    assert fresh is not None
    assert (
        fresh.status == "incomplete" and fresh.error == "DONE_INCOMPLETE"
    ), "三次拒收 → 显式未完成"


# ---------- 5) deliverable_label 中途映射（§5.4，L4 数据源） ----------


def test_generate_with_deliverable_label(db_session, user, v2_run, monkeypatch):
    ctx = _ctx(db_session, user, v2_run)
    _submit_plan(ctx, ["镜头1"], est=3)

    async def fake_core(db, u, project_id, body, agent_run_id=None):  # noqa: ANN001
        t = Task(
            user_id=u.id,
            type="image",
            prompt=body.message,
            model="m",
            status="queued",
            params=json.dumps({"agent_run_id": agent_run_id}),
        )
        db.add(t)
        db.commit()
        return {"task_id": int(t.id), "node_client_id": "x"}

    import backend.app.routers.canvas as canvas_router

    monkeypatch.setattr(canvas_router, "_agent_generate_core", fake_core)
    result = asyncio.run(
        agent_tools.execute_action(
            ctx,
            {
                "tool": "generate_image",
                "args": {"message": "镜头1 首帧", "deliverable_label": "镜头1"},
            },
        )
    )
    assert result.get("ok") is True
    fresh = store.get_run(db_session, v2_run.id)
    assert fresh is not None
    proj_map = fresh.deliverables_map()
    assert proj_map.get("镜头1"), "中途投影：label → task_ids 已建立"


# ---------- 6) 协议层：done_matches 透传 ----------


def test_parse_action_preserves_done_matches():
    raw = (
        '{"thought": "t", "done": true, "summary": "s", '
        '"done_matches": [{"label": "镜头1", "task_ids": [7]}]}'
    )
    out = agent_brain.parse_action(raw)
    assert out.get("done_matches") == [{"label": "镜头1", "task_ids": [7]}]


# ---------- 7) 生产冒烟实证：投影即真相（2026-10-10 run#9 修复） ----------


def test_done_accepted_via_projection_without_matches(
    db_session, user, v2_run, monkeypatch, fake_arq_pool
):
    """脑复述不出 task_id 不应拒收——deliverables_map 投影满足即通过。"""
    ctx = _ctx(db_session, user, v2_run)
    _submit_plan(ctx, ["镜头1"], est=2)
    t1 = _mk_task(db_session, user, int(v2_run.id), "1")
    store.map_deliverable_task(db_session, int(v2_run.id), "镜头1", int(t1.id))

    async def fake_call(db, *, user_id, messages, tool_manifest=None):  # noqa: ANN001
        return {"thought": "交付", "done": True, "summary": "s", "done_matches": None}

    monkeypatch.setattr(agent_brain, "call_brain", fake_call)
    asyncio.run(agent_orchestrator.kick_run(db_session, v2_run.id))
    fresh = store.get_run(db_session, v2_run.id)
    assert fresh is not None and fresh.status == "completed"


# ---------- 8) 同步工具交付（compose 无 task_id）——run#11 回放实证 ----------


def test_compose_url_evidence_satisfies_done(db_session, user, v2_run, monkeypatch, fake_arq_pool):
    """compose 是同步工具（返回 url 无 task_id）——url 证据必须能过终局校验。"""
    ctx = _ctx(db_session, user, v2_run)
    _submit_plan(ctx, ["成片：短片"], est=2)
    from backend.app.models.run_event import RunEvent as RE

    db_session.add(
        RE(
            run_id=int(v2_run.id),
            type="tool_result",
            payload_json=json.dumps(
                {"tool": "compose_videos", "ok": True, "url": "/outputs/media/compose_x.mp4"},
                ensure_ascii=False,
            ),
        )
    )
    db_session.commit()
    from backend.app.services import agent_run_store as st

    st.map_deliverable_evidence(
        db_session, int(v2_run.id), "成片：短片", "url:/outputs/media/compose_x.mp4"
    )

    async def fake_call(db, *, user_id, messages, tool_manifest=None):  # noqa: ANN001
        return {"thought": "交付", "done": True, "summary": "s", "done_matches": None}

    monkeypatch.setattr(agent_brain, "call_brain", fake_call)
    asyncio.run(agent_orchestrator.kick_run(db_session, v2_run.id))
    fresh = store.get_run(db_session, v2_run.id)
    assert fresh is not None and fresh.status == "completed"


def test_compose_tool_auto_maps_kind_compose(db_session, user, v2_run, monkeypatch):
    """compose_videos 未传 label 时自动映射 plan 里 kind=compose 的交付物。"""
    ctx = _ctx(db_session, user, v2_run)
    asyncio.run(
        agent_tools.execute_action(
            ctx,
            {
                "tool": "submit_plan",
                "args": {
                    "deliverables": [
                        {"kind": "video", "label": "镜头1"},
                        {"kind": "compose", "label": "成片：合集"},
                    ],
                    "est_steps": 3,
                },
            },
        )
    )

    import backend.app.services.media_service as ms

    def fake_compose(urls, bgm=None, transition="none"):  # noqa: ANN001, ARG001
        return ms._outputs_root_default() / "media" / "compose_auto.mp4"

    monkeypatch.setattr(ms, "compose_videos", fake_compose)
    result = asyncio.run(
        agent_tools.execute_action(
            ctx,
            {
                "tool": "compose_videos",
                "args": {"urls": ["/outputs/videos/a.mp4", "/outputs/videos/b.mp4"]},
            },
        )
    )
    assert result.get("ok") is True
    fresh = store.get_run(db_session, v2_run.id)
    assert fresh is not None
    m = fresh.deliverables_map()
    mapped = m.get("成片：合集", [])
    assert any("compose_auto.mp4" in str(v) for v in mapped), "kind=compose 自动映射"
    assert "镜头1" not in m or not any("compose" in str(v) for v in m["镜头1"]), "不误映射其他 kind"


# ---------- 9) resume 谱系证据（run#13 实证：子 run 不认父产物 → 假性 incomplete） ----------


def test_child_accepts_lineage_evidence(db_session, user, v2_run, monkeypatch):
    """子 run 的 done 校验接受祖先谱系的 task/url 证据——产物是真的，不强迫重做。"""
    ctx = _ctx(db_session, user, v2_run)
    _submit_plan(ctx, ["镜头1"], est=2)
    t1 = _mk_task(db_session, user, int(v2_run.id), "1")
    store.map_deliverable_task(db_session, int(v2_run.id), "镜头1", int(t1.id))

    child = store.create_run(
        db_session, user_id=int(user.id), project_id=int(v2_run.project_id),
        initial_message="续跑", engine="v2",
    )
    child.parent_run_id = v2_run.id
    db_session.commit()
    from backend.app.services import agent_run_store as st

    st.set_plan(
        db_session,
        int(child.id),
        {"deliverables": [{"kind": "video", "label": "镜头1"}], "est_steps": 1},
    )

    parent_run = store.get_run(db_session, int(v2_run.id))
    child_run = store.get_run(db_session, int(child.id))
    assert parent_run is not None and child_run is not None
    verdict = agent_orchestrator._validate_done(db_session, child_run, None)
    assert verdict["ok"], f"谱系证据应满足：{verdict['missing']}"


def test_submit_plan_coerces_compose_kind():
    """run#15 实证：脑把成片写成 kind=video → 归一为 compose（自动映射/证据转移的前提）。"""
    from backend.app.services.agent_tools import _coerce_kind

    assert _coerce_kind("video", "成片：韩剧夜晚街头短片") == "compose"
    assert _coerce_kind("video", "镜头1：街头") == "video"
    assert _coerce_kind("compose", "成片") == "compose"


def test_submit_plan_rejected_after_plan_exists(db_session, user, v2_run):
    """R4 审计：重复 submit_plan 会静默替换计划+重置预算——必须拒绝，演化走 plan_append。"""
    ctx = _ctx(db_session, user, v2_run)
    assert _submit_plan(ctx, ["镜头1"], est=3).get("ok") is True
    again = _submit_plan(ctx, ["别的计划"], est=1)
    assert again.get("ok") is False
    fresh = store.get_run(db_session, v2_run.id)
    assert fresh is not None
    labels = [d["label"] for d in (fresh.plan() or {}).get("deliverables", [])]
    assert labels == ["镜头1"], "原计划不被替换"
    assert fresh.max_steps == 5, "预算不被重置"


def test_video_duration_guard(db_session, user, v2_run, monkeypatch):
    """run#17 实证：>15s 单镜拒收（H3 上限，provider 会静默钳制成片缩水）。"""
    import backend.app.routers.canvas as canvas_router

    async def boom(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("守卫应先于生成拒绝")

    monkeypatch.setattr(canvas_router, "_agent_generate_core", boom)
    ctx = _ctx(db_session, user, v2_run)
    result = asyncio.run(
        agent_tools.execute_action(
            ctx,
            {"tool": "generate_video", "args": {"message": "x", "params": {"duration": 20}}},
        )
    )
    assert result.get("ok") is False and "15" in str(result.get("error"))


def test_compose_creates_compose_node_with_input_order(db_session, user, v2_run, monkeypatch):
    """run#17 用户反馈：agent 成片必须是合成卡（与人工流同构）——type=compose+inputOrder。"""
    import backend.app.models as models
    import backend.app.services.agent_tools as at
    import backend.app.services.media_service as ms

    # 造两个有产物的段节点
    seg_ids = []
    for i, u in enumerate(["/outputs/videos/a.mp4", "/outputs/videos/b.mp4"]):
        n = models.CanvasNode(
            project_id=int(v2_run.project_id), type="video", title=f"段{i+1}",
            position_x=0, position_y=i * 300,
            metadata_json=json.dumps({"client_id": f"seg{i}", "outputUrl": u}),
        )
        db_session.add(n)
        db_session.commit()
        seg_ids.append(int(n.id))

    def fake_compose(urls, bgm=None, transition="none"):  # noqa: ANN001, ARG001
        return ms._outputs_root_default() / "media" / "compose_node.mp4"

    monkeypatch.setattr(ms, "compose_videos", fake_compose)
    ctx = _ctx(db_session, user, v2_run)
    result = asyncio.run(
        at.execute_action(
            ctx,
            {
                "tool": "compose_videos",
                "args": {"urls": ["/outputs/videos/a.mp4", "/outputs/videos/b.mp4"]},
            },
        )
    )
    assert result.get("ok") is True
    node = (
        db_session.query(models.CanvasNode)
        .filter(models.CanvasNode.id == result["node_id"])
        .first()
    )
    assert node is not None
    assert node.type == "compose", "成片节点必须是合成卡（非普通视频卡）"
    meta = json.loads(node.metadata_json)
    assert meta.get("inputOrder") == [str(i) for i in seg_ids], "段顺序入 inputOrder"
    assert meta.get("outputUrl"), "成片可预览"
