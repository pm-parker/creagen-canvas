"""Agent Run 终态通知：画布文字卡 + 用户级 toast（技术设计 §9.2，M0 止血核心）。

铁律：通知永远不破坏终态写入（try/except 全包，设计 §8）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_KIND_TEXT = {
    "completed": "✅ 创作完成",
    "failed": "❌ 创作失败",
    "stopped": "⏹ 创作已停止",
    "incomplete": "⚠️ 创作未完成（可续跑）",
}


@dataclass
class Notice:
    """最小通知结构（设计 §9.2，审查补齐）。"""

    run_id: int
    kind: str  # completed | incomplete | failed | stopped
    title: str
    body: str
    completion: tuple[int, int] | None = None  # (done, total)，M0 无 plan 时为 None
    actionable: str | None = None  # "resume" | None（L3 落地后启用）


def notify_terminal(db: Session, run_id: int, status: str, error: str | None) -> None:
    """终态通知入口：文字卡 + toast。任何异常只记日志，绝不阻塞终态。"""
    try:
        from backend.app.models import AgentRun

        run = db.query(AgentRun).filter(AgentRun.id == run_id).first()
        if run is None:
            return
        title = _KIND_TEXT.get(status, f"创作终态：{status}")
        lines = [title]
        if error:
            explain = {
                "MAX_STEPS": "已达步数上限自动停止（可在 Agent 日志查看详情）",
                "PROTOCOL_ERROR": "模型输出无法解析为动作，可换更强的 LLM 渠道重试",
                "BUDGET_EXCEEDED": "当日 LLM 预算已耗尽",
            }.get(error, error)
            lines.append(f"原因：{explain}")
        notice = Notice(
            run_id=run_id,
            kind=status,
            title=title,
            body="\n".join(lines),
        )
        _create_text_card(db, run, notice)
        _publish_user_event(int(run.user_id), _notice_payload(notice))
    except Exception as exc:  # noqa: BLE001 — 通知永不破坏终态写入
        logger.warning("notify_terminal run=%s failed: %s", run_id, exc)


def notify_budget(db: Session, run_id: int, to_steps: int) -> None:
    """自动扩额通知（设计 §9.2 必通知项）——仅 toast，不打断画布。"""
    try:
        from backend.app.models import AgentRun

        run = db.query(AgentRun).filter(AgentRun.id == run_id).first()
        if run is None:
            return
        _publish_user_event(
            int(run.user_id),
            {
                "type": "run_notice",
                "data": {
                    "run_id": run_id,
                    "kind": "budget_granted",
                    "title": "已自动扩额",
                    "body": f"创作仍在进行：步数预算已自动扩至 {to_steps}",
                    "completion": None,
                    "actionable": None,
                },
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("notify_budget run=%s failed: %s", run_id, exc)


def _notice_payload(notice: Notice) -> dict[str, Any]:
    return {
        "type": "run_notice",
        "data": {
            "run_id": notice.run_id,
            "kind": notice.kind,
            "title": notice.title,
            "body": notice.body,
            "completion": list(notice.completion) if notice.completion else None,
            "actionable": notice.actionable,
        },
    }


def _create_text_card(db: Session, run: Any, notice: Notice) -> None:
    """画布即通知面：终态落文字卡（位置取画布最低点下方，避免遮挡）。"""
    from backend.app.models import CanvasNode

    lowest = (
        db.query(CanvasNode.position_y)
        .filter(CanvasNode.project_id == run.project_id)
        .order_by(CanvasNode.position_y.desc())
        .first()
    )
    y = (lowest[0] + 400) if lowest else 0
    import uuid as _uuid

    # 【2026-10-10 run#17 实证】服务端建节点必须带 client_id——否则客户端加载把流 id
    # 落到服务端数字 id，保存 upsert 命不中 → 幽灵复制（canvas.ts:175 不变量的前提）
    meta = {
        "client_id": f"notice-{_uuid.uuid4().hex[:10]}",
        "text": notice.body,
        "agent_run_id": run.id,
        "notice_kind": notice.kind,
    }
    db.add(
        CanvasNode(
            project_id=run.project_id,
            type="text",
            title=notice.title,
            position_x=0,
            position_y=y,
            metadata_json=json.dumps(meta, ensure_ascii=False),
        )
    )
    db.commit()


def _publish_user_event(user_id: int, payload: dict[str, Any]) -> None:
    """用户级 toast 通道（全局 SSE topic 消费，前端 10-21 接入）。"""
    from backend.app.services.task_queue import get_sync_redis

    try:
        get_sync_redis().publish(
            f"agent_user_events:{user_id}",
            json.dumps(payload, ensure_ascii=False),
        )
    except Exception as exc:  # noqa: BLE001 — toast 失败不影响文字卡
        logger.warning("publish user event uid=%s failed: %s", user_id, exc)
