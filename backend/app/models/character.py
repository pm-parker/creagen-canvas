"""角色资产（0.9.3）：用户级跨片角色库——名字/外观锚定描述/定妆照。

分镜通过 attach 快照带入 character_profiles；库中后续编辑不影响已带入分镜。
"""

from __future__ import annotations

from sqlalchemy import Column, DateTime, Integer, String, Text, UniqueConstraint, func

from backend.app.core.times import iso_utc
from backend.app.models.base import Base


class CharacterAsset(Base):
    __tablename__ = "character_assets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=False)  # 英文外观锚定描述
    ref_image_url = Column(String(500), nullable=True)  # 定妆照

    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_character_user_name"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "ref_image_url": self.ref_image_url,
            "created_at": iso_utc(self.created_at),
            "updated_at": iso_utc(self.updated_at),
        }
