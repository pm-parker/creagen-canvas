"""auth 路由：注册 / 登录 / 当前用户。

derived from creagen prod app/routers/auth.py @ 2026-09-27（精简重写）。
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.app.core.db import get_db
from backend.app.models import User
from backend.app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserResponse
from backend.app.services.auth_service import (
    AuthError,
    authenticate,
    create_access_token,
    decode_token,
    register_user,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    """FastAPI 依赖：从 Bearer token 解析当前用户。"""
    if credentials is None:
        raise HTTPException(status_code=401, detail="未提供认证凭据")
    try:
        user_id, token_ver = decode_token(credentials.credentials)
    except AuthError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message) from e
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="用户不存在")
    # P2-14: 版本校验（改密码后旧 token 立即失效）
    if (user.token_version or 0) != token_ver:
        raise HTTPException(status_code=401, detail="凭据已失效，请重新登录")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


@router.post("/register", response_model=TokenResponse)
def register(body: RegisterRequest, db: Session = Depends(get_db)) -> TokenResponse:
    try:
        user = register_user(db, body.username, body.email, body.password, body.invite_code)
    except AuthError as e:
        raise HTTPException(
            status_code=e.status_code, detail={"code": e.code, "message": e.message}
        ) from e
    return TokenResponse(access_token=create_access_token(user))


@router.post("/login", response_model=TokenResponse)
def login(
    body: LoginRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> TokenResponse:
    """登录：按用户名与来源 IP 双桶限流（N2 防暴力破解；失败也计数）。"""
    from backend.app.services.rate_limiter import RateLimitExceeded, enforce

    ip = request.client.host if request.client else "unknown"

    def _count_fail() -> None:
        """失败计数（超过任一桶即 429）——只计失败，成功登录互不影响。"""
        try:
            enforce(
                f"login:id:{body.identifier}",
                "auth",
                limit=10,
                window_seconds=300,
                message="尝试过于频繁，请 5 分钟后再试",
            )
            enforce(
                f"login:ip:{ip}",
                "auth",
                limit=30,
                window_seconds=300,
                message="该来源尝试过于频繁，请稍后再试",
            )
        except RateLimitExceeded as e:
            raise HTTPException(
                status_code=429,
                detail={
                    "code": "RATE_LIMITED",
                    "message": str(e),
                    "hint": f"请 {e.wait_seconds}s 后重试",
                },
            ) from e

    try:
        user = authenticate(db, body.identifier, body.password)
    except AuthError as e:
        _count_fail()
        raise HTTPException(
            status_code=e.status_code, detail={"code": e.code, "message": e.message}
        ) from e
    # 成功：清失败计数
    from backend.app.services.rate_limiter import clear as clear_limit

    clear_limit("auth", f"login:id:{body.identifier}")
    return TokenResponse(access_token=create_access_token(user))


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


@router.post("/change-password", response_model=TokenResponse)
def change_password(
    body: ChangePasswordRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TokenResponse:
    """修改密码：验证旧密码后更新。

    - 旧密码错误返回 400（WRONG_PASSWORD）而非 401：401 会被前端全局拦截器
      当成「登录过期」从而把用户踢出登录态。
    - 成功后 token_version +1 使旧 token 立即失效（安全属性保留），
      同时签发新 token 返回，调用方据此续期当前会话。
    """
    if not user.verify_password(body.old_password):
        raise HTTPException(
            status_code=400, detail={"code": "WRONG_PASSWORD", "message": "原密码错误"}
        )
    if len(body.new_password) < 8:
        raise HTTPException(
            status_code=422, detail={"code": "PASSWORD_TOO_SHORT", "message": "新密码至少 8 位"}
        )
    user.set_password(body.new_password)
    user.token_version = int(user.token_version or 0) + 1  # noqa: E501 — ORM 假阳性已由 pyproject overrides 覆盖
    db.commit()
    # 重新取一次，确保签发的 token 带最新 token_version
    fresh = db.get(User, user.id) or user
    return TokenResponse(access_token=create_access_token(fresh))


@router.post("/tour-completed")
def mark_tour_completed(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """新手引导完成/跳过标记（服务端化 0.7.1）：幂等，仅置 true。

    注意：user 实例挂在 get_current_user 的 session 上，须用本路由的 db 重新获取后再改，
    否则赋值发生在别的会话、commit 提交的是空事务（不会持久化）。
    """
    db_user = db.get(User, user.id)
    if db_user is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "USER_NOT_FOUND", "message": "用户不存在"},
        )
    if not db_user.tour_completed:
        db_user.tour_completed = True
        db.commit()
    return {"tour_completed": True}


@router.get("/me", response_model=UserResponse)
def me(user: User = Depends(get_current_user)) -> UserResponse:
    return UserResponse.model_validate(user.to_dict())
