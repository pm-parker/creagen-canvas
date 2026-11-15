"""画布版本快照（对标 BeefTV 画布保存保护与版本记录）。

策略：手动保存版本 + 自动快照；每画布滚动保留最近 20 份。
"""

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, func

from backend.app.models.base import Base


class CanvasSnapshot(Base):
    __tablename__ = "canvas_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(
        Integer, ForeignKey("canvas_projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version = Column(Integer, nullable=False)
    label = Column(String(200), nullable=False, default="")
    nodes_json = Column(Text, nullable=False)
    connections_json = Column(Text, nullable=False)
    created_by = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=func.now())
