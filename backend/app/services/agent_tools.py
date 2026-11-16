"""Agent 工具注册表：声明式条目 + 统一派发 + 策略钩子。

spec §3：工具=数据（name/description/params/handler），脑提示词清单自动生成；
handler 复用既有服务（_agent_generate_core / 节点边服务 / media_service）。
策略钩子：reference 白名单、坐标兜底、视频串行 422→脑可见错误。
演进缝：新增 skill / MCP 外接工具 = 新注册条目（spec §8.3）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from backend.app.models import CanvasNode, Task, User

# 类型行兜底（Skill v2.0 布局约定：图行 0 / 视频行 450 / 音频行 900 / 文字卡 -300）
_ROW_BY_TYPE = {"image": 0.0, "video": 450.0, "audio": 900.0, "text": -300.0}
_SPACING = 400.0


@dataclass
class ToolContext:
    db: Session
    user: User
    run_id: int
    project_id: int
    reference_whitelist: list[str] = field(default_factory=list)
    pinned_model: dict[str, Any] | None = None  # {"channel_id", "model"}


def _err(message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, **extra}


def _ok(**extra: Any) -> dict[str, Any]:
    return {"ok": True, **extra}


def _next_slot(ctx: ToolContext, ntype: str) -> tuple[float, float]:
    """同类型节点右移一格；无同类型从行首开始。"""
    same = (
        ctx.db.query(CanvasNode)
        .filter(CanvasNode.project_id == ctx.project_id, CanvasNode.type == ntype)
        .order_by(CanvasNode.position_x.desc())
        .first()
    )
    x = (same.position_x + _SPACING) if same else 0.0
    return x, _ROW_BY_TYPE.get(ntype, 0.0)


def _validate_reference_urls(urls: list[str], ctx: ToolContext) -> str | None:
    """白名单策略：仅允许 mention 素材 ∪ 本 run 产物（站内 /outputs/ 路径）。"""
    for u in urls:
        if u not in ctx.reference_whitelist:
            return (
                f"参考 URL 不在白名单内（仅允许用户 @ 素材或本 run 产物）: {u}"
            )
    return None


async def _tool_generate(ctx: ToolContext, args: dict[str, Any], ntype: str) -> dict[str, Any]:
    from backend.app.routers.canvas import _agent_generate_core

    message = str(args.get("message") or "").strip()
    if not message:
        return _err("message 不能为空")
    params = dict(args.get("params") or {})
    deliverable_label = str(args.get("deliverable_label") or "").strip() or None
    if deliverable_label and len(deliverable_label) > 80:
        return _err("deliverable_label 过长（≤80）")
    if not isinstance(params, dict):
        return _err("params 必须是对象")

    refs = params.get("reference_urls") or []
    if refs:
        if not isinstance(refs, list):
            return _err("reference_urls 必须是数组")
        bad = _validate_reference_urls([str(u) for u in refs], ctx)
        if bad:
            return _err(bad)

    # 【2026-10-10 run#17 实证】脑会计划 20s 单镜——本地 H3 上限 15s，provider 静默钳制
    # 致成片时长缩水且无人知晓。工具层显式拒收，教脑用更多镜头凑总时长。
    if ntype == "video":
        dur = params.get("duration")
        if isinstance(dur, (int, float)) and dur > 15:
            return _err(
                "单镜头时长上限 15 秒（本地 H3 渠道）；更长成片请拆分更多镜头（如 60s=4×15s）"
            )

    # 坐标兜底：脑不传 x/y → 按类型行自动排布
    if params.get("x") is None or params.get("y") is None:
        fx, fy = _next_slot(ctx, ntype)
        params.setdefault("x", fx)
        params.setdefault("y", fy)

    # 模型 pin（首页 @模型）
    channel_id = None
    model = None
    if ctx.pinned_model:
        model = ctx.pinned_model.get("model")
        channel_id = ctx.pinned_model.get("channel_id")

    from pydantic import BaseModel

    class _GenBody(BaseModel):
        type: str = ntype
        message: str = ""
        model: str | None = None
        negative_prompt: str = ""
        channel_id: int | None = None
        params: dict = {}

    try:
        result = await _agent_generate_core(
            ctx.db,
            ctx.user,
            ctx.project_id,
            _GenBody(
                type=ntype,
                message=message,
                model=model,
                channel_id=channel_id,
                params=params,
            ),
            agent_run_id=ctx.run_id,
        )
    except HTTPException as e:
        # 视频串行 422（教育性拒绝）等 → 脑可见错误观察，不向上抛
        detail = e.detail if isinstance(e.detail, dict) else {"message": str(e.detail)}
        return _err(f"[{detail.get('code', 'REJECTED')}] {detail.get('message', '')}")

    # 参考脉络物化：@素材/取帧 URL → 画布节点 + 参考图→新节点连线（创作说明书）
    from backend.app.services.canvas_writeback import find_node_by_client_id

    reference_node_ids: list[int] = []
    new_node = (
        find_node_by_client_id(ctx.db, ctx.project_id, result.get("node_client_id"))
        if result.get("node_client_id")
        else None
    )
    if refs and new_node is not None:
        for u in [str(x) for x in refs]:
            ref_node = _materialize_reference_node(ctx, u)
            reference_node_ids.append(ref_node.id)
            _create_edge_quiet(ctx, ref_node.id, new_node.id)

    from backend.app.services import agent_events
    from backend.app.services import agent_run_store as _run_store

    if deliverable_label:
        _run_store.map_deliverable_task(
            ctx.db, ctx.run_id, deliverable_label, int(result["task_id"])
        )
    agent_events.emit(
        ctx.db,
        ctx.run_id,
        "task_submitted",
        {"task_id": result["task_id"], "type": ntype, "deliverable_label": deliverable_label},
    )
    return _ok(
        task_id=result["task_id"],
        node_client_id=result.get("node_client_id"),
        model=result.get("model"),
        deliverable_label=deliverable_label,
        reference_node_ids=reference_node_ids,
    )


def _find_node_by_output(ctx: ToolContext, url: str) -> CanvasNode | None:
    """按 metadata.outputUrl 在本画布找节点（run 内产物可直接复用，不重复物化）。"""
    return (
        ctx.db.query(CanvasNode)
        .filter(
            CanvasNode.project_id == ctx.project_id,
            CanvasNode.metadata_json.like(f"%{url}%"),
        )
        .first()
    )


def _materialize_reference_node(ctx: ToolContext, url: str) -> CanvasNode:
    """@素材/取帧参考物化为画布节点——参考脉络可见性（图→视频=首帧参考的心智模型）。"""
    import uuid

    existing = _find_node_by_output(ctx, url)
    if existing is not None:
        return existing
    ntype = "video" if any(url.endswith(e) for e in (".mp4", ".webm", ".mov")) else "image"
    cid = f"agent-{uuid.uuid4().hex[:10]}"
    node = CanvasNode(
        project_id=ctx.project_id,
        type=ntype,
        title="参考素材" if ntype == "image" else "参考视频",
        position_x=_next_slot(ctx, ntype)[0],
        position_y=_ROW_BY_TYPE.get(ntype, 0.0),
        metadata_json=json.dumps(
            {
                "client_id": cid,
                "canvas_agent": True,
                "outputUrl": url,
                "imported_reference": True,
                "source": f"agent_run:{ctx.run_id}",
            },
            ensure_ascii=False,
        ),
    )
    ctx.db.add(node)
    ctx.db.commit()
    return node


def _create_edge_quiet(ctx: ToolContext, from_id: int, to_id: int) -> None:
    """幂等连线（去重）；参考脉络：参考节点 → 新生成节点。"""
    from backend.app.models import CanvasConnection

    dup = (
        ctx.db.query(CanvasConnection)
        .filter(
            CanvasConnection.project_id == ctx.project_id,
            CanvasConnection.from_node_id == from_id,
            CanvasConnection.to_node_id == to_id,
        )
        .first()
    )
    if dup is None:
        ctx.db.add(
            CanvasConnection(project_id=ctx.project_id, from_node_id=from_id, to_node_id=to_id)
        )
        ctx.db.commit()


def _tool_create_text_node(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    import uuid

    title = str(args.get("title") or "").strip()[:200]
    prompt = str(args.get("prompt") or "").strip()
    if not prompt and not title:
        return _err("title/prompt 至少给一个")
    x = args.get("x")
    y = args.get("y")
    if x is None or y is None:
        fx, fy = _next_slot(ctx, "text")
        x = fx if x is None else x
        y = fy if y is None else y
    cid = f"agent-{uuid.uuid4().hex[:10]}"
    node = CanvasNode(
        project_id=ctx.project_id,
        type="text",
        title=title or prompt[:30] or "Agent 节点",
        position_x=float(x),
        position_y=float(y),
        metadata_json=json.dumps(
            {
                "client_id": cid,
                "prompt": prompt,
                "canvas_agent": True,
                "source": f"agent_run:{ctx.run_id}",
            },
            ensure_ascii=False,
        ),
    )
    ctx.db.add(node)
    ctx.db.commit()
    return _ok(node_id=node.id, client_id=cid)


def _tool_create_edge(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    from backend.app.models import CanvasConnection

    frm = args.get("from_node_id")
    to = args.get("to_node_id")
    if not isinstance(frm, int) or not isinstance(to, int):
        return _err("from_node_id/to_node_id 必须是整数")
    ids = {
        n.id for n in ctx.db.query(CanvasNode.id).filter(CanvasNode.project_id == ctx.project_id)
    }
    if frm not in ids or to not in ids:
        return _err("连线引用了不属于该画布的节点")
    dup = (
        ctx.db.query(CanvasConnection)
        .filter(
            CanvasConnection.project_id == ctx.project_id,
            CanvasConnection.from_node_id == frm,
            CanvasConnection.to_node_id == to,
        )
        .first()
    )
    if dup is None:
        ctx.db.add(
            CanvasConnection(project_id=ctx.project_id, from_node_id=frm, to_node_id=to)
        )
        ctx.db.commit()
    return _ok(from_node_id=frm, to_node_id=to)


def _tool_extract_frame(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    from backend.app.services import media_service
    from backend.app.services.media_service import MediaError

    url = str(args.get("url") or "")
    if not url.startswith("/outputs/") or ".." in url:
        return _err("仅支持站内 /outputs/ 视频路径")
    try:
        t = float(args.get("time", -1))
        out = (
            media_service.extract_last_frame(media_service.ensure_local(url))
            if t < 0
            else media_service.extract_frame(media_service.ensure_local(url), t)
        )
    except (MediaError, ValueError) as e:
        return _err(f"取帧失败: {e}")
    frame_url = f"/outputs/{out.relative_to(media_service._outputs_root_default()).as_posix()}"
    # 帧产物必须落库白名单：ToolContext 每轮重建，仅内存 append 会导致下一轮
    # 接力 generate 的 reference_urls 被白名单拒（尾帧接力范式 B 不可用）
    from backend.app.services import agent_run_store as _run_store

    _run_store.extend_reference_whitelist(ctx.db, ctx.run_id, [frame_url])
    ctx.reference_whitelist.append(frame_url)  # 本轮内立即可用
    return _ok(url=frame_url)


def _tool_query_task(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    tid = args.get("task_id")
    if not isinstance(tid, int):
        return _err("task_id 必须是整数")
    t = ctx.db.query(Task).filter(Task.id == tid, Task.user_id == ctx.user.id).first()
    if t is None:
        return _err("任务不存在")
    return _ok(
        task_id=t.id,
        type=t.type,
        status=t.status,
        output_url=t.output_url,
        error=t.error_msg,
    )


def _tool_list_characters(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    """列出用户角色库——脑中途引用角色保持跨场一致性。"""
    from backend.app.models import CharacterAsset

    chars = (
        ctx.db.query(CharacterAsset)
        .filter(CharacterAsset.user_id == ctx.user.id)
        .order_by(CharacterAsset.id)
        .all()
    )
    return _ok(
        characters=[
            {
                "id": c.id,
                "name": c.name,
                "description": (c.description or "")[:200],
                "ref_image_url": c.ref_image_url,
            }
            for c in chars
        ]
    )


def _tool_create_character_node(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    """角色卡上画布——连线到关键帧/视频 = "保持此角色外观"。"""
    import uuid

    from backend.app.models import CharacterAsset

    cid_db = args.get("character_id")
    if not isinstance(cid_db, int):
        return _err("character_id 必须是整数")
    c = (
        ctx.db.query(CharacterAsset)
        .filter(CharacterAsset.id == cid_db, CharacterAsset.user_id == ctx.user.id)
        .first()
    )
    if c is None:
        return _err("角色不存在")
    node_cid = f"agent-{uuid.uuid4().hex[:10]}"
    node = CanvasNode(
        project_id=ctx.project_id,
        type="character",
        title=c.name,
        position_x=args.get("x", _next_slot(ctx, "character")[0]),
        position_y=args.get("y", -600.0),
        metadata_json=json.dumps(
            {
                "client_id": node_cid,
                "canvas_agent": True,
                "source": f"agent_run:{ctx.run_id}",
                "character_id": c.id,
                "character_name": c.name,
                "character_desc": c.description or "",
                "outputUrl": c.ref_image_url or None,
            },
            ensure_ascii=False,
        ),
    )
    ctx.db.add(node)
    ctx.db.commit()
    return _ok(node_id=node.id, client_id=node_cid, name=c.name)


def _tool_compose_videos(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    from backend.app.services import media_service
    from backend.app.services.media_service import MediaError

    urls = args.get("urls")
    if not isinstance(urls, list) or len(urls) < 2 or not all(isinstance(u, str) for u in urls):
        return _err("urls 必须是 ≥2 个站内视频路径数组")
    for u in urls:
        if not u.startswith("/outputs/") or ".." in u:
            return _err("仅支持站内 /outputs/ 视频路径")
    bgm = args.get("bgm_url")
    if bgm and (not isinstance(bgm, str) or not bgm.startswith("/outputs/")):
        return _err("bgm_url 必须是站内音频路径")
    transition = str(args.get("transition") or "none")
    if transition not in media_service.COMPOSE_TRANSITIONS:
        return _err(f"transition 须为 {'/'.join(media_service.COMPOSE_TRANSITIONS)} 之一")
    try:
        out = media_service.compose_videos(
            urls, bgm if isinstance(bgm, str) else None, transition=transition
        )
    except MediaError as e:
        return _err(f"合成失败: {e}")
    frame_url = f"/outputs/{out.relative_to(media_service._outputs_root_default()).as_posix()}"
    # 【run#11 回放实证修复】同步工具无 task_id：url 证据入中途投影——显式 label 优先，
    # 缺省自动映射 plan 中 kind=compose 的交付物（脑不必记得传 label）
    from backend.app.services import agent_run_store as _rs

    run = _rs.get_run(ctx.db, ctx.run_id)
    label = str(args.get("deliverable_label") or "").strip() or None
    if label is None and run is not None:
        plan = run.plan() or {}
        for d in plan.get("deliverables") or []:
            if d.get("kind") == "compose":
                label = str(d.get("label") or "")
                break
    if label:
        _rs.map_deliverable_evidence(ctx.db, ctx.run_id, label, f"url:{frame_url}")
    # 成片物化为合成节点（与人工 ComposeDialog 同构——run#17 用户实测反馈：
    # agent 成片落普通视频卡，丢了段落定序/再合成/预览的整卡能力）+ 各段→成片连线
    import uuid as _uuid

    seg_node_ids: list[int] = []
    for u in urls:
        src = _find_node_by_output(ctx, u)
        if src is not None:
            seg_node_ids.append(src.id)

    cid = f"agent-{_uuid.uuid4().hex[:10]}"
    node = CanvasNode(
        project_id=ctx.project_id,
        type="compose",
        title="成片",
        position_x=_next_slot(ctx, "video")[0],
        position_y=_ROW_BY_TYPE.get("video", 450.0) + 300,
        metadata_json=json.dumps(
            {
                "client_id": cid,
                "canvas_agent": True,
                "outputUrl": frame_url,
                "source": f"agent_run:{ctx.run_id}",
                "composed": True,
                "transition": transition,
                "inputOrder": [str(i) for i in seg_node_ids],
            },
            ensure_ascii=False,
        ),
    )
    ctx.db.add(node)
    ctx.db.commit()
    for nid in seg_node_ids:
        _create_edge_quiet(ctx, nid, node.id)
    return _ok(url=frame_url, node_id=node.id)


def _tool_remove_own_node(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    nid = args.get("node_id")
    if not isinstance(nid, int):
        return _err("node_id 必须是整数")
    node = (
        ctx.db.query(CanvasNode)
        .filter(CanvasNode.id == nid, CanvasNode.project_id == ctx.project_id)
        .first()
    )
    if node is None:
        return _err("节点不存在")
    try:
        meta = json.loads(node.metadata_json or "{}")
    except json.JSONDecodeError:
        meta = {}
    if meta.get("source") != f"agent_run:{ctx.run_id}":
        return _err("只允许删除本 run 创建的节点（用户节点只读）")
    ctx.db.delete(node)  # 连线随节点级联清理
    ctx.db.commit()
    return _ok(node_id=nid)


# ---------- 注册表 ----------

# ---------- L1 计划合约工具（设计 §5） ----------

_PLAN_KINDS = ("image", "video", "audio", "text", "compose")
HARD_STEP_CAP = 60
# run#15 实证：脑常把成片写成 kind=video，绕过 compose 自动映射/证据转移——提交时归一
_COMPOSE_LABEL_RE = re.compile(r"成片|合成|合集|拼接")


def _coerce_kind(kind: str, label: str) -> str:
    if kind == "video" and _COMPOSE_LABEL_RE.search(label):
        return "compose"
    return kind


def _validate_deliverables(items: Any) -> list[dict[str, str]] | str:
    if not isinstance(items, list) or not (1 <= len(items) <= 40):
        return "deliverables 必须是 1-40 项的数组"
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for it in items:
        if not isinstance(it, dict):
            return "交付物项必须是对象"
        kind = str(it.get("kind") or "video")
        label = str(it.get("label") or "").strip()
        if kind not in _PLAN_KINDS:
            return f"kind 必须是 {'/'.join(_PLAN_KINDS)} 之一"
        if not label or len(label) > 80 or label in seen:
            return "label 必须非空、≤80 字符且不重复"
        seen.add(label)
        out.append({"kind": _coerce_kind(kind, label), "label": label})
    return out


def _tool_submit_plan(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    from backend.app.services import agent_events
    from backend.app.services import agent_run_store as _store

    deliverables = _validate_deliverables(args.get("deliverables"))
    if isinstance(deliverables, str):
        return _err(deliverables)
    try:
        est = int(args.get("est_steps"))
    except (TypeError, ValueError):
        return _err("est_steps 必须是整数")
    if not (1 <= est <= HARD_STEP_CAP):
        return _err(f"est_steps 须在 1-{HARD_STEP_CAP}")

    run = _store.get_run(ctx.db, ctx.run_id)
    if run is not None and run.plan() is not None:
        return _err("已有计划——中途调整请用 plan_append（防静默替换计划/重置预算）")
    plan = {"deliverables": deliverables, "est_steps": est}
    _store.set_plan(ctx.db, ctx.run_id, plan)
    granted = min(est + 2, HARD_STEP_CAP)
    _store.update_max_steps(ctx.db, ctx.run_id, granted)
    agent_events.emit(
        ctx.db, ctx.run_id, "plan_submitted",
        {"deliverables": deliverables, "est_steps": est},
    )
    agent_events.emit(
        ctx.db, ctx.run_id, "budget_granted",
        {"from": 12, "to": granted, "reason": "plan"},
    )
    return _ok(deliverables=len(deliverables), max_steps=granted)


def _tool_plan_append(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    from backend.app.services import agent_events
    from backend.app.services import agent_run_store as _store

    run = _store.get_run(ctx.db, ctx.run_id)
    plan = run.plan() if run else None
    if plan is None:
        return _err("尚未提交计划，先 submit_plan")
    deliverables = _validate_deliverables(args.get("deliverables"))
    if isinstance(deliverables, str):
        return _err(deliverables)
    try:
        delta = int(args.get("est_delta"))
    except (TypeError, ValueError):
        return _err("est_delta 必须是整数")
    if not (1 <= delta <= HARD_STEP_CAP):
        return _err(f"est_delta 须在 1-{HARD_STEP_CAP}")

    existing = {d["label"] for d in plan["deliverables"]}
    for d in deliverables:
        if d["label"] in existing:
            return _err(f"交付物已存在: {d['label']}")
    plan["deliverables"].extend(deliverables)
    plan["est_steps"] = int(plan.get("est_steps") or 0) + delta
    _store.set_plan(ctx.db, ctx.run_id, plan)
    target = min(plan["est_steps"] + 2, HARD_STEP_CAP)
    if run is not None and target > (run.max_steps or 0):
        _store.update_max_steps(ctx.db, ctx.run_id, target)
        agent_events.emit(
            ctx.db, ctx.run_id, "budget_granted", {"to": target, "reason": "append"}
        )
    agent_events.emit(
        ctx.db, ctx.run_id, "plan_append", {"added": deliverables, "est_delta": delta}
    )
    return _ok(deliverables=len(plan["deliverables"]), max_steps=target)


_REGISTRY: list[dict[str, Any]] = [
    {
        "name": "submit_plan",
        "description": (
            "【首个动作必须调用】提交创作计划：交付物清单（kind+label）+ 预计步数。"
            "系统据此协商步数预算（est_steps+2，上限 60）并以此校验最终交付。"
            "发现需要追加交付物时用 plan_append"
        ),
        "params": {
            "deliverables": (
                '[{"kind": "video", "label": "镜头1"}, '
                '{"kind": "compose", "label": "成片：xxx"}]（成片/合成类 kind 必须用 compose）'
            ),
            "est_steps": "预计动作步数（1-60）",
        },
        "handler": _tool_submit_plan,
    },
    {
        "name": "plan_append",
        "description": "追加交付物到既有计划（计划演化）；系统自动扩预算",
        "params": {"deliverables": "新增交付物数组", "est_delta": "追加步数"},
        "handler": _tool_plan_append,
    },
    {
        "name": "generate_image",
        "description": (
            "生成图片（自动落画布节点）。params 可含 image_count/reference_urls/x/y。"
            "服务计划时传 deliverable_label 对应 submit_plan 里的 label"
        ),
        "params": {
            "message": "提示词",
            "params": "可选参数对象",
            "deliverable_label": "所属交付物 label",
        },
        "handler": lambda ctx, args: _tool_generate(ctx, args, "image"),
    },
    {
        "name": "generate_video",
        "description": (
            "生成视频（自动落画布节点；串行纪律：一次只能有一个进行中）。"
            "params 可含 duration/resolution/reference_urls(首帧)/x/y。"
            "服务计划时传 deliverable_label 对应 submit_plan 里的 label"
        ),
        "params": {
            "message": "提示词",
            "params": "可选参数对象",
            "deliverable_label": "所属交付物 label",
        },
        "handler": lambda ctx, args: _tool_generate(ctx, args, "video"),
    },
    {
        "name": "generate_audio",
        "description": "生成音频（自动落画布节点）。params 可含 voice/x/y",
        "params": {"message": "提示词", "params": "可选参数对象"},
        "handler": lambda ctx, args: _tool_generate(ctx, args, "audio"),
    },
    {
        "name": "create_text_node",
        "description": "创建文字卡节点（分镜/计划/说明），可指定 title/prompt/x/y",
        "params": {"title": "标题", "prompt": "内容", "x": "横坐标", "y": "纵坐标"},
        "handler": _tool_create_text_node,
    },
    {
        "name": "create_edge",
        "description": "节点连线（图→视频=首帧参考；图→图=参考图）——创作脉络",
        "params": {"from_node_id": "起点节点 id", "to_node_id": "终点节点 id"},
        "handler": _tool_create_edge,
    },
    {
        "name": "extract_frame",
        "description": "从视频取帧（time=-1 末帧——尾帧接力续拍的基础），返回站内图片路径",
        "params": {"url": "站内 /outputs/ 视频路径", "time": "秒，-1 为末帧"},
        "handler": _tool_extract_frame,
    },
    {
        "name": "query_task",
        "description": "查询任务状态与产物 URL",
        "params": {"task_id": "任务 id"},
        "handler": _tool_query_task,
    },
    {
        "name": "remove_own_node",
        "description": "删除本 run 自己创建的废案节点（用户节点只读）",
        "params": {"node_id": "节点 id"},
        "handler": _tool_remove_own_node,
    },
    {
        "name": "list_characters",
        "description": "列出用户角色库（名字/外观描述/定妆照URL）——跨场一致性引用",
        "params": {},
        "handler": _tool_list_characters,
    },
    {
        "name": "create_character_node",
        "description": "角色卡上画布（定妆照+外观描述）——连线到关键帧/视频节点 = 保持此角色外观",
        "params": {"character_id": "角色库 id", "x": "横坐标（可选）", "y": "纵坐标（可选）"},
        "handler": _tool_create_character_node,
    },
    {
        "name": "compose_videos",
        "description": (
            "多段视频按序拼接成片（不同分辨率自动归一；可选 BGM 25% 混音）。"
            "多场次创作完成后用此工具交付成片。urls 必须来自本 run 产物"
        ),
        "params": {
            "urls": "站内 /outputs/ 视频路径数组（按播放顺序）",
            "bgm_url": "可选站内音频路径（25% 音量铺满）",
            "transition": "段间转场：none/fade/slideleft/circleopen/wipeleft（缺省硬切）",
            "deliverable_label": "所属交付物 label（缺省自动映射 kind=compose 的交付物）",
        },
        "handler": _tool_compose_videos,
    },
    {
        "name": "done",
        "description": "任务完成，附 summary 总结（不再有后续动作）",
        "params": {"summary": "成果摘要"},
        "handler": lambda ctx, args: _ok(done=True),
    },
]
_REGISTRY_BY_NAME = {t["name"]: t for t in _REGISTRY}


def tool_manifest() -> list[dict[str, Any]]:
    """脑提示词工具清单（从注册表生成）。"""
    return [
        {"name": t["name"], "description": t["description"], "params": t["params"]}
        for t in _REGISTRY
    ]


async def execute_action(ctx: ToolContext, action: dict[str, Any]) -> dict[str, Any]:
    """统一派发：未知工具/参数异常 → 脑可见错误观察（永不向上抛，脑可自适应）。"""
    name = action.get("tool")
    args = action.get("args") or {}
    if not isinstance(name, str) or name not in _REGISTRY_BY_NAME:
        return _err(f"未知工具: {name}")
    if not isinstance(args, dict):
        return _err("args 必须是对象")
    handler = _REGISTRY_BY_NAME[name]["handler"]
    result = handler(ctx, args)
    if hasattr(result, "__await__"):
        result = await result
    return result
