"""画布模型：项目 / 节点 / 连线。

derived from creagen prod app/models/canvas.py @ 2026-09-27。
"""

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import relationship

from backend.app.core.times import iso_utc
from backend.app.models.base import Base


class CanvasProject(Base):
    __tablename__ = "canvas_projects"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(200), nullable=False, default="未命名画布")
    viewport = Column(Text, default='{"x":0,"y":0,"k":1}')
    background_mode = Column(String(20), default="dot")
    deleted_at = Column(DateTime, nullable=True, index=True)  # 回收站软删除
    revision = Column(Integer, nullable=False, default=0, server_default="0")  # 节点同步乐观锁
    # 只读分享（P0）：null=私有；非空=公开只读链接 token（进回收站/删除即失效）
    share_token = Column(String(24), unique=True, nullable=True, index=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    nodes = relationship(
        "CanvasNode", back_populates="project", cascade="all, delete-orphan", lazy="selectin"
    )
    connections = relationship(
        "CanvasConnection", back_populates="project", cascade="all, delete-orphan", lazy="selectin"
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "title": self.title,
            "viewport": self.viewport,
            "background_mode": self.background_mode,
            "revision": self.revision or 0,
            "share_token": self.share_token,
            "created_at": iso_utc(self.created_at),
            "updated_at": iso_utc(self.updated_at),
        }


class CanvasNode(Base):
    __tablename__ = "canvas_nodes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(
        Integer, ForeignKey("canvas_projects.id", ondelete="CASCADE"), nullable=False
    )
    type = Column(String(20), nullable=False)  # "text" | "image" | "video" | "audio"
    title = Column(String(200), default="")
    position_x = Column(Float, default=0)
    position_y = Column(Float, default=0)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    metadata_json = Column(Text, default="{}")

    project = relationship("CanvasProject", back_populates="nodes")

    def to_dict(self) -> dict:
        import json

        try:
            metadata = json.loads(self.metadata_json or "{}")
        except json.JSONDecodeError:
            metadata = {}  # 容错：历史脏数据不炸整画布
        if not isinstance(metadata, dict):
            metadata = {}
        return {
            "id": self.id,
            "project_id": self.project_id,
            "type": self.type,
            "title": self.title,
            "position": {"x": self.position_x, "y": self.position_y},
            "width": self.width,
            "height": self.height,
            "metadata": metadata,
        }


class CanvasConnection(Base):
    __tablename__ = "canvas_connections"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(
        Integer, ForeignKey("canvas_projects.id", ondelete="CASCADE"), nullable=False
    )
    from_node_id = Column(Integer, nullable=False)
    to_node_id = Column(Integer, nullable=False)

    project = relationship("CanvasProject", back_populates="connections")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "from_node_id": self.from_node_id,
            "to_node_id": self.to_node_id,
        }
