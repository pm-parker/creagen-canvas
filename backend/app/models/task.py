"""生成任务模型。

derived from creagen prod app/models/task.py @ 2026-09-27。
列与生产保持一致以便 cherry-pick 回流；type 扩展为四域："image"|"video"|"llm"|"audio"。
失败归因的 to_dict 扩展在错误契约（core/errors.py）落地时补充。
"""

import json
from datetime import datetime
from typing import Any

from sqlalchemy import Column, DateTime, Float, Integer, String, Text, func

from backend.app.core.times import iso_utc
from backend.app.models.base import Base


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=True)
    type = Column(String(10), nullable=False)  # "image" | "video" | "llm" | "audio"
    prompt = Column(Text, nullable=False)
    negative_prompt = Column(Text, default="")
    model = Column(String(50), nullable=False)
    provider = Column(String(40), nullable=True)
    queue_name = Column(String(40), nullable=True)
    status = Column(String(20), nullable=False, default="queued")
    phase = Column(String(40), nullable=False, default="submitted")
    asset_status = Column(String(40), nullable=False, default="pending")
    retry_count = Column(Integer, nullable=False, default=0)
    params = Column(Text, default="{}")  # JSON
    output_url = Column(Text, nullable=True)
    error_msg = Column(Text, nullable=True)
    created_at = Column(DateTime, default=func.now())
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    cancel_requested_at = Column(DateTime, nullable=True)
    worker_heartbeat_at = Column(DateTime, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    cost_amount = Column(Float, nullable=True)
    # D3.4 LLM 计量后端化：真实 token 用量（provider usage 透传；非 LLM 为 None）
    tokens_used = Column(Integer, nullable=True)

    def params_dict(self) -> dict[str, Any]:
        try:
            data = json.loads(self.params or "{}")
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    def to_dict(self, compact: bool = False) -> dict[str, Any]:
        prompt = self.prompt or ""
        if compact and len(prompt) > 220:
            prompt = prompt[:217] + "..."
        return {
            "id": self.id,
            "type": self.type,
            "prompt": prompt,
            "negative_prompt": self.negative_prompt or "",
            "model": self.model,
            "provider": self.provider or self.params_dict().get("provider"),
            # 详情（非 compact）携带 params：重试保真的数据源（N3+M1）
            **({} if compact else {"params": self.params_dict()}),
            "queue_name": self.queue_name,
            "status": self.status,
            "phase": self.phase or "submitted",
            "can_cancel": (
                self.status in {"queued", "generating"} and self.cancel_requested_at is None
            ),
            "can_retry": self.status == "failed",
            "retry_count": self.retry_count or 0,
            "output_url": self.output_url,
            "extra_urls": self.params_dict().get("extra_outputs", []),
            "error_msg": self.error_msg,
            "created_at": iso_utc(self.created_at),
            "started_at": iso_utc(self.started_at),
            "completed_at": iso_utc(self.completed_at),
            "duration_ms": self.duration_ms,
            "cost_amount": round(self.cost_amount, 6) if self.cost_amount is not None else None,
        }

    def mark_started(self, when: datetime | None = None) -> None:
        """置 generating。终态写保护（477 实证）：arq 重投执行的 mark_started
        不得覆盖 failed/completed——已终态直接跳过。"""
        if self.status in {"completed", "failed", "cancelled"}:
            return
        self.status = "generating"
        self.started_at = when or datetime.utcnow()
        self.phase = "starting"

    def mark_completed(self, output_url: str, when: datetime | None = None) -> None:
        self.status = "completed"
        self.phase = "completed"
        self.output_url = output_url
        self.completed_at = when or datetime.utcnow()
        # 清残留：重投/取消竞态会留下 completed+error 矛盾态（画布173 事故教训）
        self.error_msg = None
        self.cancel_requested_at = None

    def mark_failed(self, error_msg: str, when: datetime | None = None) -> None:
        self.status = "failed"
        self.phase = "failed"
        self.error_msg = error_msg
        self.completed_at = when or datetime.utcnow()
