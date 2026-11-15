"""任务路由：提交（含渠道检查与入队）/ 列表 / 详情 / 取取消 / SSE 事件流。"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.core.config import get_settings
from backend.app.core.db import get_db
from backend.app.models import User
from backend.app.providers import ProviderError, registry
from backend.app.routers.auth import get_current_user
from backend.app.services.param_schema import merge_model_param_defaults
from backend.app.services.rate_limiter import RateLimitExceeded
from backend.app.services.task_queue import (
    QueueUnavailableError,
    enqueue_generation_task,
    subscribe_task_events,
)
from backend.app.services.task_store import (
    TaskValidationError,
    create_task,
    get_task_or_none,
    list_user_tasks,
    mark_cancel_requested,
)

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


class TaskCreateRequest(BaseModel):
    type: str = Field(..., description='任务域: "image" | "video" | "llm" | "audio"')
    prompt: str = Field(..., min_length=1, max_length=4000, description="生成提示词（≤4000 字）")
    model: str = Field(..., min_length=1)
    negative_prompt: str = ""
    params: dict[str, object] | None = None
    channel_id: int | None = None
    prompt_id: int | None = Field(None, description="关联提示词库条目：完成后自动绑定效果图")


@router.post("")
async def submit_task(
    body: TaskCreateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """提交生成任务：渠道检查 → 入队 → 返回任务快照。"""
    from backend.app.services.rate_limiter import enforce

    try:
        enforce(
            f"user:{user.id}",
            "task_submit",
            limit=30,
            window_seconds=60,
            message="提交过于频繁，每分钟最多 30 次",
        )
    except RateLimitExceeded as e:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "RATE_LIMITED",
                "message": str(e),
                "hint": f"请 {e.wait_seconds}s 后重试",
            },
        ) from e

    settings = get_settings()
    from backend.app.core.errors import ProviderNotConfiguredError
    from backend.app.services.llm_budget import BudgetExceeded, budget_check
    from backend.app.services.provider_service import resolve_provider_for_task as _resolve

    if body.type == "llm":
        try:
            budget_check(db, f"llm:{user.id}")
        except BudgetExceeded as e:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "BUDGET_EXCEEDED",
                    "message": str(e),
                    "hint": "当日 LLM 预算已耗尽，明天再试或联系管理员调整",
                },
            ) from e

    try:
        provider, _channel = _resolve(db, user.id, body.type, body.model)
    except ProviderNotConfiguredError as e:
        if settings.enable_fake_provider:
            provider = registry.resolve(body.type, body.model, None, allow_fake=True)
        else:
            raise HTTPException(
                status_code=422,
                detail={"code": e.code, "message": e.message, "hint": e.hint},
            ) from e
    except ProviderError as e:
        status = 422 if e.code in {"PROVIDER_NOT_CONFIGURED", "TASK_TYPE_INVALID"} else 400
        raise HTTPException(
            status_code=status, detail={"code": e.code, "message": e.message, "hint": e.hint}
        ) from e

    params_with_channel = merge_model_param_defaults(db, body.type, body.model, body.params)
    params_with_channel = dict(params_with_channel)
    if body.channel_id:
        params_with_channel["channel_id"] = body.channel_id
    if body.prompt_id:
        params_with_channel["prompt_id"] = body.prompt_id
    try:
        task = create_task(
            db,
            user_id=user.id,
            type_=body.type,
            prompt=body.prompt,
            model=body.model,
            params=params_with_channel,
            negative_prompt=body.negative_prompt,
        )
    except TaskValidationError as e:
        raise HTTPException(status_code=422, detail={"code": e.code, "message": e.message}) from e

    try:
        queue_name = "video" if task.type == "video" else "generate"  # P2-14: 视频长任务独立队列
        if task.queue_name != queue_name:
            task.queue_name = queue_name
            db.commit()
        await enqueue_generation_task(task.id, queue_name)
    except (QueueUnavailableError, ConnectionError, OSError) as e:
        task.status = "failed"
        task.error_msg = "任务队列不可用，请稍后重试"
        db.commit()
        raise HTTPException(
            status_code=503, detail={"code": "QUEUE_UNAVAILABLE", "message": f"任务队列不可用: {e}"}
        ) from e

    return {"task": task.to_dict(), "provider": type(provider).__name__}


@router.get("")
def list_tasks(
    status: str | None = None,
    type: str | None = Query(None, alias="type"),  # noqa: A002
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    tasks = list_user_tasks(db, user.id, status=status, type_=type, limit=limit, offset=offset)
    # 审计批次三（P3）：total 是过滤后全量计数（原 len(当前页) 恒等于 limit）
    from sqlalchemy import func as _func

    from backend.app.models import Task as _Task

    q = db.query(_func.count(_Task.id)).filter(_Task.user_id == user.id)
    if status:
        q = q.filter(_Task.status == status)
    if type:
        q = q.filter(_Task.type == type)
    total = q.scalar() or 0
    return {"tasks": [t.to_dict(compact=True) for t in tasks], "total": total}


@router.get("/stream")
async def stream_task_events(
    task_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> StreamingResponse:
    """SSE：订阅单个任务的事件流（completed/failed/phase）。"""
    task = get_task_or_none(db, task_id)
    if task is None or (task.user_id is not None and task.user_id != user.id):
        raise HTTPException(
            status_code=404, detail={"code": "TASK_NOT_FOUND", "message": "任务不存在"}
        )

    def event_stream():
        # 若任务已终态，直接补发终态帧，避免订阅晚于完成导致挂流
        current = get_task_or_none(db, task_id)
        if current is not None and current.status in {"completed", "failed", "cancelled"}:
            payload = json.dumps(
                {
                    "type": current.status,
                    "data": {
                        "output_url": current.output_url or "",
                        "error": current.error_msg or "",
                    },
                },
                ensure_ascii=False,
            )
            yield f"data: {payload}\n\n"
            return
        # LLM 流式：生成中先重放累计快照（幂等：delta 为全量文本）
        if current is not None and current.type == "llm" and current.status == "generating":
            try:
                from backend.app.services.task_queue import get_sync_redis

                snap = get_sync_redis().get(f"canvas:task_text:{task_id}")
                if snap:
                    yield "data: " + json.dumps(
                        {"type": "delta", "data": {"text": snap}}, ensure_ascii=False
                    ) + "\n\n"
            except Exception:  # noqa: BLE001 — 快照不可达不阻断订阅
                pass
        pubsub = subscribe_task_events(task_id)
        try:
            while True:
                # 有界等待而非阻塞 listen()：空周期发心跳注释帧，
                # 防中间层空闲断连，也让已断开的客户端及时释放线程
                message = pubsub.get_message(timeout=5.0)
                if message is None:
                    yield ": keepalive\n\n"
                    continue
                if message.get("type") == "message":
                    payload = message.get("data") or "{}"
                    yield f"data: {payload}\n\n"
                    parsed = json.loads(payload)
                    if parsed.get("type") in {"completed", "failed"}:
                        return
        finally:
            pubsub.close()

    return StreamingResponse(
        event_stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
    )


@router.get("/{task_id}")
def get_task_detail(
    task_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    task = get_task_or_none(db, task_id)
    if task is None or (task.user_id is not None and task.user_id != user.id):
        raise HTTPException(
            status_code=404, detail={"code": "TASK_NOT_FOUND", "message": "任务不存在"}
        )
    return {"task": task.to_dict()}


@router.delete("/{task_id}")
def cancel_task(
    task_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    task = get_task_or_none(db, task_id)
    if task is None or (task.user_id is not None and task.user_id != user.id):
        raise HTTPException(
            status_code=404, detail={"code": "TASK_NOT_FOUND", "message": "任务不存在"}
        )
    ok = mark_cancel_requested(db, task_id)
    # 僵尸根治：用户取消联动撤销上游 job（精确删 pending，不 interrupt——
    # interrupt 是全局的，会误杀 GPU 上其他健康任务）
    remote = None
    if ok:
        try:
            from backend.app.services.provider_service import resolve_provider_for_task

            provider, _ch = resolve_provider_for_task(
                db, task.user_id, task.type, task.model,
                channel_id=(task.params_dict() or {}).get("channel_id"),
            )
            cancel_fn = getattr(provider, "cancel_remote_job", None)
            if cancel_fn is not None:
                import asyncio as _aio

                remote = _aio.run(cancel_fn(task_id, interrupt=False))
        except Exception:  # noqa: BLE001 — 远程撤销失败不阻断取消落库（收尸兜底）
            remote = None
    return {"cancelled": ok, "remote_cancelled": remote}
