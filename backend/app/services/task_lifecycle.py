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

from backend.app.services.task_queue import publish_task_event
from backend.app.services.task_store import get_task_or_none, is_cancel_requested

logger = logging.getLogger("canvas.lifecycle")


class TaskCancelledError(Exception):
    pass


def _writeback_canvas_node(db, task, result: dict) -> None:
    """画布级 Agent API 回写：结果/失败原因写入画布节点 metadata（服务端闭环）。

    task.params.canvas_target = {project_id, node_client_id}（agent 端点写入）。
    """
    target = task.params_dict().get("canvas_target")
    if not isinstance(target, dict) or "node_client_id" not in target:
        return
    try:
        import json as _json

        from backend.app.services.canvas_writeback import find_node_by_client_id

        # 按 client_id 定位节点：解析 metadata JSON 后比对字段值（不得子串匹配原始
        # JSON 字符串——客户端重存会重排序/重转义键，导致回写丢失目标节点）
        node = find_node_by_client_id(db, target.get("project_id"), target["node_client_id"])
        if node is None:
            return
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
            task.mark_completed(result.get("output_url") or "", when=completed_at)
            task.duration_ms = duration_ms
            task.cost_amount = result.get("cost_amount", estimated_cost)
            # D3.4 LLM 计量后端化：真实 token 用量落任务行（成本看板展示 token 消耗）
            usage = result.get("usage") or {}
            if usage.get("total_tokens"):
                task.tokens_used = int(usage["total_tokens"])
            # P2-9: 多图备选持久化（params JSON 列，免迁移；详情/重试可取回）
            extras = result.get("extra_urls") or []
            if extras:
                merged = task.params_dict()
                merged["extra_outputs"] = list(extras)
                task.params = __import__("json").dumps(merged, ensure_ascii=False)
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
        task.mark_failed(error_text, when=completed_at)
        task.duration_ms = duration_ms
        db.commit()
        _writeback_canvas_node(db, task, result)
        publish_task_event(task_id, "failed", {"error": str(error_text)})
        return result

    except TaskCancelledError as exc:
        task.mark_failed(str(exc))
        db.commit()
        _writeback_canvas_node(db, task, {"status": "failed", "error": str(exc)})
        publish_task_event(task_id, "failed", {"error": str(exc)})
        return {"status": "failed", "error": str(exc)}

    except asyncio.CancelledError:
        # arq 超时击杀（job_timeout 从入队计时，排队也吃预算）≠ 用户取消
        # （真取消走 TaskCancelledError 分支）。按 created_at 距终态的时长区分文案，
        # 杜绝"没人取消却报已取消"的误导（2026-10-08 过载事故实证：433-435 精确
        # 1800.2s 被杀却报"任务已被取消"）。
        from datetime import datetime as _dt

        _elapsed = (_dt.utcnow() - task.created_at).total_seconds() if task.created_at else 0.0
        if _elapsed >= 1700:
            _msg = f"生成超时（已运行 {_elapsed / 60:.0f} 分钟）被系统中止，请减少并发或稍后重试"
        else:
            _msg = "任务被系统中止（worker 关闭或中断），可重新提交"
        task.mark_failed(_msg)
        db.commit()
        publish_task_event(task_id, "failed", {"error": _msg})
        raise

    except Exception as exc:  # noqa: BLE001 — 生命周期兜底：任何异常落库为 failed
        logger.exception("Task %s failed during lifecycle", task_id)
        task.mark_failed(str(exc))
        db.commit()
        _writeback_canvas_node(db, task, {"status": "failed", "error": str(exc)})
        publish_task_event(task_id, "failed", {"error": str(exc)})
        return {"status": "failed", "error": str(exc)}
