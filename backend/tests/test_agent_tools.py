"""agent_tools 单测：注册表清单 / 派发 / 视频串行转换 / 白名单 / 自删守卫 / 坐标兜底。"""

from __future__ import annotations

import json

import pytest

from backend.app.models import CanvasNode, CanvasProject, User
from backend.app.services.agent_tools import (
    ToolContext,
    execute_action,
    tool_manifest,
)


def _mk_user(db_session):  # noqa: ANN001
    u = User(username="alice", email="alice@t.local", password_hash="x")
    db_session.add(u)
    db_session.commit()
    return u


@pytest.fixture
def setup(db_session):  # noqa: ANN001
    u = _mk_user(db_session)
    p = CanvasProject(user_id=u.id, title="Agent 创作·测试")
    db_session.add(p)
    db_session.commit()
    return u, p


def _ctx(db_session, setup, whitelist=None, pinned=None):  # noqa: ANN001
    u, p = setup
    return ToolContext(
        db=db_session,
        user=u,
        run_id=7,
        project_id=p.id,
        reference_whitelist=list(whitelist or []),
        pinned_model=pinned,
    )


# ---------- 注册表 ----------

def test_manifest_contains_all_tools():
    names = {t["name"] for t in tool_manifest()}
    assert {
        "generate_image",
        "generate_video",
        "generate_audio",
        "create_text_node",
        "create_edge",
        "extract_frame",
        "query_task",
        "remove_own_node",
        "done",
    } <= names


def test_execute_unknown_tool_returns_error(setup, db_session):
    import asyncio

    r = asyncio.run(
        execute_action(_ctx(db_session, setup), {"tool": "hack_the_planet", "args": {}})
    )
    assert r["ok"] is False and "未知工具" in r["error"]


# ---------- generate：source 标记 / agent_run_id 透传 / 模型 pin ----------

def test_generate_image_marks_source_and_run_id(setup, db_session, monkeypatch):
    captured = {}

    async def fake_core(db, user, project_id, body, agent_run_id=None):  # noqa: ANN001
        captured["project_id"] = project_id
        captured["agent_run_id"] = agent_run_id
        captured["body"] = body
        return {"task_id": 42, "node_client_id": "agent-xyz", "model": "m", "status": "queued"}

    from backend.app.routers import canvas as canvas_router

    monkeypatch.setattr(canvas_router, "_agent_generate_core", fake_core)

    import asyncio

    r = asyncio.run(
        execute_action(
            _ctx(db_session, setup),
            {
                "tool": "generate_image",
                "args": {"message": "赛博朋克猫", "params": {"x": 100, "y": 0}},
            },
        )
    )
    assert r["ok"] is True and r["task_id"] == 42
    assert captured["agent_run_id"] == 7
    assert captured["body"].type == "image"


def test_generate_video_serial_422_becomes_error_observation(setup, db_session, monkeypatch):
    """视频串行纪律：继承 _agent_generate_core 的 422 → 脑可见错误（不向上抛）。"""
    from fastapi import HTTPException

    async def busy(db, user, project_id, body, agent_run_id=None):  # noqa: ANN001
        raise HTTPException(
            status_code=422,
            detail={"code": "AGENT_VIDEO_SERIAL", "message": "已有进行中的视频任务"},
        )

    from backend.app.routers import canvas as canvas_router

    monkeypatch.setattr(canvas_router, "_agent_generate_core", busy)

    import asyncio

    r = asyncio.run(
        execute_action(
            _ctx(db_session, setup), {"tool": "generate_video", "args": {"message": "猫走动"}}
        )
    )
    assert r["ok"] is False
    assert "AGENT_VIDEO_SERIAL" in r["error"] or "视频" in r["error"]


def test_generate_reference_whitelist_enforced(setup, db_session, monkeypatch):
    """reference_urls 只允许白名单（mention 素材 ∪ run 产物）。"""
    called = False

    async def nope(db, user, project_id, body, agent_run_id=None):  # noqa: ANN001
        nonlocal called
        called = True
        return {}

    from backend.app.routers import canvas as canvas_router

    monkeypatch.setattr(canvas_router, "_agent_generate_core", nope)

    import asyncio

    r = asyncio.run(
        execute_action(
            _ctx(db_session, setup, whitelist=["/outputs/images/own.png"]),
            {
                "tool": "generate_image",
                "args": {"message": "参考这张", "params": {"reference_urls": ["http://evil/x.png"]}},
            },
        )
    )
    assert r["ok"] is False and called is False
    assert "参考" in r["error"] or "白名单" in r["error"]


def test_generate_whitelisted_reference_passes(setup, db_session, monkeypatch):
    captured = {}

    async def ok_core(db, user, project_id, body, agent_run_id=None):  # noqa: ANN001
        captured["params"] = body.params
        return {"task_id": 1}

    from backend.app.routers import canvas as canvas_router

    monkeypatch.setattr(canvas_router, "_agent_generate_core", ok_core)

    import asyncio

    r = asyncio.run(
        execute_action(
            _ctx(db_session, setup, whitelist=["/outputs/images/own.png"]),
            {
                "tool": "generate_image",
                "args": {
                    "message": "参考",
                    "params": {"reference_urls": ["/outputs/images/own.png"]},
                },
            },
        )
    )
    assert r["ok"] is True
    assert captured["params"]["reference_urls"] == ["/outputs/images/own.png"]


def test_coordinate_fallback_by_type(setup, db_session, monkeypatch):
    """脑不传 x/y → 按类型行兜底（图 y=0 / 视频 y=450 / 音频 y=900 / 文字 y=-300）。"""
    captured = {}

    async def ok_core(db, user, project_id, body, agent_run_id=None):  # noqa: ANN001
        captured["params"] = body.params
        return {"task_id": 1}

    from backend.app.routers import canvas as canvas_router

    monkeypatch.setattr(canvas_router, "_agent_generate_core", ok_core)

    import asyncio

    asyncio.run(
        execute_action(
            _ctx(db_session, setup), {"tool": "generate_video", "args": {"message": "v"}}
        )
    )
    assert captured["params"]["y"] == 450


# ---------- 节点 / 连线 / 自删守卫 ----------

def test_create_text_node_marks_source(setup, db_session):
    import asyncio

    r = asyncio.run(
        execute_action(
            _ctx(db_session, setup),
            {"tool": "create_text_node", "args": {"title": "分镜", "prompt": "第一镜：开场"}},
        )
    )
    assert r["ok"] is True and r["node_id"] > 0
    node = db_session.get(CanvasNode, r["node_id"])
    meta = json.loads(node.metadata_json)
    assert meta["source"] == "agent_run:7"


def test_create_edge_validates_nodes(setup, db_session):
    import asyncio

    r = asyncio.run(
        execute_action(
            _ctx(db_session, setup),
            {"tool": "create_edge", "args": {"from_node_id": 999, "to_node_id": 1000}},
        )
    )
    assert r["ok"] is False  # 节点不属于画布


def test_remove_own_node_guard(setup, db_session):
    u, p = setup
    # 用户旧节点（无 source）
    foreign = CanvasNode(project_id=p.id, type="text", title="用户的", position_x=0, position_y=0)
    # run 自建节点
    own = CanvasNode(
        project_id=p.id,
        type="text",
        title="Agent 的",
        position_x=0,
        position_y=0,
        metadata_json=json.dumps({"source": "agent_run:7"}),
    )
    db_session.add_all([foreign, own])
    db_session.commit()

    import asyncio

    r_foreign = asyncio.run(
        execute_action(
            _ctx(db_session, setup),
            {"tool": "remove_own_node", "args": {"node_id": foreign.id}},
        )
    )
    assert r_foreign["ok"] is False and "只允许删除" in r_foreign["error"]

    r_own = asyncio.run(
        execute_action(
            _ctx(db_session, setup), {"tool": "remove_own_node", "args": {"node_id": own.id}}
        )
    )
    assert r_own["ok"] is True
    assert db_session.get(CanvasNode, own.id) is None


def test_query_task(setup, db_session):
    from backend.app.models import Task

    t = Task(
        user_id=setup[0].id,
        type="image",
        prompt="p",
        model="m",
        status="completed",
        output_url="/outputs/images/x.png",
    )
    db_session.add(t)
    db_session.commit()

    import asyncio

    r = asyncio.run(
        execute_action(_ctx(db_session, setup), {"tool": "query_task", "args": {"task_id": t.id}})
    )
    assert r["ok"] is True and r["status"] == "completed"


def test_extract_frame_rejects_external(setup, db_session):
    import asyncio

    r = asyncio.run(
        execute_action(
            _ctx(db_session, setup), {"tool": "extract_frame", "args": {"url": "http://evil/v.mp4"}}
        )
    )
    assert r["ok"] is False


def test_done_tool(setup, db_session):
    import asyncio

    r = asyncio.run(execute_action(_ctx(db_session, setup), {"tool": "done", "args": {}}))
    assert r["ok"] is True and r["done"] is True


def test_extract_frame_persists_whitelist(db_session, setup, monkeypatch, tmp_path):
    """取帧产物必须落库白名单——否则下一轮 ToolContext 重建后尾帧接力被拒。"""
    from pathlib import Path

    from backend.app.services import agent_run_store as store
    from backend.app.services import media_service

    u, p = setup
    run = store.create_run(
        db_session, user_id=u.id, project_id=p.id, initial_message="接力", mentions=[]
    )
    ctx = ToolContext(
        db=db_session, user=u, run_id=run.id, project_id=p.id, reference_whitelist=[]
    )

    (tmp_path / "frame_x.png").write_bytes(b"png")
    monkeypatch.setattr(media_service, "extract_last_frame", lambda src: tmp_path / "frame_x.png")
    monkeypatch.setattr(media_service, "ensure_local", lambda url: Path("/outputs/videos/v.mp4"))
    monkeypatch.setattr(media_service, "_outputs_root_default", lambda: tmp_path)

    import asyncio

    r = asyncio.run(
        execute_action(
            ctx, {"tool": "extract_frame", "args": {"url": "/outputs/videos/v.mp4", "time": -1}}
        )
    )
    assert r["ok"] is True
    assert "/outputs/frame_x.png" in store.get_reference_whitelist(db_session, run.id)


def test_generate_reference_materialized_on_canvas(db_session, setup, monkeypatch):
    """@素材作参考 → 画布物化参考节点 + 参考图→新节点连线（创作脉络可见）。"""
    import asyncio

    from backend.app.models import CanvasConnection, CanvasNode
    from backend.app.services import agent_run_store as store

    u, p = setup
    run = store.create_run(
        db_session, user_id=u.id, project_id=p.id, initial_message="x", mentions=[]
    )

    async def fake_core(db, usr, project_id, body, agent_run_id=None):  # noqa: ANN001
        # 真实核心会先建节点——模拟之
        import uuid as _u

        cid = f"agent-{_u.uuid4().hex[:10]}"
        n = CanvasNode(
            project_id=project_id, type=body.type, title=body.message[:20],
            position_x=0, position_y=450,
            metadata_json=json.dumps({"client_id": cid, "canvas_agent": True}, ensure_ascii=False),
        )
        db.add(n)
        db.commit()
        return {"task_id": 77, "node_client_id": cid, "model": "m", "status": "queued"}

    from backend.app.routers import canvas as canvas_router

    monkeypatch.setattr(canvas_router, "_agent_generate_core", fake_core)

    ctx = ToolContext(
        db=db_session, user=u, run_id=run.id, project_id=p.id,
        reference_whitelist=["/outputs/images/ref.png"],
    )
    r = asyncio.run(
        execute_action(
            ctx,
            {"tool": "generate_video", "args": {
                "message": "让这张图动起来",
                "params": {"reference_urls": ["/outputs/images/ref.png"]},
            }},
        )
    )
    assert r["ok"] is True
    # 参考节点已物化（带 outputUrl + source 标记）
    ref = db_session.query(CanvasNode).filter(
        CanvasNode.project_id == p.id,
        CanvasNode.metadata_json.like("%ref.png%"),
    ).first()
    assert ref is not None
    meta = json.loads(ref.metadata_json)
    assert meta.get("outputUrl") == "/outputs/images/ref.png"
    assert meta.get("source") == f"agent_run:{run.id}"
    # 连线：参考图 → 新视频节点
    video = db_session.query(CanvasNode).filter(
        CanvasNode.project_id == p.id, CanvasNode.type == "video"
    ).first()
    edge = db_session.query(CanvasConnection).filter(
        CanvasConnection.project_id == p.id,
        CanvasConnection.from_node_id == ref.id,
        CanvasConnection.to_node_id == video.id,
    ).first()
    assert edge is not None
    assert r.get("reference_node_ids") == [ref.id]


def test_reference_no_duplicate_when_node_exists(db_session, setup, monkeypatch):
    """参考 URL 已是画布节点产物（run 内生成）→ 不重复建节点，只补连线。"""
    import asyncio
    import uuid as _u

    from backend.app.models import CanvasNode
    from backend.app.services import agent_run_store as store

    u, p = setup
    run = store.create_run(
        db_session, user_id=u.id, project_id=p.id, initial_message="y", mentions=[]
    )
    existing = CanvasNode(
        project_id=p.id, type="image", title="已有",
        position_x=0, position_y=0,
        metadata_json=json.dumps({"outputUrl": "/outputs/images/own.png", "canvas_agent": True}),
    )
    db_session.add(existing)
    db_session.commit()

    async def fake_core(db, usr, project_id, body, agent_run_id=None):  # noqa: ANN001
        cid = f"agent-{_u.uuid4().hex[:10]}"
        db.add(
            CanvasNode(
                project_id=project_id, type="video", title="v",
                position_x=400, position_y=450,
                metadata_json=json.dumps({"client_id": cid}),
            )
        )
        db.commit()
        return {"task_id": 78, "node_client_id": cid, "model": "m", "status": "queued"}

    from backend.app.routers import canvas as canvas_router

    monkeypatch.setattr(canvas_router, "_agent_generate_core", fake_core)

    ctx = ToolContext(
        db=db_session, user=u, run_id=run.id, project_id=p.id,
        reference_whitelist=["/outputs/images/own.png"],
    )
    asyncio.run(
        execute_action(
            ctx,
            {"tool": "generate_video", "args": {
                "message": "动", "params": {"reference_urls": ["/outputs/images/own.png"]},
            }},
        )
    )
    # 无重复：project 内 outputUrl=own.png 的节点仍只有 1 个
    cnt = db_session.query(CanvasNode).filter(
        CanvasNode.project_id == p.id,
        CanvasNode.metadata_json.like("%own.png%"),
    ).count()
    assert cnt == 1


def test_compose_videos_tool(db_session, setup, monkeypatch, tmp_path):
    """Agent 合成工具：成片物化节点 + 各段→成片连线 + source 标记。"""
    import asyncio

    from backend.app.models import CanvasNode
    from backend.app.services import agent_run_store as store
    from backend.app.services import media_service

    u, p = setup
    run = store.create_run(
        db_session, user_id=u.id, project_id=p.id, initial_message="x", mentions=[]
    )
    # 两段源视频节点
    for i, url in enumerate(["/outputs/videos/s1.mp4", "/outputs/videos/s2.mp4"]):
        db_session.add(CanvasNode(
            project_id=p.id, type="video", title=f"s{i+1}", position_x=i * 400, position_y=450,
            metadata_json=json.dumps({"outputUrl": url, "client_id": f"src{i}"}),
        ))
    db_session.commit()

    (tmp_path / "out.mp4").write_bytes(b"v")
    monkeypatch.setattr(
        media_service, "compose_videos",
        lambda urls, bgm=None, root=None, transition="none": tmp_path / "out.mp4",
    )
    # agent_tools 函数内 from-import 拿的是源模块属性——直接 patch 源模块
    monkeypatch.setattr(
        media_service,
        "compose_videos",
        lambda urls, bgm=None, root=None, transition="none": tmp_path / "out.mp4",
    )
    monkeypatch.setattr(media_service, "_outputs_root_default", lambda: tmp_path)

    ctx = ToolContext(db=db_session, user=u, run_id=run.id, project_id=p.id)
    r = asyncio.run(
        execute_action(ctx, {"tool": "compose_videos", "args": {"urls": [
            "/outputs/videos/s1.mp4", "/outputs/videos/s2.mp4"]}})
    )
    assert r["ok"] is True and r["url"].startswith("/outputs/")
    final = db_session.query(CanvasNode).filter(
        CanvasNode.project_id == p.id, CanvasNode.metadata_json.like("%composed%")
    ).first()
    assert final is not None
    from backend.app.models import CanvasConnection

    edges = (
        db_session.query(CanvasConnection)
        .filter(CanvasConnection.to_node_id == final.id)
        .count()
    )
    assert edges == 2


def test_character_tools(db_session, setup):
    """角色工具：list + create_character_node（物化+source 标记）。"""
    import asyncio

    from backend.app.models import CanvasNode, CharacterAsset
    from backend.app.services import agent_run_store as store

    u, p = setup
    run = store.create_run(
        db_session, user_id=u.id, project_id=p.id, initial_message="ch", mentions=[]
    )
    ch = CharacterAsset(user_id=u.id, name="小雨", description="blue hair, red jacket")
    db_session.add(ch)
    db_session.commit()

    ctx = ToolContext(db=db_session, user=u, run_id=run.id, project_id=p.id)

    r1 = asyncio.run(execute_action(ctx, {"tool": "list_characters", "args": {}}))
    assert r1["ok"] and r1["characters"][0]["name"] == "小雨"

    r2 = asyncio.run(
        execute_action(ctx, {"tool": "create_character_node", "args": {"character_id": ch.id}})
    )
    assert r2["ok"] and r2["name"] == "小雨"
    node = db_session.get(CanvasNode, r2["node_id"])
    meta = json.loads(node.metadata_json)
    assert node.type == "character"
    assert meta["character_id"] == ch.id
    assert meta["source"] == f"agent_run:{run.id}"
