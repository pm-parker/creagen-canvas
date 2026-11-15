"""故事板模型：多段短视频脚与分段。

derived from creagen prod app/models/storyboard.py @ 2026-09-27（列保持一致，方法精简）。
"""

import json
from typing import Any

from sqlalchemy import Column, DateTime, Float, Index, Integer, String, Text, UniqueConstraint, func

from backend.app.core.times import iso_utc
from backend.app.models.base import Base


def _safe_json(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        parsed = json.loads(value)
        return parsed if parsed is not None else default
    except json.JSONDecodeError:
        return default


class Storyboard(Base):
    """多段短视频（如 30 段 × 6 秒）。状态机：draft → generating → completed | failed。"""

    __tablename__ = "storyboards"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    title = Column(String(200), nullable=True)
    short_description = Column(Text, nullable=False)

    story_summary = Column(Text, nullable=True)
    character_profiles = Column(Text, default="[]")

    creative_brief = Column(Text, nullable=True)
    segments_count = Column(Integer, nullable=False, default=30)
    segment_duration = Column(Integer, nullable=False, default=6)
    resolution = Column(String(10), nullable=False, default="480p")

    status = Column(String(20), nullable=False, default="draft", index=True)
    final_video_url = Column(String(500), nullable=True)
    character_ref_image_url = Column(String(500), nullable=True)

    metadata_json = Column(Text, default="{}")

    completed_segments = Column(Integer, nullable=False, default=0)
    failed_segments = Column(Integer, nullable=False, default=0)
    total_cost = Column(Float, nullable=True)

    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    started_generation_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "title": self.title,
            "short_description": self.short_description,
            "story_summary": self.story_summary,
            "character_profiles": _safe_json(self.character_profiles, []),
            "creative_brief": _safe_json(self.creative_brief, {}),
            "segments_count": self.segments_count,
            "segment_duration": self.segment_duration,
            "resolution": self.resolution,
            "status": self.status,
            "final_video_url": self.final_video_url,
            "character_ref_image_url": self.character_ref_image_url,
            "metadata": _safe_json(self.metadata_json, {}),
            "completed_segments": self.completed_segments,
            "failed_segments": self.failed_segments,
            "total_cost": self.total_cost,
            "created_at": iso_utc(self.created_at),
            "updated_at": iso_utc(self.updated_at),
            "completed_at": iso_utc(self.completed_at),
        }


class StoryboardSegment(Base):
    """故事板的单个分段（一段视频 clip）。"""

    __tablename__ = "storyboard_segments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    storyboard_id = Column(Integer, nullable=False)
    segment_index = Column(Integer, nullable=False)

    prompt = Column(Text, nullable=False)
    negative_prompt = Column(Text, default="")
    character_refs = Column(Text, default="[]")
    camera_motion = Column(String(100), nullable=True)
    mood = Column(String(50), nullable=True)
    audio_cue = Column(String(200), nullable=True)

    task_id = Column(Integer, nullable=True)
    # 逐段目标时长（None=回落分镜级 segment_duration）
    target_duration_ms = Column(Integer, nullable=True)
    # 关键帧层（M5）：定帧图 URL（已审）+ 当前关键帧批量任务
    keyframe_url = Column(String(500), nullable=True)
    keyframe_task_id = Column(Integer, nullable=True)
    status = Column(String(20), nullable=False, default="pending", index=True)
    output_video_url = Column(String(500), nullable=True)
    last_frame_url = Column(String(500), nullable=True)
    duration_ms = Column(Integer, nullable=True)
    retry_count = Column(Integer, nullable=False, default=0)
    error_message = Column(Text, nullable=True)

    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("storyboard_id", "segment_index", name="uq_storyboard_segment"),
        Index("ix_segments_storyboard", "storyboard_id", "segment_index"),
    )
