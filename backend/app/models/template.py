"""分镜模板（0.10）：用户级段结构快照——批量内容复用的单位。

只存创作结构（prompt/镜头/情绪/音频/逐段时长），不带任何产物或生成状态。
"""

from __future__ import annotations

import json

from sqlalchemy import Column, DateTime, Integer, Text, func

from backend.app.core.times import iso_utc
from backend.app.models.base import Base


class StoryboardTemplate(Base):
    __tablename__ = "storyboard_templates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    title = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    segments_json = Column(Text, nullable=False, default="[]")  # 段结构快照

    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    def to_dict(self) -> dict:
        try:
            segments = json.loads(self.segments_json or "[]")
        except json.JSONDecodeError:
            segments = []
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "segments": segments if isinstance(segments, list) else [],
            "created_at": iso_utc(self.created_at),
            "updated_at": iso_utc(self.updated_at),
        }
