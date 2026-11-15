"""用户模型。

derived from creagen prod app/models/user.py @ 2026-09-27。
"""

import bcrypt
from sqlalchemy import Boolean, Column, DateTime, Integer, String, func

from backend.app.core.times import iso_utc
from backend.app.models.base import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), unique=True, nullable=False)
    email = Column(String(120), unique=True, nullable=False)
    password_hash = Column(String(128), nullable=False)
    role = Column(String(20), default="user")
    # P2-14: JWT 撤销——改密码/管理员踢出时 +1，旧 token 立即失效
    token_version = Column(Integer, default=0, nullable=False, server_default="0")
    # Agent API Key（画布级 Agent API）：cg- 前缀；null=未生成；轮换即旧 key 失效
    api_key = Column(String(64), unique=True, nullable=True, index=True)
    # 新手引导完成标记（服务端化 0.7.1）：清缓存/换设备不再重教育
    tour_completed = Column(Boolean, default=False, nullable=False, server_default="false")
    created_at = Column(DateTime, default=func.now())

    def set_password(self, password: str) -> None:
        self.password_hash = bcrypt.hashpw(password[:72].encode("utf-8"), bcrypt.gensalt()).decode(
            "utf-8"
        )

    def verify_password(self, password: str) -> bool:
        if not self.password_hash:
            return False
        try:
            return bcrypt.checkpw(password[:72].encode("utf-8"), self.password_hash.encode("utf-8"))
        except ValueError:
            return False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "username": self.username,
            "email": self.email,
            "role": self.role,
            "tour_completed": self.tour_completed,
            "created_at": iso_utc(self.created_at),
        }
