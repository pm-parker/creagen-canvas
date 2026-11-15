"""能力配置 KV 模型。

生产版 capability_config 表无 ORM 定义，此处按 spec 建最小 KV 模型。
"""

from sqlalchemy import Column, DateTime, String, Text, func

from backend.app.models.base import Base


class CapabilityConfig(Base):
    __tablename__ = "capability_config"

    key = Column(String(100), primary_key=True)
    value_json = Column(Text, default="{}")
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
