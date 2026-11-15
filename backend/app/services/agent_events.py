"""Agent Run v2 事件流服务：emit 唯一写入口 + 差量读取 + 投影重建（设计 §3.1）。

契约：业务代码不得绕过 emit 直接改 AgentRun 投影游标；
emit 同事务追加事件并更新 last_event_id，提交后广播 Redis（失败不阻塞）。
"""

from __future__ import annotations

import json
from typing import Any, cast

from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from backend.app.models import AgentRun
from backend.app.models.run_event import RUN_EVENT_TYPES, RunEvent


def emit(
    db: Session,
    run_id: int,
    event_type: str,
    payload: dict[str, Any],
    parent_id: int | None = None,
) -> RunEvent:
    """追加事件 + 同事务更新投影游标 + 提交后广播。

    事件类型必须在 RUN_EVENT_TYPES 开放枚举内（防漂移，设计 §13）。
    """
    if event_type not in RUN_EVENT_TYPES:
        raise ValueError(f"未知事件类型: {event_type}")
    ev = RunEvent(
        run_id=run_id,
        type=event_type,
        parent_id=parent_id,
        payload_json=json.dumps(payload, ensure_ascii=False),
    )
    db.add(ev)
    db.flush()  # 先拿单调 id，同事务内更新投影
    run = db.query(AgentRun).filter(AgentRun.id == run_id).first()
    if run is not None:
        run.last_event_id = ev.id
    db.commit()

    from backend.app.services.task_queue import publish_agent_run_event

    publish_agent_run_event(run_id, event_type, {"event_id": ev.id, **payload})
    return ev


def list_events_since(db: Session, run_id: int, since: int = 0) -> list[RunEvent]:
    """差量读取（SSE 补发 / 回放）。"""
    return (
        db.query(RunEvent)
        .filter(RunEvent.run_id == run_id)
        .filter(cast("ColumnElement[bool]", RunEvent.id > since))
        .order_by(RunEvent.id.asc())
        .all()
    )


def rebuild_projection(db: Session, run_id: int) -> int | None:
    """投影重建（运维工具）：游标以事件流为准。M0 仅 last_event_id，后续扩完成度等。"""
    last = (
        db.query(RunEvent.id)
        .filter(RunEvent.run_id == run_id)
        .order_by(RunEvent.id.desc())
        .first()
    )
    last_id = last[0] if last else None
    run = db.query(AgentRun).filter(AgentRun.id == run_id).first()
    if run is None:
        return last_id
    run.last_event_id = last_id
    db.commit()
    return last_id
