"""Agent Run v2 事件流模型（append-only，技术设计 §3.1）。

日志即真相：run 的一切事实先落本表；AgentRun 列/SSE/通知/面板均为投影。
derived from creagen prod design 2026-10-10 agent-run-v2-tech-design.md §3。
"""

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Integer, String, Text, func

from backend.app.models.base import Base

# SQLite 需 INTEGER 才有 rowid 自增；PG 为 BIGINT。模块级变量保 mypy 推断。
_EVENT_ID_TYPE = BigInteger().with_variant(Integer, "sqlite")

# 开放枚举：新能力=新类型，老类型语义永不改（设计 §3.2）
RUN_EVENT_TYPES = (
    "run_created",
    "plan_submitted",
    "plan_append",
    "budget_granted",
    "task_submitted",
    "task_submitted",
    "task_observed",
    "assistant_turn",
    "tool_result",
    "compaction",
    "steering_queued",
    "run_terminal",
)


class RunEvent(Base):
    __tablename__ = "run_events"

    id = Column(_EVENT_ID_TYPE, primary_key=True, autoincrement=True)  # 全局单调=SSE 补发游标
    run_id = Column(
        Integer, ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    type = Column(String(40), nullable=False)
    parent_id = Column(BigInteger, nullable=True)  # 树形预留（steering/分支）
    payload_json = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime, nullable=False, default=func.now())

    def payload(self) -> dict:
        import json

        try:
            data = json.loads(self.payload_json or "{}")
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "run_id": self.run_id,
            "type": self.type,
            "parent_id": self.parent_id,
            "payload": self.payload(),
            "created_at": str(self.created_at) if self.created_at else None,
        }
