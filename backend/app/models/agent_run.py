"""Agent 自由创作运行模型。

spec: docs/superpowers/specs/2026-10-09-homepage-agent-design.md §1
messages_json 为脑对话历史真相源（重启恢复）；pending_json 类型化等待物（v2 可扩 skill/remote）。
"""

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)

from backend.app.models.base import Base

AGENT_RUN_ACTIVE_STATUSES = ("queued", "planning", "waiting")
AGENT_RUN_TERMINAL_STATUSES = ("completed", "failed", "stopped", "incomplete")


class AgentRun(Base):
    __tablename__ = "agent_runs"

    # 用户维度查运行（agent_run_store.get_active_run / list_runs 走 user_id+status）
    __table_args__ = (Index("ix_agent_runs_user_status", "user_id", "status"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    project_id = Column(Integer, nullable=False)  # 自动创建的画布项目
    status = Column(String(20), nullable=False, default="queued")
    messages_json = Column(Text, nullable=False, default="[]")
    pending_json = Column(Text, nullable=False, default="[]")
    # reference 白名单（mention 素材 ∪ run 产物）——generate 工具策略钩子依据
    reference_whitelist_json = Column(Text, nullable=False, default="[]")
    step_count = Column(Integer, nullable=False, default=0)
    max_steps = Column(Integer, nullable=False, default=12)
    pinned_model_json = Column(Text)  # {"channel_id": int, "model": str} | None
    brain_channel_id = Column(Integer, nullable=True)
    tokens_used = Column(Integer, nullable=False, default=0)
    est_cost = Column(Float, nullable=False, default=0.0)
    error = Column(Text, nullable=True)
    # ---- v2（事件溯源完成合约 M0）：engine 创建时定格，flag 只定新 run 默认（设计 §3.3/3.4）----
    engine = Column(String(4), nullable=False, default="v1", server_default="v1")
    last_event_id = Column(BigInteger, nullable=True)
    action_steps = Column(  # L2：产出动作的脑轮才计步
        Integer, nullable=False, default=0, server_default="0"
    )
    brain_calls = Column(Integer, nullable=False, default=0, server_default="0")
    done_rejections = Column(Integer, nullable=False, default=0, server_default="0")
    plan_json = Column(Text, nullable=True)
    # M5 压缩记录：{summary, first_kept, chars_before}——messages_json 原文永不改（无损）
    compaction_json = Column(Text, nullable=True)
    extensions_json = Column(Text, nullable=True)
    parent_run_id = Column(Integer, nullable=True)  # resume 谱系
    deliverables_proj_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    def messages(self) -> list[dict]:
        import json

        try:
            data = json.loads(self.messages_json or "[]")
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, TypeError):
            return []

    def reference_whitelist(self) -> list[str]:
        import json

        try:
            data = json.loads(self.reference_whitelist_json or "[]")
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, TypeError):
            return []

    def plan(self) -> dict | None:
        import json

        try:
            data = json.loads(self.plan_json or "null")
            return data if isinstance(data, dict) else None
        except (json.JSONDecodeError, TypeError):
            return None

    def compaction(self) -> dict | None:
        import json

        try:
            data = json.loads(self.compaction_json or "null")
            return data if isinstance(data, dict) else None
        except (json.JSONDecodeError, TypeError):
            return None

    def deliverables_map(self) -> dict:
        """中途投影（设计 §5.4）：{label: [task_ids]}——L4 完成度数据源。"""
        import json

        try:
            data = json.loads(self.deliverables_proj_json or "{}")
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    def pending(self) -> list[dict]:
        import json

        try:
            data = json.loads(self.pending_json or "[]")
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, TypeError):
            return []

    def to_dict(self) -> dict:
        from backend.app.core.times import iso_utc

        return {
            "id": self.id,
            "user_id": self.user_id,
            "project_id": self.project_id,
            "status": self.status,
            "step_count": self.step_count,
            "max_steps": self.max_steps,
            "pinned_model": (self.pinned_model_json or None),
            "brain_channel_id": self.brain_channel_id,
            "tokens_used": self.tokens_used or 0,
            "est_cost": self.est_cost or 0.0,
            "error": self.error,
            "engine": getattr(self, "engine", "v1"),
            "parent_run_id": self.parent_run_id,
            "action_steps": self.action_steps or 0,
            "brain_calls": self.brain_calls or 0,
            "created_at": iso_utc(self.created_at),
            "updated_at": iso_utc(self.updated_at),
        }


class AgentStep(Base):
    __tablename__ = "agent_steps"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(
        Integer, ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    idx = Column(Integer, nullable=False, default=0)
    thought = Column(Text, default="")
    action_json = Column(Text, default="{}")
    result_json = Column(Text, default="{}")
    created_at = Column(DateTime, default=func.now())

    def to_dict(self) -> dict:
        import json

        from backend.app.core.times import iso_utc

        def _parse(raw: str) -> dict:
            try:
                v = json.loads(raw or "{}")
                return v if isinstance(v, dict) else {}
            except (json.JSONDecodeError, TypeError):
                return {}

        return {
            "id": self.id,
            "run_id": self.run_id,
            "idx": self.idx,
            "thought": self.thought or "",
            "action": _parse(self.action_json),
            "result": _parse(self.result_json),
            "created_at": iso_utc(self.created_at),
        }
