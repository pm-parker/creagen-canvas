"""任务生命周期：执行包装 / 取消检查 / 失败兜底。

derived from creagen prod app/services/task_lifecycle.py @ 2026-09-27。
asyncio.shield 保证 arq 层取消不会跳过 except 分支导致 DB 卡在 generating。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from backend.app.models import Task
from backend.app.services.task_queue import publish_task_event
from backend.app.services.task_store import (
    _guarded_terminal_write,
    get_task_or_none,
    is_cancel_requested,
)

logger = logging.getLogger("canvas.lifecycle")


class TaskCancelledError(Exception):
    pass


def _dt_format(dt: datetime) -> datetime:
    """终态字段统一用 naive UTC（列类型 naive，避免 PG 报 aware/not-naive）。"""
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


def _fresh_status(db, task_id: int) -> str | None:
    """直读 DB 最新 status（绕过会话缓存），用于日志/兑底定性。"""
    from sqlalchemy import select

    with db.no_autoflush:
        row = db.execute(select(Task.status).where(Task.id == task_id)).first()
    return row[0] if row is not None else None


def _write_terminal_or_keep(
    db,
    task,
    error_msg: str,
    *,
    when: datetime | None = None,
    duration_ms: int | None = None,
    publish: bool = True,
) -> bool:
    """generating → failed 的条件写：已有终态不覆盖。返回是否写入成功。"""
    wrote = _guarded_terminal_write(
        db,
        int(task.id),
        "generating",
        {
            "status": "failed",
            "phase": "failed",
            "error_msg": str(error_msg),
            "completed_at": _dt_format(when or datetime.utcnow()),
            **({"duration_ms": duration_ms} if duration_ms is not None else {}),
        },
    )
    if wrote:
        task.status = "failed"
        task.phase = "failed"
        task.error_msg = error_msg
        if duration_ms is not None:
            task.duration_ms = duration_ms
        db.commit()
        if publish:
            publish_task_event(int(task.id), "failed", {"error": str(error_msg)})
    else:
        logger.warning(
            "task %s failed write dropped: terminal state %r kept",
            task.id, _fresh_status(db, int(task.id)),
        )
    return bool(wrote)


def _writeback_canvas_node(db, task, result: dict) -> None:
    """画布级 Agent API 回写：结果/失败原因写入画布节点 metadata（服务端闭环）。

    task.params.canvas_target = {project_id, node_client_id}（agent 端点写入）。
    """
    target = task.params_dict().get("canvas_target")
    if not isinstance(target, dict) or "node_client_id" not in target:
        return
    try:
        import json as _json

        from backend.app.models import CanvasNode

        # 按 client_id 定位节点（metadata JSON 含 client_id；agent 端点创建的节点必带）
        nodes = (
            db.query(CanvasNode)
            .filter(
                CanvasNode.project_id == target.get("project_id"),
                CanvasNode.metadata_json.contains(f'"client_id": "{target["node_client_id"]}"'),
            )
            .all()
        )
        if not nodes:
            return
        node = nodes[0]
        meta = _json.loads(node.metadata_json or "{}")
        if not isinstance(meta, dict):
            meta = {}
        if result.get("status") == "completed":
            meta["outputUrl"] = result.get("output_url") or ""
            extras = result.get("extra_urls") or []
            if extras:
                meta["extraUrls"] = [meta["outputUrl"], *extras]
            m = result.get("meta") or {}
            if isinstance(m, dict):
                if m.get("duration") is not None:
                    meta["durationSec"] = m["duration"]
                if m.get("resolution"):
                    meta["resolution"] = m["resolution"]
            meta.pop("canvas_agent_error", None)
        else:
            meta.pop("outputUrl", None)
            meta["canvas_agent_error"] = str(result.get("error") or "生成失败")[:300]
        node.metadata_json = _json.dumps(meta, ensure_ascii=False)
        db.commit()
    except Exception:  # noqa: BLE001 — 回写失败不影响任务状态
        logger.warning("canvas writeback failed for task %s", task.id, exc_info=True)


def _sync_prompt_thumb(db, task) -> None:
    """P1-5: 图像任务完成后自动绑定提示词效果图。

    - 个人提示词：始终更新为最新生成结果
    - 种子提示词：只记 usage_count，不自动绑定（防跨用户内容注入，审计批次三）
    - 始终 usage_count += 1（库内热度统计）
    """
    if task.type != "image" or not task.output_url:
        return
    prompt_id = task.params_dict().get("prompt_id")
    if not isinstance(prompt_id, int):
        return
    try:
        from backend.app.models import Prompt

        p = db.get(Prompt, prompt_id)
        if p is None:
            return
        p.usage_count = (p.usage_count or 0) + 1
        # 审计批次三（P2）：仅个人提示词绑定生成图；种子（user_id=None，全员可见）
        # 一旦被任意用户生成图绑定 = 跨用户内容注入（恶意图上全员灵感墙）
        if p.user_id == task.user_id:
            p.thumb_url = task.output_url
        db.commit()
    except Exception:  # noqa: BLE001 — 同步失败不影响任务结果
        logger.warning("prompt sync failed for task %s", task.id, exc_info=True)


def ensure_not_cancelled(db, task_id: int) -> None:
    if is_cancel_requested(db, task_id):
        raise TaskCancelledError("任务已被取消")


async def run_task_lifecycle(
    db,
    task_id: int,
    generate_fn: Callable[[Any], Awaitable[dict[str, Any]]],
    *,
    user_id: int | None = None,
    estimated_cost: float = 0.0,
) -> dict[str, Any]:
    """执行生成任务：starting → completed/failed，成本快照与事件发布。"""
    started_at = datetime.utcnow()
    task = get_task_or_none(db, task_id)
    if task is None:
        return {"status": "failed", "error": f"task {task_id} not found"}

    # 终态写保护：直接调用方（未走 claim）可能传入已终态任务——mark_started
    # 自带终态守卫，不会复活 failed/completed
    task.mark_started()
    task.phase = "starting"
    db.commit()
    publish_task_event(task_id, "phase", {"phase": "starting"})

    try:
        ensure_not_cancelled(db, task_id)
        result = await asyncio.shield(generate_fn(task))
        ensure_not_cancelled(db, task_id)

        completed_at = datetime.utcnow()
        duration_ms = int((completed_at - started_at).total_seconds() * 1000)

        if result.get("status") == "completed":
            # 终态写保护（条件 CAS）：生成期间任务可能已被并发写终态
            # （排队超时收尸 reaper / 用户取消 / 重投的另一个 worker）。
            # completed 不得覆盖 failed 等已有结论——争写失败时尊重现有终态。
            _iso = _dt_format(completed_at)
            usage = result.get("usage") or {}
            extras = result.get("extra_urls") or []
            fields: dict[str, Any] = {
                "status": "completed",
                "phase": "completed",
                "output_url": result.get("output_url") or "",
                "completed_at": _iso,
                "duration_ms": duration_ms,
                "cost_amount": result.get("cost_amount", estimated_cost),
                "error_msg": None,
                "cancel_requested_at": None,
            }
            if usage.get("total_tokens"):
                fields["tokens_used"] = int(usage["total_tokens"])
            if extras:
                merged = task.params_dict()
                merged["extra_outputs"] = list(extras)
                fields["params"] = __import__("json").dumps(merged, ensure_ascii=False)
            n = _guarded_terminal_write(db, task_id, "generating", fields)
            if n == 0:
                _cur = _fresh_status(db, task_id)
                logger.warning(
                    "task %s completed result dropped: terminal state %r kept",
                    task_id, _cur,
                )
                return {**result, "status": _cur or "failed", "superseded": True}
            # 已提交的 ORM 快照同步为终态（后续 writeback/事件读同一状态）
            task.status = "completed"
            task.phase = "completed"
            task.output_url = result.get("output_url") or ""
            task.completed_at = completed_at
            task.duration_ms = duration_ms
            task.cost_amount = result.get("cost_amount", estimated_cost)
            task.error_msg = None
            task.cancel_requested_at = None
            db.commit()
            _writeback_canvas_node(db, task, result)
            _sync_prompt_thumb(db, task)
            # P0: 视频元数据随事件下发（节点时长/分辨率徽标）
            params = task.params_dict()
            meta: dict = {}
            if task.type == "video":
                for k in ("duration", "resolution"):
                    if params.get(k) is not None:
                        meta[k] = params[k]
            payload = {
                "output_url": result.get("output_url", ""),
                "extra_urls": result.get("extra_urls", []),
                "meta": meta,
            }
            publish_task_event(task_id, "completed", payload)
            return result

        error_text = result.get("error") or "生成失败"
        # 终态写保护：failed 同样不得覆盖已有终态（重投/取消竞态）
        if not _write_terminal_or_keep(
            db, task, error_text, when=completed_at, duration_ms=duration_ms, publish=True
        ):
            _writeback_canvas_node(db, task, result)
            return {**result, "superseded": True}
        _writeback_canvas_node(db, task, result)
        return result

    except TaskCancelledError as exc:
        # 终态写保护：取消失败化不得覆盖已有终态（竞态窗口内可能已完成）
        _write_terminal_or_keep(db, task, str(exc), publish=True)
        _writeback_canvas_node(db, task, {"status": "failed", "error": str(exc)})
        return {"status": "failed", "error": str(exc)}

    except asyncio.CancelledError:
        # arq 超时击杀（job_timeout 从 job 开始执行计时，排队不吃预算）≠ 用户取消
        # （真取消走 TaskCancelledError 分支）。按 created_at 距终态的时长区分文案，
        # 杜绝"没人取消却报已取消"的误导（2026-10-08 过载事故实证：433-435 精确
        # 1800.2s 被杀却报"任务已被取消"）。
        from datetime import datetime as _dt

        _elapsed = (_dt.utcnow() - task.created_at).total_seconds() if task.created_at else 0.0
        if _elapsed >= 1700:
            _msg = f"生成超时（已运行 {_elapsed / 60:.0f} 分钟）被系统中止，请减少并发或稍后重试"
        else:
            _msg = "任务被系统中止（worker 关闭或中断），可重新提交"
        # 终态写保护：不覆盖并发已落的终态（收尸/取消竞态）
        _write_terminal_or_keep(db, task, _msg, publish=True)
        raise

    except Exception as exc:  # noqa: BLE001 — 生命周期兜底：任何异常落库为 failed
        logger.exception("Task %s failed during lifecycle", task_id)
        _write_terminal_or_keep(db, task, str(exc), publish=True)
        _writeback_canvas_node(db, task, {"status": "failed", "error": str(exc)})
        return {"status": "failed", "error": str(exc)}
