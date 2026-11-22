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
    """登录：按用户名与来源 IP 双桶限流（N2 防暴力破解）。

    P2 安全：验密码前先只读预检“用户名桶”是否已超限（不消耗额度）——
    锁定期内即使密码正确也 429，既不泄露“这次猜对没有”，也真正阻断暴力破解。
    IP 桶只对“已失败”的尝试生效（错密码时 429 替代 401），绝不拦截正确密码：
    反代聚合 IP 下，攻击者的失败不应连累同 IP 其他用户正常登录。
    成功登录不计数并清空该用户名失败计数。
    """
    from backend.app.services.rate_limiter import RateLimitExceeded, enforce, is_limited
    from backend.app.services.rate_limiter import clear as clear_limit

    ip = request.client.host if request.client else "unknown"

    def _locked_out() -> HTTPException | None:
        """只读预检：仅查用户名桶（不消耗额度、不看 IP 桶）。

        IP 桶不得进入预检：预检在验密码之前，若它参与判定，攻击者刷爆共享 IP
        桶即可让所有用户（含正确密码）一律 429，构成拒绝服务。
        """
        wait = is_limited("auth", f"login:id:{body.identifier}", limit=10)
        if wait is not None:
            return HTTPException(
                status_code=429,
                detail={
                    "code": "RATE_LIMITED",
                    "message": "尝试过于频繁，请 5 分钟后再试",
                    "hint": f"请 {wait}s 后重试",
                },
            )
        return None

    def _count_fail() -> HTTPException | None:
        """失败计数：两桶各 +1；任一桶超限则返回 429（仅作用于失败尝试）。

        两桶都先计数再判断，避免第一个桶抛出导致第二个桶漏计；
        返回 429 替代 401，只发生在密码已错的前提下——正确密码永不被 IP 桶拦截。
        """
        limited: HTTPException | None = None
        for key, limit, message in (
            (f"login:id:{body.identifier}", 10, "尝试过于频繁，请 5 分钟后再试"),
            (f"login:ip:{ip}", 30, "该来源尝试过于频繁，请稍后再试"),
        ):
            try:
                enforce("auth", key, limit=limit, window_seconds=300, message=message)
            except RateLimitExceeded as e:
                if limited is None:
                    limited = HTTPException(
                        status_code=429,
                        detail={
                            "code": "RATE_LIMITED",
                            "message": str(e),
                            "hint": f"请 {e.wait_seconds}s 后重试",
                        },
                    )
        return limited

    # 锁定期内不验密码，直接 429：不泄露密码正确性，也不给重试机会
    locked = _locked_out()
    if locked is not None:
        raise locked

    try:
        user = authenticate(db, body.identifier, body.password)
    except AuthError as e:
        # 失败才计数；IP 桶超限时以 429 替代 401（密码已错，无泄露风险）
        limited = _count_fail()
        if limited is not None:
            raise limited from e
        raise HTTPException(
            status_code=e.status_code, detail={"code": e.code, "message": e.message}
        ) from e
    # 成功：清失败计数
    clear_limit("auth", f"login:id:{body.identifier}")
    return TokenResponse(access_token=create_access_token(user))


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


@router.post("/change-password")
def change_password(
    body: ChangePasswordRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """修改密码：验证旧密码后更新。"""
    if not user.verify_password(body.old_password):
        raise HTTPException(
            status_code=401, detail={"code": "OLD_PASSWORD_WRONG", "message": "旧密码不正确"}
        )
    if len(body.new_password) < 8:
        raise HTTPException(
            status_code=422, detail={"code": "PASSWORD_TOO_SHORT", "message": "新密码至少 8 位"}
        )
    user.set_password(body.new_password)
    user.token_version = int(user.token_version or 0) + 1  # noqa: E501 — ORM 假阳性已由 pyproject overrides 覆盖
    db.commit()
    return {"changed": True}


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
