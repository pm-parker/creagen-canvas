"""auth 请求/响应 schema。"""

from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=50)
    email: str = Field(..., max_length=120)
    password: str = Field(..., min_length=1)
    invite_code: str | None = None


class LoginRequest(BaseModel):
    identifier: str  # 用户名或邮箱
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: int
    username: str
    email: str
    role: str
    tour_completed: bool = False
    # 注：balance 已移除——列全库零写入（纯装饰），返回假余额违背信任原则（W1）；
    # 未来实现真实扣减时再加回。
