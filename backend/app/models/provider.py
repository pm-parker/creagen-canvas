"""模型渠道配置：渠道 + 模型两级目录（spec A2 新增）。

渠道 key 加密存储（core/crypto.py），任何响应不得回传明文（Review Focus #1）。
"""

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import relationship

from backend.app.core.times import iso_utc
from backend.app.models.base import Base

PROVIDER_DOMAINS = ("image", "video", "llm", "audio")

# 计价单位枚举（全项目唯一来源；spec §4）——新维度=新枚举值，零迁移
PRICING_UNITS = ("per_image", "per_second", "per_character", "per_token_1m", "per_request")


class ProviderChannel(Base):
    __tablename__ = "provider_channels"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,  # NULL=系统渠道（全员共享，管理员配置）
        index=True,
    )
    domain = Column(String(10), nullable=False)  # "image" | "video" | "llm" | "audio"
    name = Column(String(100), nullable=False)
    base_url = Column(String(500), nullable=False)
    key_encrypted = Column(String(1000), nullable=False)  # core/crypto v1: token
    enabled = Column(Integer, nullable=False, default=1)
    sort_order = Column(Integer, nullable=False, default=0)
    # 能力标志 JSON 数组（如 ["edit"]=支持 /images/edits 局部重绘），空=仅基础生成
    capabilities = Column(Text, nullable=False, default="[]")
    created_at = Column(DateTime, default=func.now())

    models = relationship(
        "ProviderModel", back_populates="channel", cascade="all, delete-orphan", lazy="selectin"
    )

    def _capabilities_list(self) -> list[str]:
        import json

        try:
            v = json.loads(self.capabilities or "[]")
            return v if isinstance(v, list) else []
        except (ValueError, TypeError):
            return []

    def to_dict(self, mask_key: str = "") -> dict:
        """序列化。mask_key 必须由调用方显式传入（如 'sk-***abcd'）——本方法绝不解密。"""
        return {
            "id": self.id,
            "domain": self.domain,
            "name": self.name,
            "base_url": self.base_url,
            "key_masked": mask_key,
            "enabled": bool(self.enabled),
            "sort_order": self.sort_order,
            "capabilities": self._capabilities_list(),
            "models": [m.to_dict() for m in self.models],
            "created_at": iso_utc(self.created_at),
        }


class ProviderModel(Base):
    __tablename__ = "provider_models"

    id = Column(Integer, primary_key=True, autoincrement=True)
    channel_id = Column(
        Integer, ForeignKey("provider_channels.id", ondelete="CASCADE"), nullable=False
    )
    model_id = Column(String(100), nullable=False)
    display_name = Column(String(200), nullable=True)
    enabled = Column(Integer, nullable=False, default=1)
    # ---- 模型配置升级批次（spec §4）：全部可空/带默认，存量行为零变化 ----
    # 默认参数（语义键，域 schema 见 services/param_schema.py）
    params_json = Column(Text, nullable=False, default="{}", server_default="{}")
    # 能力元数据：{vision, tools, reasoning, local, max_tokens, context_length, supports_i2i…}
    meta_json = Column(Text, nullable=False, default="{}", server_default="{}")
    # 免费标记（显式语义，看板显示「免费」而非 ¥0/未计价）
    is_free = Column(Integer, nullable=False, default=0, server_default="0")
    # 计价单位：NULL=未配置→回落内置建议价（兼容承诺）
    pricing_unit = Column(String(20), nullable=True)
    price_input = Column(Float, nullable=True)  # 元/张|秒|千字符|1M输入tokens|次
    price_output = Column(Float, nullable=True)  # 仅 per_token_1m：元/1M 输出 tokens

    channel = relationship("ProviderChannel", back_populates="models")

    __table_args__ = (UniqueConstraint("channel_id", "model_id", name="uq_channel_model"),)

    @staticmethod
    def _loads_or_empty(raw: str | None) -> dict:
        """JSON 容错：坏数据回 {} 不炸任务（Review Focus 2 读取侧防线）。"""
        import json

        if not raw:
            return {}
        try:
            v = json.loads(raw)
            return v if isinstance(v, dict) else {}
        except (ValueError, TypeError):
            return {}

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "model_id": self.model_id,
            "display_name": self.display_name or self.model_id,
            "enabled": bool(self.enabled),
            "params_json": self._loads_or_empty(self.params_json),
            "meta_json": self._loads_or_empty(self.meta_json),
            "is_free": bool(self.is_free),
            "pricing_unit": self.pricing_unit,
            "price_input": self.price_input,
            "price_output": self.price_output,
        }
