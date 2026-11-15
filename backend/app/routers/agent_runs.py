"""首页自由创作 Agent REST API（/api/agent-runs · 会话鉴权）。

spec §4：POST 创建（能力开关/单活跃 run/限流）→ GET 列表/详情 → stop → SSE。
与 /api/agent/v1（X-API-Key，外部 agent）不同，本路由是产品内用户功能。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.core.db import get_db
from backend.app.models import User
from backend.app.routers.auth import get_current_user
from backend.app.services import agent_orchestrator
from backend.app.services import agent_run_store as store
from backend.app.services.rate_limiter import RateLimitExceeded, enforce

router = APIRouter(prefix="/api/agent-runs", tags=["agent-runs"])


class Mention(BaseModel):
    type: str = Field(..., description='asset | character | prompt | model')
    task_id: int | None = None
    id: int | None = None
    channel_id: int | None = None
    model: str | None = None


class AgentRunCreate(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000)
    mentions: list[Mention] = Field(default_factory=list, max_length=20)


def _run_events_v2(db: Session) -> bool:
    """run_events_v2 flag：只决定新 run 的默认 engine（在途 run 按 run.engine 分派）。"""
    from backend.app.models import CapabilityConfig

    row = db.get(CapabilityConfig, "run_events_v2")
    if row is None:
        return False
    try:
        return json.loads(str(row.value_json or "false")) is True
    except (ValueError, TypeError):
        return False


def _agent_enabled(db: Session) -> bool:
    from backend.app.models import CapabilityConfig

    row = db.get(CapabilityConfig, "agent_enabled")
    if row is None:
        return True
    try:
        return json.loads(str(row.value_json or "true")) is not False
    except (ValueError, TypeError):
        return True


@router.post("", status_code=201)
async def create_agent_run(
    body: AgentRunCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    if not _agent_enabled(db):
        raise HTTPException(
            status_code=403,
            detail={"code": "AGENT_DISABLED", "message": "管理员已关闭自由创作功能"},
        )
    try:
        enforce(
            f"agent-run:{user.id}",
            "agent",
            limit=5,
            window_seconds=60,
            message="提交过于频繁，每分钟最多 5 次",
        )
    except RateLimitExceeded as e:
        raise HTTPException(
            status_code=429, detail={"code": "RATE_LIMITED", "message": str(e)}
        ) from e
    from backend.app.services.task_queue import QueueUnavailableError

    engine = "v2" if _run_events_v2(db) else "v1"
    try:
        run = await agent_orchestrator.submit_run(
            db,
            user=user,
            message=body.message,
            mentions=[m.model_dump() for m in body.mentions],
            engine=engine,
        )
    except QueueUnavailableError as e:
        # run 已入库（queued）：sweeper 会在几分钟内接管重试，用户无需重提
        raise HTTPException(
            status_code=503,
            detail={
                "code": "QUEUE_UNAVAILABLE",
                "message": f"队列暂时不可用，创作已排队，稍后自动开始: {e}",
            },
        ) from e
    except agent_orchestrator.AgentRunError as e:
        status = 409 if e.code == "ACTIVE_RUN_EXISTS" else 400
        detail: dict = {"code": e.code, "message": e.message}
        if e.code == "ACTIVE_RUN_EXISTS":
            active = store.get_active_run(db, user.id)
            if active is not None:
                detail["run_id"] = active.id
                detail["project_id"] = active.project_id
        if e.hint:
            detail["hint"] = e.hint
        raise HTTPException(status_code=status, detail=detail) from e
    return {"run_id": run.id, "project_id": run.project_id}


@router.get("")
def list_agent_runs(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    runs = store.list_runs(db, user.id)
    out = []
    for r in runs:
        d = r.to_dict()
        # 完成度只富化活跃/incomplete（终态 completed 无需；避免全表 N+1）
        if d.get("engine") == "v2" and d.get("status") not in ("completed", "failed", "stopped"):
            d["completion"] = _completion(db, r)
        out.append(d)
    return {"runs": out}


def _own_run(db: Session, user: User, run_id: int):
    run = store.get_run(db, run_id)
    if run is None or run.user_id != user.id:
        raise HTTPException(
            status_code=404, detail={"code": "RUN_NOT_FOUND", "message": "运行不存在"}
        )
    return run


@router.post("/{run_id}/resume", status_code=201)
async def resume_agent_run(
    run_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """续跑未完成的创作（设计 §8）：继承剩余交付物+脑上下文，子 run 谱系可追溯。"""
    try:
        child = await agent_orchestrator.resume_run(db, user=user, run_id=run_id)
    except agent_orchestrator.AgentRunError as e:
        status = (
            409
            if e.code in (
                "ACTIVE_RUN_EXISTS",
                "NOT_RESUMABLE",
                "RESUME_V1_UNSUPPORTED",
                "NOTHING_TO_RESUME",
            )
            else 400
        )
        raise HTTPException(
            status_code=status, detail={"code": e.code, "message": e.message}
        ) from e
    return {"run_id": child.id, "project_id": child.project_id}


def _completion(db: Session, run) -> dict | None:  # noqa: ANN001 — L4 完成度投影（done/total）
    plan = run.plan()
    if plan is None:
        return None
    total = len(plan.get("deliverables") or [])
    if total == 0:
        return None
    from backend.app.services.agent_orchestrator import _remaining_deliverables

    done = total - len(_remaining_deliverables(db, run))
    return {"done": done, "total": total}


@router.get("/notices")
def stream_agent_notices(
    db: Session = Depends(get_db),  # noqa: ARG001 — 鉴权依赖
    user: User = Depends(get_current_user),
) -> StreamingResponse:
    """用户级通知流（L4 toast 通道）：订阅 Redis agent_user_events:{uid}，断线由客户端重连。"""
    return StreamingResponse(
        _notices_stream(int(user.id)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


def _notices_stream(user_id: int):  # noqa: ANN202 — generator
    from backend.app.services.task_queue import get_sync_redis

    try:
        pubsub = get_sync_redis().pubsub()
        pubsub.subscribe(f"agent_user_events:{user_id}")
    except Exception:  # noqa: BLE001 — Redis 不可用：空流退出，前端重连
        yield 'data: {"type": "notice_unavailable"}\n\n'
        return
    try:
        while True:
            message = pubsub.get_message(timeout=5.0)
            if message is None:
                yield ": keepalive\n\n"
                continue
            if message.get("type") == "message":
                payload = message.get("data") or "{}"
                yield f"data: {payload}\n\n"
    finally:
        pubsub.close()


@router.get("/{run_id}")
def get_agent_run(
    run_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    run = _own_run(db, user, run_id)
    steps = store.list_steps(db, run_id)
    detail = run.to_dict()
    detail["completion"] = _completion(db, run)
    if run.brain_channel_id:
        from backend.app.models import ProviderChannel

        ch = db.get(ProviderChannel, run.brain_channel_id)
        detail["brain_channel_name"] = ch.name if ch else None
    return {"run": detail, "steps": [s.to_dict() for s in steps]}


@router.post("/{run_id}/stop")
async def stop_agent_run(
    run_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    try:
        ok = await agent_orchestrator.stop_run(db, user=user, run_id=run_id)
    except agent_orchestrator.AgentRunError as e:
        raise HTTPException(
            status_code=404, detail={"code": e.code, "message": e.message}
        ) from e
    return {"ok": ok}


@router.get("/{run_id}/events")
def stream_agent_run_events(
    run_id: int,
    request: Request,
    since: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> StreamingResponse:
    """SSE：run 详情快照先行 + 事件续传 + keepalive（镜像分镜 stream 模式）。

    v2 补发（设计 §9.1）：带 since（或 Last-Event-ID 头）时先差量回放 run_events，
    每行携带 `id:`（= run_events.id）供断线重连续传；旧客户端不带 since 行为不变。
    """
    run = _own_run(db, user, run_id)
    last_event_id = since
    if last_event_id is None:
        raw = request.headers.get("Last-Event-ID", "")
        if raw.isdigit():
            last_event_id = int(raw)
    return StreamingResponse(
        _run_stream(db, run.id, since=last_event_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


def _run_stream(db: Session, run_id: int, since: int | None = None):  # noqa: ANN202 — generator
    """快照（run+steps 全量）→（v2：since 差量回放）→ 订阅事件直至终态。"""
    from backend.app.services.task_queue import subscribe_agent_run_events

    run = store.get_run(db, run_id)
    if run is None:
        yield 'data: {"type": "error", "data": {"message": "run not found"}}\n\n'
        return
    snapshot_payload = {
        "run": run.to_dict(),
        "steps": [s.to_dict() for s in store.list_steps(db, run_id)],
    }
    snapshot = json.dumps({"type": "snapshot", "data": snapshot_payload}, ensure_ascii=False)
    yield f"data: {snapshot}\n\n"
    if since is not None and getattr(run, "engine", "v1") == "v2":
        from backend.app.services import agent_events

        for ev in agent_events.list_events_since(db, run_id, since=since):
            line = json.dumps(
                {"type": ev.type, "data": ev.payload(), "event_id": ev.id}, ensure_ascii=False
            )
            yield f"id: {ev.id}\ndata: {line}\n\n"
    terminal_types = {"run_completed", "run_failed", "run_stopped"}
    if run.status in ("completed", "failed", "stopped", "incomplete"):
        yield "data: " + json.dumps(
            {"type": "run_stopped", "data": {"status": run.status}}, ensure_ascii=False
        ) + "\n\n"
        return
    pubsub = subscribe_agent_run_events(run_id)
    try:
        while True:
            message = pubsub.get_message(timeout=5.0)
            if message is None:
                yield ": keepalive\n\n"
                continue
            if message.get("type") == "message":
                payload = message.get("data") or "{}"
                yield f"data: {payload}\n\n"
                try:
                    parsed = json.loads(payload)
                except (ValueError, TypeError):
                    continue
                if parsed.get("type") in terminal_types:
                    return
    finally:
        pubsub.close()
