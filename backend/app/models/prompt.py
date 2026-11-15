"""提示词库模型。

derived from creagen prod app/models/prompt.py @ 2026-09-27。
"""

import json

from sqlalchemy import Column, DateTime, Float, ForeignKey, Index, Integer, String, Text, func

from backend.app.core.times import iso_utc
from backend.app.models.base import Base


class Prompt(Base):
    __tablename__ = "prompts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # NULL=内置种子（全员共享）；否则为创建者私有（多用户隔离）
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    promptvault_id = Column(String(36), unique=True, nullable=True)
    title = Column(String(500), nullable=True)
    content = Column(Text, nullable=False)
    category = Column(String(50), nullable=True)
    tags = Column(Text, default="[]")
    image_url = Column(String(500), nullable=True)
    thumb_url = Column(String(500), nullable=True)
    model = Column(String(50), nullable=True)
    source = Column(String(20), default="manual")  # "promptvault" | "manual"
    source_url = Column(String(500), nullable=True)
    usage_count = Column(Integer, default=0)
    gen_count = Column(Integer, default=0)
    favorite_count = Column(Integer, default=0)
    score = Column(Float, default=0.0)
    score_count = Column(Integer, default=0)
    is_template = Column(Integer, default=0)
    token_count = Column(Integer, default=0)
    description = Column(Text, nullable=True)
    ai_tags = Column(Text, nullable=True)
    extra_images = Column(Text, nullable=True)
    # 内容分级（2026-10-07 拍板）：NULL=普通；"mature"=成人/擦边——共享条目默认隐藏
    rating = Column(String(10), nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "content": self.content,
            "category": self.category,
            "tags": json.loads(self.tags) if self.tags else [],
            "image_url": self.image_url,
            "thumb_url": self.thumb_url,
            "model": self.model,
            "source": self.source,
            "usage_count": self.usage_count,
            "gen_count": self.gen_count,
            "favorite_count": self.favorite_count,
            "score": self.score,
            "score_count": self.score_count,
            "is_template": self.is_template,
            "rating": self.rating,
            "description": self.description or "",
            "ai_tags": json.loads(self.ai_tags) if self.ai_tags else [],
            "extra_images": json.loads(self.extra_images) if self.extra_images else [],
            "created_at": iso_utc(self.created_at),
            "updated_at": iso_utc(self.updated_at),
        }


class PromptFavorite(Base):
    __tablename__ = "prompt_favorites"

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    prompt_id = Column(Integer, ForeignKey("prompts.id", ondelete="CASCADE"), primary_key=True)
    created_at = Column(DateTime, default=func.now())

    __table_args__ = (
        Index("ix_prompt_favorites_prompt_id", "prompt_id"),
        Index("ix_prompt_favorites_user_id", "user_id"),
    )
