"""画布路由：项目 CRUD + 节点/连线整体同步。

derived from creagen prod app/routers/canvas.py @ 2026-09-27。
"""

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.core.db import get_db
from backend.app.core.times import iso_utc
from backend.app.models import CanvasConnection, CanvasNode, CanvasProject, User
from backend.app.routers.auth import get_current_user
from backend.app.schemas.canvas import (
    BulkConnectionsUpdate,
    BulkNodesUpdate,
    ProjectCreate,
    ProjectUpdate,
)

router = APIRouter(prefix="/api/canvas", tags=["canvas"])


def _own_project_or_404(db: Session, project_id: int, user: User) -> CanvasProject:
    project = db.get(CanvasProject, project_id)
    if project is None or project.user_id != user.id or project.deleted_at is not None:
        raise HTTPException(
            status_code=404, detail={"code": "PROJECT_NOT_FOUND", "message": "画布不存在"}
        )
    return project


def _own_trashed_or_404(db: Session, project_id: int, user: User) -> CanvasProject:
    project = db.get(CanvasProject, project_id)
    if project is None or project.user_id != user.id or project.deleted_at is None:
        raise HTTPException(
            status_code=404, detail={"code": "PROJECT_NOT_FOUND", "message": "回收站中不存在"}
        )
    return project


@router.get("/projects")
def list_projects(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    projects = (
        db.query(CanvasProject)
        .filter(CanvasProject.user_id == user.id, CanvasProject.deleted_at.is_(None))
        .order_by(CanvasProject.updated_at.desc())
        .all()
    )
    return {"projects": [_card_dict(db, p) for p in projects]}


def _card_dict(db: Session, p: CanvasProject) -> dict:
    """列表卡片数据：基础字段 + 节点数 + 首图缩略图（nodes 已 selectin 预加载，无 N+1）。"""
    d = p.to_dict()
    d["node_count"] = len(p.nodes)
    thumb = None
    for n in sorted(p.nodes, key=lambda n: n.id):
        if n.type != "image":
            continue
        try:
            meta = json.loads(n.metadata_json or "{}")
        except json.JSONDecodeError:
            continue
        url = meta.get("outputUrl") if isinstance(meta, dict) else None
        if url:
            thumb = url
            break
    d["thumb_url"] = thumb
    return d


@router.get("/projects/trash")
def list_trash(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """回收站：已软删除的画布（可恢复/彻底删除）。"""
    projects = (
        db.query(CanvasProject)
        .filter(CanvasProject.user_id == user.id, CanvasProject.deleted_at.isnot(None))
        .order_by(CanvasProject.deleted_at.desc())
        .all()
    )
    return {"projects": [p.to_dict() for p in projects]}


@router.post("/projects/{project_id}/restore")
def restore_project(
    project_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    project = _own_trashed_or_404(db, project_id, user)
    project.deleted_at = None
    db.commit()
    return {"project": project.to_dict()}


@router.delete("/projects/{project_id}/purge", status_code=204)
def purge_project(
    project_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> None:
    """彻底删除（仅限回收站中的画布；级联删节点/连线/快照）。"""
    project = _own_trashed_or_404(db, project_id, user)
    db.delete(project)
    db.commit()




# ---------- 只读分享 + fork（P0）----------


def _public_share_or_404(db: Session, token: str) -> CanvasProject:
    """公开只读：token 有效且画布未进回收站。"""
    project = (
        db.query(CanvasProject)
        .filter(CanvasProject.share_token == token, CanvasProject.deleted_at.is_(None))
        .first()
    )
    if project is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "SHARE_NOT_FOUND", "message": "分享链接不存在或已关闭"},
        )
    return project


@router.post("/projects/{project_id}/share")
def enable_share(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """开启只读分享：生成随机 token（旧 token 若存在则作废换新）。"""
    import secrets

    project = _own_project_or_404(db, project_id, user)
    project.share_token = secrets.token_urlsafe(9)  # 12 字符 URL 安全
    db.commit()
    return {"share_token": project.share_token}


@router.delete("/projects/{project_id}/share")
def disable_share(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """关闭分享：链接立即失效。"""
    project = _own_project_or_404(db, project_id, user)
    project.share_token = None
    db.commit()
    return {"shared": False}


@router.get("/shared/{token}")
def get_shared_canvas(
    token: str,
    db: Session = Depends(get_db),
    request: Request = None,
):
    """公开只读画布（免登录）。按 IP 限流防爬。脱敏：不含 user_id。"""
    from fastapi import Request as _R  # noqa: F401

    from backend.app.services.rate_limiter import RateLimitExceeded, enforce

    try:
        enforce(
            f"share:{request.client.host if request else 'anon'}",
            "view",
            limit=60,
            window_seconds=60,
            message="访问过于频繁",
        )
    except RateLimitExceeded as e:
        raise HTTPException(
            status_code=429, detail={"code": "RATE_LIMITED", "message": str(e)}
        ) from e

    project = _public_share_or_404(db, token)
    author = db.get(User, project.user_id)
    return {
        "project": {
            "id": project.id,
            "title": project.title,
            "updated_at": iso_utc(project.updated_at),
        },
        "author": author.username if author else "匿名",
        # 与 GET /projects/{id} 同形状：前端复用同一 hydrate 逻辑
        "nodes": [n.to_dict() for n in project.nodes],
        "connections": [c.to_dict() for c in project.connections],
    }


@router.post("/shared/{token}/fork", status_code=201)
def fork_shared_canvas(
    token: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """复制公开画布到自己的名下（深拷贝节点/连线，标题加副本后缀）。"""
    project = _public_share_or_404(db, token)
    copy = CanvasProject(
        user_id=user.id,
        title=f"{project.title} - 副本"[:200],
        viewport=project.viewport,
        background_mode=project.background_mode,
    )
    db.add(copy)
    db.flush()  # 拿 copy.id

    old_new: dict[int, int] = {}
    for n in project.nodes:
        nn = CanvasNode(
            project_id=copy.id,
            type=n.type,
            title=n.title,
            position_x=n.position_x,
            position_y=n.position_y,
            width=n.width,
            height=n.height,
            metadata_json=n.metadata_json,
        )
        db.add(nn)
        db.flush()
        old_new[n.id] = nn.id
    for c in project.connections:
        db.add(CanvasConnection(
            project_id=copy.id,
            from_node_id=old_new.get(c.from_node_id, c.from_node_id),
            to_node_id=old_new.get(c.to_node_id, c.to_node_id),
        ))
    db.commit()
    db.refresh(copy)
    return {"project": copy.to_dict()}


@router.post("/projects", status_code=201)
def create_project(
    body: ProjectCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    project = CanvasProject(user_id=user.id, title=body.title)
    db.add(project)
    db.commit()
    db.refresh(project)
    return {"project": project.to_dict()}


@router.get("/projects/{project_id}")
def get_project(
    project_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    project = _own_project_or_404(db, project_id, user)
    data = project.to_dict()
    data["nodes"] = [n.to_dict() for n in project.nodes]
    data["connections"] = [c.to_dict() for c in project.connections]
    return {"project": data}


@router.put("/projects/{project_id}")
def update_project(
    project_id: int,
    body: ProjectUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    project = _own_project_or_404(db, project_id, user)
    if body.title is not None:
        project.title = body.title
    if body.viewport is not None:
        project.viewport = body.viewport
    if body.background_mode is not None:
        project.background_mode = body.background_mode
    db.commit()
    db.refresh(project)
    return {"project": project.to_dict()}


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(
    project_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> None:
    """删除 → 回收站（软删）；已在回收站的再删 = 彻底删除（幂等语义）。"""
    from datetime import datetime

    existing = db.get(CanvasProject, project_id)
    if existing is not None and existing.user_id == user.id:
        if existing.deleted_at is None:
            existing.deleted_at = datetime.utcnow()
            db.commit()
            return
        db.delete(existing)  # 已软删 → purge
        db.commit()
        return
    raise HTTPException(
        status_code=404, detail={"code": "PROJECT_NOT_FOUND", "message": "画布不存在"}
    )


@router.put("/projects/{project_id}/nodes")
def sync_nodes(
    project_id: int,
    body: BulkNodesUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """节点同步：按 metadata.client_id 稳定 upsert（id 不再随保存变化）。

    并发保护（H1）：body.base_revision 与 project.revision 不一致时 409
    REVISION_CONFLICT（携带当前 revision），由客户端合并后重试；
    不带 base_revision（外部工具/旧客户端）跳过校验直接应用。
    删除语义：仅删除带 client_id 的既有节点中不在本次集合者；
    无 client_id 的存量节点（外部写入）保留不删。
    """
    project = _own_project_or_404(db, project_id, user)
    import json as _json

    if body.base_revision is not None and body.base_revision != (project.revision or 0):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "REVISION_CONFLICT",
                "message": "画布已在其他窗口被修改",
                "hint": "已自动合并服务端最新内容后重试",
                "current_revision": project.revision or 0,
            },
        )

    incoming: list[tuple[object, dict]] = []
    for item in body.nodes:
        try:
            parsed = _json.loads(item.metadata_json or "{}")
            if not isinstance(parsed, dict):
                raise ValueError
        except _json.JSONDecodeError as e:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "METADATA_INVALID",
                    "message": f"节点 {item.title or item.type} 的 metadata 不是合法 JSON",
                },
            ) from e
        incoming.append((item, parsed))

    existing = db.query(CanvasNode).filter(CanvasNode.project_id == project.id).all()
    by_cid: dict[str, CanvasNode] = {}
    for n in existing:
        try:
            meta = _json.loads(n.metadata_json or "{}")
            cid = meta.get("client_id") if isinstance(meta, dict) else None
        except _json.JSONDecodeError:
            cid = None
        if cid:
            by_cid[str(cid)] = n

    incoming_cids = {
        str(parsed.get("client_id")) for _, parsed in incoming if parsed.get("client_id")
    }
    if incoming_cids:
        # UI 保存形态：只删带 cid 且不在本次集合的（无 cid 存量=外部写入，保留）
        for cid, node in by_cid.items():
            if cid not in incoming_cids:
                db.delete(node)
    else:
        # 外部直写形态（payload 全无 client_id）：保持旧全量替换语义
        db.query(CanvasNode).filter(CanvasNode.project_id == project.id).delete()

    for item, parsed in incoming:
        cid = parsed.get("client_id")
        target = by_cid.get(str(cid)) if cid else None
        if target is not None:
            target.type = item.type
            target.title = item.title
            target.position_x = item.position_x
            target.position_y = item.position_y
            target.width = item.width
            target.height = item.height
            target.metadata_json = item.metadata_json
        else:
            db.add(
                CanvasNode(
                    project_id=project.id,
                    type=item.type,
                    title=item.title,
                    position_x=item.position_x,
                    position_y=item.position_y,
                    width=item.width,
                    height=item.height,
                    metadata_json=item.metadata_json,
                )
            )
    project.revision = (project.revision or 0) + 1
    db.commit()
    nodes = db.query(CanvasNode).filter(CanvasNode.project_id == project.id).all()
    return {"nodes": [n.to_dict() for n in nodes], "revision": project.revision}


@router.put("/projects/{project_id}/connections")
def sync_connections(
    project_id: int,
    body: BulkConnectionsUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    project = _own_project_or_404(db, project_id, user)
    # 审计批次三（P3）：连线端点必须校验 node 归属（原可写跨项目悬空引用）
    valid_ids = {
        n.id
        for n in db.query(CanvasNode.id).filter(CanvasNode.project_id == project.id).all()
    }
    for item in body.connections:
        if item.from_node_id not in valid_ids or item.to_node_id not in valid_ids:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "NODE_INVALID",
                    "message": "连线引用了不属于该画布的节点",
                },
            )
    db.query(CanvasConnection).filter(CanvasConnection.project_id == project.id).delete()
    for item in body.connections:
        db.add(
            CanvasConnection(
                project_id=project.id,
                from_node_id=item.from_node_id,
                to_node_id=item.to_node_id,
            )
        )
    db.commit()
    conns = db.query(CanvasConnection).filter(CanvasConnection.project_id == project.id).all()
    return {"connections": [c.to_dict() for c in conns], "revision": project.revision or 0}


# ---------- 画布级 Agent API（P2 战略项）----------


class AgentCreateRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000, description="创作指令（即提示词）")
    type: str = Field("image", description='任务域: "image" | "video" | "llm" | "audio"')
    model: str | None = Field(None, description="缺省=系统默认模型")
    negative_prompt: str = ""
    channel_id: int | None = Field(None, description="指定渠道（同名模型多渠道时选择）")
    params: dict = Field(
        default_factory=dict, description="可选：duration/resolution/reference_urls 等"
    )


@router.post("/projects/{project_id}/agent", status_code=202)
async def agent_create(
    project_id: int,
    body: AgentCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    """外部 Agent 一句话创作入口（X-API-Key 认证，旧路径保留兼容）。

    新接入请用 /api/agent/v1/generate（同逻辑，见 backend/app/routers/agent.py）。
    """
    from backend.app.routers.agent import require_agent_key
    from backend.app.services.rate_limiter import RateLimitExceeded, enforce

    user = require_agent_key(request, db)
    try:
        enforce(
            f"agent:{user.id}",
            "agent",
            limit=10,
            window_seconds=60,
            message="Agent 提交过于频繁，每分钟最多 10 次",
        )
    except RateLimitExceeded as e:
        raise HTTPException(
            status_code=429, detail={"code": "RATE_LIMITED", "message": str(e)}
        ) from e
    return await _agent_generate_core(db, user, project_id, body)


async def _agent_generate_core(
    db: Session, user, project_id: int, body, agent_run_id: int | None = None
) -> dict:
    """Agent 生成核心（新旧端点共用）：建画布节点 → 入队生成 → 返回 task_id。

    服务端闭环：worker 完成回写节点 outputUrl（前端刷新画布可见）。
    agent_run_id：首页自由创作 Agent 专用——写入 task params 供终态钩子回链、
    节点 metadata.source 标记归属（自删守卫依据）。
    """
    from backend.app.services.param_schema import merge_model_param_defaults
    from backend.app.services.task_queue import QueueUnavailableError, enqueue_generation_task
    from backend.app.services.task_store import TaskValidationError, create_task

    project = _own_project_or_404(db, project_id, user)

    # 默认模型：系统 default_models，缺省回落该域第一个启用渠道
    model = body.model or _default_model_for(db, body.type)

    import json as _json
    import uuid as _uuid

    # 制片清单闸（2026-10-26）：strict 项目未 ready 时禁入视频工作流
    # 探针豁免: message 以 [probe] 开头的任务不受闸约束 (探针是 ready 前置动作)
    if body.type == "video" and not body.message.strip().startswith("[probe]"):
        from backend.app.routers.manifest import check_gate

        check_gate(db, project_id)

    # 视频串行硬约束（2026-10-08）：进行中视频 ≥1 时拒绝——0 秒反馈取代闸门
    # 58 分钟排队超时失败；错误文案教育 agent 串行（Skill v2.1 纪律的入口兜底）
    if body.type == "video":
        from backend.app.models import Task as _T

        busy = (
            db.query(_T.id)
            .filter(_T.type == "video", _T.status.in_(["queued", "generating"]))
            .count()
        )
        if busy >= 1:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "AGENT_VIDEO_SERIAL",
                    "message": (
                        "已有进行中的视频任务，请等它完成（GET /api/agent/v1/tasks/{id} 轮询至 "
                        "completed）后再提交下一个——视频生成挤兑 GPU 会导致全部超时失败"
                    ),
                },
            )

    # v1.4 定点投放：显式 x/y 用于节点位置（不进 task params）
    explicit_params = dict(body.params or {})
    px = explicit_params.pop("x", None)
    py = explicit_params.pop("y", None)

    node_cid = f"agent-{_uuid.uuid4().hex[:10]}"
    node = CanvasNode(
        project_id=project.id,
        type=body.type if body.type in {"text", "image", "video", "audio"} else "image",
        title=body.message.strip()[:30],
        position_x=px if px is not None else _agent_slot_x(db, project.id),
        position_y=py if py is not None else _agent_slot_y(db, project.id),
        metadata_json=_json.dumps({
            "client_id": node_cid,
            "prompt": body.message.strip(),
            "canvas_agent": True,
            **({"source": f"agent_run:{agent_run_id}"} if agent_run_id else {}),
        }, ensure_ascii=False),
    )
    db.add(node)
    db.commit()

    explicit = explicit_params
    if getattr(body, "channel_id", None) is not None:
        explicit["channel_id"] = body.channel_id  # 一等字段指定渠道（同名多模型路由依据）
    explicit["canvas_target"] = {"project_id": project.id, "node_client_id": node_cid}
    if agent_run_id is not None:
        explicit["agent_run_id"] = agent_run_id  # 终态钩子回链（task→run）
    try:
        task = create_task(
            db,
            user_id=user.id,
            type_=body.type,
            prompt=body.message.strip(),
            model=model,
            params=merge_model_param_defaults(db, body.type, model, explicit),
            negative_prompt=body.negative_prompt,
        )
    except TaskValidationError as e:
        db.delete(node)
        db.commit()
        raise HTTPException(status_code=422, detail={"code": e.code, "message": e.message}) from e

    try:
        await enqueue_generation_task(task.id, "video" if task.type == "video" else "generate")
    except (QueueUnavailableError, ConnectionError, OSError) as e:
        # 审计批次二（P2）：入队失败必须回收节点，不得遗留孤儿
        db.delete(node)
        db.commit()
        raise HTTPException(
            status_code=503,
            detail={"code": "QUEUE_UNAVAILABLE", "message": f"任务队列不可用: {e}"},
        ) from e

    return {
        "task_id": task.id,
        "node_client_id": node_cid,
        "model": model,
        "status": "queued",
    }


def _default_model_for(db: Session, domain: str) -> str:
    """系统默认模型 → 该域第一个启用渠道的第一个模型 → 兜底占位（提交校验会拦）。"""
    import json as _json

    from backend.app.models import CapabilityConfig, ProviderChannel, ProviderModel

    row = db.get(CapabilityConfig, "default_models")
    if row:
        try:
            defaults = _json.loads(str(row.value_json or "{}"))
            m = defaults.get(domain)
            # 两种格式兼容：字符串 / settings 端点写入的 {channel_id, model} dict
            if isinstance(m, str) and m:
                return m
            if isinstance(m, dict) and isinstance(m.get("model"), str) and m["model"]:
                return m["model"]
        except ValueError:
            pass
    ch = (
        db.query(ProviderChannel)
        .filter(ProviderChannel.enabled == 1, ProviderChannel.domain == domain)
        .order_by(ProviderChannel.sort_order)
        .first()
    )
    if ch:
        m = (
            db.query(ProviderModel)
            .filter(
                ProviderModel.channel_id == ch.id,
                ProviderModel.display_name.isnot(None),
            )
            .order_by(ProviderModel.display_name)
            .first()
        )
        if m:
            return m.model_id
    return f"{domain}-default"


def _agent_slot_x(db: Session, project_id: int) -> float:
    from backend.app.models import CanvasNode

    last = (
        db.query(CanvasNode)
        .filter(CanvasNode.project_id == project_id)
        .order_by(CanvasNode.position_x.desc())
        .first()
    )
    return (last.position_x + 340) if last else 0.0


def _agent_slot_y(db: Session, project_id: int) -> float:
    from backend.app.models import CanvasNode

    n = db.query(CanvasNode).filter(CanvasNode.project_id == project_id).count()
    return float((n % 5) * 260)
