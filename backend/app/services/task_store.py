"""任务持久化：创建 / 查询 / 状态迁移。

derived from creagen prod app/services/task_store.py @ 2026-09-27（W1 精简版：
保留创建/查询/取消/参数合并；重试与 stale 清理在 W3+ 按需抽回）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from backend.app.models import Task
from backend.app.models.provider import PROVIDER_DOMAINS

VALID_TASK_TYPES = PROVIDER_DOMAINS  # image | video | llm | audio


class TaskValidationError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def create_task(
    db: Session,
    *,
    user_id: int,
    type_: str,
    prompt: str,
    model: str,
    params: dict[str, Any] | None = None,
    negative_prompt: str = "",
    queue_name: str = "generate",
) -> Task:
    import json

    if type_ not in VALID_TASK_TYPES:
        raise TaskValidationError("TASK_TYPE_INVALID", f"不支持的任务类型: {type_}")
    if not prompt or not prompt.strip():
        raise TaskValidationError("PROMPT_EMPTY", "提示词不能为空")
    task = Task(
        user_id=user_id,
        type=type_,
        prompt=prompt.strip(),
        negative_prompt=negative_prompt,
        model=model,
        queue_name=queue_name,
        params=json.dumps(params or {}, ensure_ascii=False),
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def get_task_or_none(db: Session, task_id: int) -> Task | None:
    return db.get(Task, task_id)


def list_user_tasks(
    db: Session,
    user_id: int,
    *,
    status: str | None = None,
    type_: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Task]:
    query = db.query(Task).filter(Task.user_id == user_id)
    if status:
        query = query.filter(Task.status == status)
    if type_:
        query = query.filter(Task.type == type_)
    return query.order_by(Task.created_at.desc()).offset(offset).limit(min(limit, 200)).all()


def merge_task_params(db: Session, task_id: int, extra: dict[str, Any]) -> None:
    task = get_task_or_none(db, task_id)
    if task is None:
        return
    merged = {**task.params_dict(), **extra}
    import json

    task.params = json.dumps(merged, ensure_ascii=False)
    db.commit()


def _fresh_cancel_state(db: Session, task_id: int) -> tuple[str | None, object]:
    """直读 DB 最新 (status, cancel_requested_at)，绕过 identity map 缓存。

    worker / API 是两个进程两个会话，expire_on_commit=False 时 db.get 命中的
    是本会话缓存的 Task 实例——另一进程提交的取消标记永远看不见。
    这里只 SELECT 列值（不加载 ORM 实体），因此不会覆盖本会话的待写状态。
    """
    from sqlalchemy import select

    stmt = select(Task.status, Task.cancel_requested_at).where(Task.id == task_id)
    with db.no_autoflush:  # 不打断会话内未 flush 的 pending 变更
        row = db.execute(stmt).first()
    if row is None:
        return None, None
    return row[0], row[1]


def mark_cancel_requested(db: Session, task_id: int) -> bool:
    from datetime import datetime

    task = get_task_or_none(db, task_id)
    if task is None:
        return False
    status, cancel_at = _fresh_cancel_state(db, task_id)
    if status is not None and status not in {"queued", "generating"}:
        return False  # 已终态：不得复活取消标记
    if cancel_at is not None:
        return True
    task.cancel_requested_at = datetime.utcnow()
    db.commit()
    return True


def is_cancel_requested(db: Session, task_id: int) -> bool:
    """任务是否已被请求取消（读 DB 最新值，不用会话缓存）。"""
    _status, cancel_at = _fresh_cancel_state(db, task_id)
    return cancel_at is not None
