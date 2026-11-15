"""Agent API v1（/api/agent/v1 · X-API-Key 鉴权）：外部 Agent 的最小可用闭环。







设计原则：



- 只读面（me/projects/models/tasks）+ 一个生成入口（generate）



- key 泄漏防线沿用审计批次二：生成限流 10 次/分钟前置



- 任务归属严格隔离（他人 key 不可见）



- 旧端点 POST /api/canvas/projects/{id}/agent 保留兼容（同逻辑）



"""







from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.core.db import get_db
from backend.app.models import Task, User
from backend.app.routers.settings import get_user_by_api_key

router = APIRouter(prefix="/api/agent/v1", tags=["agent-api"])







RATE_LIMIT_PER_MIN = 10











def require_agent_key(request: Request, db: Session = Depends(get_db)) -> User:



    """X-API-Key → User（Agent API 统一鉴权依赖）。"""



    key = request.headers.get("X-API-Key", "")



    user = get_user_by_api_key(db, key)



    if user is None:



        raise HTTPException(



            status_code=401,



            detail={"code": "API_KEY_INVALID", "message": "API Key 无效或已撤销"},



        )



    return user











def _enforce_rate(user: User) -> None:



    from backend.app.services.rate_limiter import RateLimitExceeded, enforce







    try:



        enforce(



            f"agent:{user.id}",



            "agent",



            limit=RATE_LIMIT_PER_MIN,



            window_seconds=60,



            message=f"Agent 提交过于频繁，每分钟最多 {RATE_LIMIT_PER_MIN} 次",



        )



    except RateLimitExceeded as e:



        raise HTTPException(



            status_code=429, detail={"code": "RATE_LIMITED", "message": str(e)}



        ) from e











@router.get("/me")



def agent_me(user: User = Depends(require_agent_key)):



    """key 校验 + 基本信息（Agent 接入自检第一步）。"""



    return {



        "username": user.username,



        "agent_api": True,



        "rate_limit": f"{RATE_LIMIT_PER_MIN}/min",



    }











@router.get("/projects")



def list_projects(db: Session = Depends(get_db), user: User = Depends(require_agent_key)):



    """列出自己的画布项目（Agent 选择落画布的目标）。"""



    from backend.app.models import CanvasProject







    rows = (



        db.query(CanvasProject)



        .filter(CanvasProject.user_id == user.id, CanvasProject.deleted_at.is_(None))



        .order_by(CanvasProject.updated_at.desc())



        .limit(50)



        .all()



    )



    return {



        "projects": [



            {"id": p.id, "title": p.title, "updated_at": str(p.updated_at or "")}



            for p in rows



        ]



    }











class ProjectCreate(BaseModel):



    title: str = Field("Agent 画布", max_length=100)











@router.post("/projects", status_code=201)



def create_project(



    body: ProjectCreate,



    db: Session = Depends(get_db),



    user: User = Depends(require_agent_key),



):



    """创建画布项目（Agent 自建工作区）。"""



    from backend.app.models import CanvasProject







    p = CanvasProject(user_id=user.id, title=body.title.strip() or "Agent 画布")



    db.add(p)



    db.commit()



    return {"id": p.id, "title": p.title}











@router.get("/models")



def list_models(



    domain: str = "",



    db: Session = Depends(get_db),



    user: User = Depends(require_agent_key),



):



    """列可用模型（个人+系统渠道，含域/免费标记）——Agent 选择模型的依据。"""



    from sqlalchemy import or_

    from backend.app.models import ProviderChannel, ProviderModel







    q = (



        db.query(ProviderModel, ProviderChannel)



        .join(ProviderChannel, ProviderModel.channel_id == ProviderChannel.id)



        .filter(



            ProviderModel.enabled == 1,



            ProviderChannel.enabled == 1,



            or_(ProviderChannel.user_id == user.id, ProviderChannel.user_id.is_(None)),



        )



    )



    if domain:



        q = q.filter(ProviderChannel.domain == domain)



    rows = q.order_by(ProviderChannel.domain, ProviderModel.model_id).limit(200).all()



    # 系统默认模型（admin 在 Web 端设置）——agent 选型的最高优先依据



    defaults = _system_defaults(db)







    def _ref_cap(m: ProviderModel) -> dict:



        """参考图能力（provider_models.meta_json，P2 元数据）：supports_ref/max_refs。"""



        try:



            meta = json.loads(m.meta_json or "{}")



        except (ValueError, TypeError):



            meta = {}



        if not isinstance(meta, dict) or not meta.get("supports_ref"):



            return {"supports_ref": False, "max_refs": 0}



        try:



            cap = max(1, min(12, int(meta.get("max_refs") or 1)))



        except (TypeError, ValueError):



            cap = 1



        return {"supports_ref": True, "max_refs": cap}







    out = []



    for m, ch in rows:



        item = {



            "model_id": m.model_id,



            "channel_id": ch.id,



            "domain": ch.domain,



            "channel": ch.name,



            "display_name": m.display_name or m.model_id,



            "is_free": bool(m.is_free),



            "is_default": bool(defaults.get(ch.domain, {}).get("model") == m.model_id),



            "pricing_unit": m.pricing_unit,



            "price_input": m.price_input,



        }



        item.update(_ref_cap(m))



        out.append(item)



    return {"defaults": defaults, "models": out}











def _system_defaults(db: Session) -> dict:



    """CapabilityConfig default_models（兼容字符串/{channel_id,model} 两种格式）。"""



    import json as _json

    from backend.app.models import CapabilityConfig







    row = db.get(CapabilityConfig, "default_models")



    if not row:



        return {}



    try:



        raw = _json.loads(str(row.value_json or "{}"))



    except (ValueError, TypeError):



        return {}



    out: dict[str, dict[str, str | int | None]] = {}



    for dom, v in (raw or {}).items():



        if isinstance(v, str) and v:



            out[dom] = {"model": v}



        elif isinstance(v, dict) and isinstance(v.get("model"), str) and v["model"]:



            out[dom] = {"model": v["model"], "channel_id": v.get("channel_id")}



    return out











class AgentGenerateRequest(BaseModel):



    project_id: int



    message: str = Field(..., min_length=1, max_length=4000)



    type: str = Field("image", description='任务域: "image" | "video" | "llm" | "audio"')



    model: str | None = Field(None, description="缺省=系统默认模型")



    negative_prompt: str = ""



    channel_id: int | None = Field(



        None, description="指定渠道（同名模型多渠道时选择；从 /models 的 channel_id 取）"



    )



    params: dict = Field(



        default_factory=dict, description="可选：duration/resolution/reference_urls 等"



    )











@router.post("/generate", status_code=202)



async def agent_generate(



    body: AgentGenerateRequest,



    db: Session = Depends(get_db),



    user: User = Depends(require_agent_key),



):



    """生成入口：建画布节点 → 入队生成 → 返回 task_id 供轮询。







    与旧端点 POST /api/canvas/projects/{id}/agent 同逻辑（服务端闭环回写节点）。



    """



    from backend.app.routers.canvas import _agent_generate_core







    return await _agent_generate_core(db, user, body.project_id, body)











class AgentNodeCreate(BaseModel):



    type: str = Field("text", description='节点类型: "text" | "image" | "video" | "audio"')



    title: str = Field("", max_length=200)



    prompt: str = Field("", max_length=8000, description="节点提示词/文本内容")



    x: float = 0



    y: float = 0











class AgentNodePatch(BaseModel):



    title: str | None = Field(None, max_length=200)



    prompt: str | None = Field(None, max_length=8000)



    x: float | None = None



    y: float | None = None











class AgentEdgeCreate(BaseModel):



    from_node_id: int



    to_node_id: int











class AgentFrameRequest(BaseModel):



    url: str = Field(..., max_length=2000, description="站内 /outputs/ 视频路径")



    time: float = Field(-1, description="秒；-1 = 末帧（尾帧接力）")











def _own_project(db: Session, user: User, project_id: int):



    from backend.app.models import CanvasProject







    p = (



        db.query(CanvasProject)



        .filter(CanvasProject.id == project_id, CanvasProject.user_id == user.id)



        .first()



    )



    if p is None:



        raise HTTPException(



            status_code=404, detail={"code": "PROJECT_NOT_FOUND", "message": "项目不存在"}



        )



    return p











def _canvas_rate(user: User) -> None:



    """画布写操作限流（组织节点/连线是轻操作，放宽到 30/min）。"""



    from backend.app.services.rate_limiter import RateLimitExceeded, enforce







    try:



        enforce(



            f"agent-canvas:{user.id}", "agent", limit=30, window_seconds=60,



            message="画布操作过于频繁，每分钟最多 30 次",



        )



    except RateLimitExceeded as e:



        raise HTTPException(



            status_code=429, detail={"code": "RATE_LIMITED", "message": str(e)}



        ) from e











@router.post("/projects/{project_id}/nodes", status_code=201)



def agent_create_node(



    project_id: int,



    body: AgentNodeCreate,



    db: Session = Depends(get_db),



    user: User = Depends(require_agent_key),



):



    """建节点（定点）：串联创作的画布组织——文字分镜卡/占位/构图草稿。"""



    import json as _json
    import uuid as _uuid

    from backend.app.models import CanvasNode







    _canvas_rate(user)



    project = _own_project(db, user, project_id)



    cid = f"agent-{_uuid.uuid4().hex[:10]}"



    node = CanvasNode(



        project_id=project.id,



        type=body.type if body.type in {"text", "image", "video", "audio"} else "text",



        title=body.title.strip()[:200] or (body.prompt.strip()[:30] or "Agent 节点"),



        position_x=body.x,



        position_y=body.y,



        metadata_json=_json.dumps(



            {"client_id": cid, "prompt": body.prompt, "canvas_agent": True},



            ensure_ascii=False,



        ),



    )



    db.add(node)



    db.commit()



    return {"id": node.id, "client_id": cid, "x": body.x, "y": body.y}











@router.patch("/projects/{project_id}/nodes/{node_id}")



def agent_patch_node(



    project_id: int,



    node_id: int,



    body: AgentNodePatch,



    db: Session = Depends(get_db),



    user: User = Depends(require_agent_key),



):



    """改节点（标题/提示词/位置）。"""



    import json as _json

    from backend.app.models import CanvasNode







    _canvas_rate(user)



    _own_project(db, user, project_id)



    node = (



        db.query(CanvasNode)



        .filter(CanvasNode.project_id == project_id, CanvasNode.id == node_id)



        .first()



    )



    if node is None:



        raise HTTPException(



            status_code=404, detail={"code": "NODE_NOT_FOUND", "message": "节点不存在"}



        )



    if body.title is not None:



        node.title = body.title.strip()[:200]



    if body.x is not None:



        node.position_x = body.x



    if body.y is not None:



        node.position_y = body.y



    if body.prompt is not None:



        try:



            meta = _json.loads(str(node.metadata_json or "{}"))



        except (ValueError, TypeError):



            meta = {}



        meta["prompt"] = body.prompt



        node.metadata_json = _json.dumps(meta, ensure_ascii=False)



    db.commit()



    return {"id": node.id, "ok": True}











@router.delete("/projects/{project_id}/nodes/{node_id}", status_code=200)



def agent_delete_node(



    project_id: int,



    node_id: int,



    db: Session = Depends(get_db),



    user: User = Depends(require_agent_key),



):


    

    """删节点（连带清理其连线，避免悬空边）。"""



    from backend.app.models import CanvasConnection, CanvasNode







    _canvas_rate(user)



    _own_project(db, user, project_id)



    node = (



        db.query(CanvasNode)



        .filter(CanvasNode.project_id == project_id, CanvasNode.id == node_id)



        .first()



    )



    if node is None:



        raise HTTPException(



            status_code=404, detail={"code": "NODE_NOT_FOUND", "message": "节点不存在"}



        )



    db.query(CanvasConnection).filter(



        (CanvasConnection.from_node_id == node_id) | (CanvasConnection.to_node_id == node_id)



    ).delete(synchronize_session=False)



    db.delete(node)



    db.commit()



    return {"id": node_id, "deleted": True}











@router.post("/projects/{project_id}/edges", status_code=201)



def agent_create_edge(



    project_id: int,



    body: AgentEdgeCreate,



    db: Session = Depends(get_db),



    user: User = Depends(require_agent_key),



):



    """连线（图→视频=首帧参考；图→图=参考图）——把创作脉络画在画布上。"""



    from backend.app.models import CanvasConnection, CanvasNode







    _canvas_rate(user)



    _own_project(db, user, project_id)



    ids = {



        n.id



        for n in db.query(CanvasNode.id).filter(CanvasNode.project_id == project_id).all()



    }



    if body.from_node_id not in ids or body.to_node_id not in ids:



        raise HTTPException(



            status_code=422,



            detail={"code": "NODE_INVALID", "message": "连线引用了不属于该画布的节点"},



        )



    dup = (



        db.query(CanvasConnection)



        .filter(



            CanvasConnection.project_id == project_id,



            CanvasConnection.from_node_id == body.from_node_id,



            CanvasConnection.to_node_id == body.to_node_id,



        )



        .first()



    )



    if dup is None:



        db.add(



            CanvasConnection(



                project_id=project_id,



                from_node_id=body.from_node_id,



                to_node_id=body.to_node_id,



            )



        )



        db.commit()



    return {"from_node_id": body.from_node_id, "to_node_id": body.to_node_id, "ok": True}











@router.post("/frame")



def agent_extract_frame(



    body: AgentFrameRequest,



    db: Session = Depends(get_db),  # noqa: ARG001



    user: User = Depends(require_agent_key),



):



    """取帧（尾帧接力的地基）：time=-1 取末帧 → 站内路径可直接作 reference_urls。"""



    from backend.app.services import media_service
    from backend.app.services.media_service import MediaError







    if not body.url.startswith("/outputs/") or ".." in body.url:



        raise HTTPException(



            status_code=422,



            detail={"code": "MEDIA_URL_INVALID", "message": "仅支持站内 /outputs/ 视频路径"},



        )



    try:



        out = (



            media_service.extract_last_frame(media_service.ensure_local(body.url))



            if body.time < 0



            else media_service.extract_frame(media_service.ensure_local(body.url), body.time)



        )



    except MediaError as e:



        raise HTTPException(



            status_code=422, detail={"code": "FRAME_FAILED", "message": str(e)}



        ) from e



    return {"url": f"/outputs/{out.relative_to(media_service._outputs_root_default()).as_posix()}"}











class AgentChannelCreate(BaseModel):



    domain: str = Field(..., description='渠道域: "image" | "video" | "llm" | "audio"')



    name: str = Field(..., min_length=1, max_length=100)



    base_url: str = Field(..., min_length=1, max_length=500)



    key: str = Field(



        "", max_length=500, description="渠道 API Key（服务端加密存储，响应仅掩码）"



    )



    models: list[dict] = Field(



        default_factory=list, description="模型目录 [{model_id, is_free?, pricing_unit?, ...}]"



    )











@router.get("/channels")



def agent_channels(



    db: Session = Depends(get_db),



    user: User = Depends(require_agent_key),



):



    """列自己（含系统）渠道——掩码 key + 模型目录。"""



    from sqlalchemy import or_

    from backend.app.models import ProviderChannel







    rows = (



        db.query(ProviderChannel)



        .filter(or_(ProviderChannel.user_id == user.id, ProviderChannel.user_id.is_(None)))



        .order_by(ProviderChannel.domain, ProviderChannel.id)



        .all()



    )



    from backend.app.routers.providers import _to_response







    return {"channels": [_to_response(ch).model_dump() for ch in rows]}











@router.post("/channels", status_code=201)



def agent_create_channel(



    body: AgentChannelCreate,



    db: Session = Depends(get_db),



    user: User = Depends(require_agent_key),



):



    """Agent 代用户接入渠道+模型（v1.3）：key 服务端加密；计价校验复用。







    注意：系统渠道（全员共享）仍只能 Web 端管理员配置——Agent 建的是用户私有渠道。



    """



    from backend.app.services import provider_service
    from backend.app.services.rate_limiter import RateLimitExceeded, enforce







    try:



        enforce(



            f"agent-ch:{user.id}", "agent", limit=5, window_seconds=60,



            message="渠道接入过于频繁，每分钟最多 5 次",



        )



    except RateLimitExceeded as e:



        raise HTTPException(



            status_code=429, detail={"code": "RATE_LIMITED", "message": str(e)}



        ) from e



    try:



        ch = provider_service.create_channel(



            db,



            user_id=int(user.id),



            domain=body.domain,



            name=body.name,



            base_url=body.base_url,



            key_plain=body.key,



            models=body.models or None,



        )



    except provider_service.ChannelError as e:



        raise HTTPException(status_code=422, detail={"code": e.code, "message": e.message}) from e



    from backend.app.routers.providers import _to_response







    return _to_response(ch).model_dump()











@router.get("/skill.md")



def download_skill(request: Request):



    """Skill 文件经 Web 路径下载（无需 key——agent 引导期只有基址也能自举）。







    规范源：backend/app/skills/creagen-canvas/SKILL.md（随代码部署）；



    仓库根 skills/ 目录是同内容副本（供 cp 安装）。



    """



    from pathlib import Path

    from fastapi.responses import Response







    path = Path(__file__).resolve().parents[1] / "skills" / "creagen-canvas" / "SKILL.md"



    if not path.is_file():



        raise HTTPException(



            status_code=404, detail={"code": "SKILL_NOT_FOUND", "message": "Skill 文件缺失"}



        )



    content = path.read_text(encoding="utf-8")



    import hashlib







    etag = '"' + hashlib.md5(content.encode("utf-8")).hexdigest() + '"'



    if request.headers.get("if-none-match") == etag:



        return Response(status_code=304, headers={"ETag": etag})



    return Response(



        content=content,



        media_type="text/markdown; charset=utf-8",



        headers={



            "Content-Disposition": 'attachment; filename="creagen-canvas-SKILL.md"',



            "ETag": etag,



            "Cache-Control": "no-cache",



        },



    )











def _task_dict(t: Task) -> dict:



    """Agent 视角的任务序列化：含 LLM content（文本结果直读，output_url 塞文本是历史包袱）。"""



    target = (t.params_dict().get("canvas_target") or {})



    return {



        "id": t.id,



        "status": t.status,



        "type": t.type,



        "model": t.model,



        "output_url": t.output_url,



        "content": t.output_url if (t.type == "llm" and t.status == "completed") else None,



        "error": t.error_msg,



        "node_client_id": target.get("node_client_id"),



        "created_at": str(t.created_at or ""),



        "completed_at": str(t.completed_at or ""),



    }











@router.get("/tasks")



def agent_tasks(



    limit: int = 20,



    status: str = "",



    db: Session = Depends(get_db),



    user: User = Depends(require_agent_key),



):



    """Agent 记忆①：最近任务列表（倒序；跨会话恢复/接力创作的依据）。"""



    q = db.query(Task).filter(Task.user_id == user.id)



    if status:



        q = q.filter(Task.status == status)



    rows = q.order_by(Task.id.desc()).limit(max(1, min(limit, 100))).all()



    return {"tasks": [_task_dict(t) for t in rows]}











@router.get("/tasks/{task_id}")



def agent_task(



    task_id: int,



    db: Session = Depends(get_db),



    user: User = Depends(require_agent_key),



):



    """任务状态轮询：queued/generating/completed/failed + 产物 URL（LLM 看 content）。"""



    t = db.get(Task, task_id)



    if t is None or t.user_id != user.id:



        raise HTTPException(



            status_code=404, detail={"code": "TASK_NOT_FOUND", "message": "任务不存在"}



        )



    return _task_dict(t)











@router.get("/projects/{project_id}/nodes")



def agent_project_nodes(



    project_id: int,



    db: Session = Depends(get_db),



    user: User = Depends(require_agent_key),



):



    """Agent 记忆②：画布节点与产物（读回接力素材：outputUrl/prompt/canvas_agent）。"""



    import json as _json

    from backend.app.models import CanvasNode, CanvasProject







    project = (



        db.query(CanvasProject)



        .filter(CanvasProject.id == project_id, CanvasProject.user_id == user.id)



        .first()



    )



    if project is None:



        raise HTTPException(



            status_code=404, detail={"code": "PROJECT_NOT_FOUND", "message": "项目不存在"}



        )



    from backend.app.models import CanvasConnection







    nodes = (



        db.query(CanvasNode)



        .filter(CanvasNode.project_id == project.id)



        .order_by(CanvasNode.id)



        .all()



    )



    edges = (



        db.query(CanvasConnection)



        .filter(CanvasConnection.project_id == project.id)



        .all()



    )



    out = []



    for n in nodes:



        try:



            meta = _json.loads(str(n.metadata_json or "{}"))



        except (ValueError, TypeError):



            meta = {}



        out.append(



            {



                "id": n.id,



                "type": n.type,



                "title": n.title,



                "client_id": meta.get("client_id"),



                "prompt": meta.get("prompt"),



                "output_url": meta.get("outputUrl"),



                "canvas_agent": bool(meta.get("canvas_agent")),



                "x": n.position_x,



                "y": n.position_y,



            }



        )



    return {



        "nodes": out,



        "edges": [



            {"from_node_id": e.from_node_id, "to_node_id": e.to_node_id} for e in edges



        ],



    }











# ---------- v1.5：合成 + 角色库（与自由创作脑工具能力对齐） ----------











@router.post("/compose", status_code=200)



def agent_compose(



    body: dict,



    db: Session = Depends(get_db),



    user: User = Depends(require_agent_key),



):



    """多段视频按序拼接成片（归一分辨率；可选 BGM 25% 混音）——多场成片交付。"""



    from backend.app.services import media_service
    from backend.app.services.media_service import MediaError







    _canvas_rate(user)



    urls = body.get("urls")



    if not isinstance(urls, list) or len(urls) < 2:



        raise HTTPException(



            status_code=422,



            detail={



                "code": "URLS_INVALID",



                "message": "urls 必须是 ≥2 个站内 /outputs/ 视频路径数组",



            },



        )



    for u in urls:



        if not str(u).startswith("/outputs/") or ".." in str(u):



            raise HTTPException(



                status_code=422,



                detail={"code": "MEDIA_URL_INVALID", "message": "仅支持站内 /outputs/ 路径"},



            )



    bgm = body.get("bgm_url")



    if bgm and (not str(bgm).startswith("/outputs/") or ".." in str(bgm)):



        raise HTTPException(



            status_code=422,



            detail={"code": "BGM_URL_INVALID", "message": "bgm_url 仅支持站内 /outputs/ 路径"},



        )



    transition = body.get("transition", "none")



    from backend.app.services.media_service import COMPOSE_TRANSITIONS



    if transition not in COMPOSE_TRANSITIONS:



        raise HTTPException(



            status_code=422,



            detail={"code": "TRANSITION_INVALID",

                 "message": f"transition 仅支持 {COMPOSE_TRANSITIONS}"},



        )



    try:



        out = media_service.compose_videos(
            [str(u) for u in urls], str(bgm) if bgm else None, transition=transition,
        )



    except MediaError as e:



        raise HTTPException(status_code=422, detail={"code": e.code, "message": e.message}) from e



    root = media_service._outputs_root_default()



    return {"url": f"/outputs/{out.relative_to(root).as_posix()}"}











@router.get("/characters")



def agent_characters(



    db: Session = Depends(get_db),



    user: User = Depends(require_agent_key),



):



    """用户角色库（跨场一致性引用——外观锚定描述 + 定妆照）。"""



    from backend.app.models import CharacterAsset







    chars = (



        db.query(CharacterAsset)



        .filter(CharacterAsset.user_id == user.id)



        .order_by(CharacterAsset.id)



        .all()



    )



    return {



        "characters": [



            {



                "id": c.id,



                "name": c.name,



                "description": c.description,



                "ref_image_url": c.ref_image_url,



            }



            for c in chars



        ]



    }
