"""API 调用计量模型。

derived from creagen prod app/models/api_usage.py @ 2026-09-27。
"""

from sqlalchemy import Column, DateTime, Integer, String, func

from backend.app.models.base import Base


class APIUsage(Base):
    __tablename__ = "api_usage"

    id = Column(Integer, primary_key=True, autoincrement=True)
    model = Column(String(50), nullable=False)
    count = Column(Integer, default=1)
    window_start = Column(DateTime, nullable=False)
    window_end = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=func.now())
