"""上传记录（0.10.3 审计批次二）：归属/滚动配额/清理依据。

此前 uploads 端点只落盘不落库——无归属（无法按用户审计/限额）、无配额
（60/min × 200MB = 磁盘填充向量）。本表补齐最小记账。
"""

from __future__ import annotations

from sqlalchemy import Column, DateTime, Integer, String, func

from backend.app.core.times import iso_utc
from backend.app.models.base import Base


class UploadRecord(Base):
    __tablename__ = "upload_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    path = Column(String(500), nullable=False)  # /outputs/uploads/xxx.png
    mime = Column(String(100), nullable=False)
    size_bytes = Column(Integer, nullable=False)

    created_at = Column(DateTime, default=func.now())

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "path": self.path,
            "mime": self.mime,
            "size_bytes": self.size_bytes,
            "created_at": iso_utc(self.created_at),
        }
