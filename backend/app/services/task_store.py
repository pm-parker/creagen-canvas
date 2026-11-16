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


TERMINAL_TASK_STATUSES = frozenset({"completed", "failed", "cancelled"})


def _guarded_terminal_write(
    db: Session,
    task_id: int,
    expected_status: str,
    fields: dict[str, Any],
) -> int:
    """条件 UPDATE 写终态：仅当 status 仍为 expected 时生效，返回受影响行数。

    终态一旦落库（failed/completed/cancelled）就是权威结论，任何晚到的并发写
    （收尸 reaper、取消、另一个 worker 的回写）都不得覆盖它。WHERE 带原状态
    的条件 UPDATE + rowcount 检查即数据库层的 CAS。
    """
    n = (
        db.query(Task)
        .filter(Task.id == task_id, Task.status == expected_status)
        .update(fields, synchronize_session=False)
    )
    db.commit()
    return int(n)


def claim_queued_task(db: Session, task_id: int) -> str | None:
    """作业入口守卫：queued → generating 的原子认领。

    worker 从队列取到 job 时，任务可能已被并发改写—— queued 收尸 reaper 标成
    failed、另一个 worker（arq 重投）抢先执行、或任务不存在。这里先直读 DB
    最新状态（不用会话缓存），非 queued 直接返回该状态让调用方放弃；仍为
    queued 则用条件 UPDATE 抢占（两 worker 同时认领只有一个成功）。
    先 commit 清空本会话事务，保证读到的是其他进程已提交的最新快照。

    认领只占坑、不计时：不写 started_at。视频任务认领后可能要在渠道闸门
    前排队数十分钟（GATE_QUEUE_TIMEOUT_S），若认领即写 started_at，30 分钟
    generating 收尸会把闸门排队中的任务误判成孤儿标 failed，随后真正完成的
    结果又被终态写保护丢弃（review 实证事故）。started_at 留给生成真正开始
    的时刻（task_lifecycle.mark_started）；phase=waiting_gate 是“已认领、
    生成未开始（可能在闸门排队）”的显式标记，收尸 sweep 以 started_at
    IS NULL 识别并跳过这类任务。
    """
    db.commit()  # 结束旧事务：确保下面的 SELECT 取到最新已提交快照
    fresh_status, _cancel_at = _fresh_cancel_state(db, task_id)
    if fresh_status != "queued":
        return fresh_status  # None=不存在；其余为已认领/终态——一律不执行
    from datetime import datetime

    now = datetime.utcnow()
    n = _guarded_terminal_write(
        db,
        task_id,
        "queued",
        {"status": "generating", "phase": "waiting_gate", "worker_heartbeat_at": now},
    )
    if n == 0:
        # 认领窗口内被并发改写（reaper 失败化 / 另一 worker 认领）——重读定性
        db.rollback()
        fresh_status, _ = _fresh_cancel_state(db, task_id)
        return fresh_status if fresh_status is not None else "gone"
    # 本会话 identity map 里的 Task 同步为已认领状态（后续 ORM 写基于同一行）
    task = get_task_or_none(db, task_id)
    if task is not None:
        db.refresh(task)
    return "claimed"


def ensure_claimed_task_startable(db: Session, task_id: int) -> str | None:
    """闸门后、生成前的复检：任务仍可开始生成才返回 None，否则返回当前状态。

    闸门前排队最长 GATE_QUEUE_TIMEOUT_S（3500s），期间任务可能已被并发写终态
    （用户取消 / 收尸 reaper）——此时不得再启动生成。直读 DB 最新状态
    （不用会话缓存）定性；已认领未终态（generating）则放行，started_at 由
    task_lifecycle.mark_started 在生成真正开始时写入（闸门等待不计超时）。
    """
    db.commit()  # 结束旧事务：确保下面的 SELECT 取到最新已提交快照
    fresh_status, _ = _fresh_cancel_state(db, task_id)
    if fresh_status == "generating":
        return None  # 已认领未终态——闸门已拿到，可以开始生成
    return fresh_status

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
