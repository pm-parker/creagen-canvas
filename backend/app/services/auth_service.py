"""认证服务：JWT 签发与邀请制校验。

derived from creagen prod app/routers/auth.py @ 2026-09-27（抽取核心逻辑重写）。
"""

from datetime import UTC, datetime, timedelta

from jose import JWTError, jwt
from sqlalchemy.orm import Session

from backend.app.core.config import get_settings
from backend.app.models import User

ALGORITHM = "HS256"


class AuthError(Exception):
    """携带用户可读信息的认证失败。"""

    def __init__(self, message: str, status_code: int = 400, code: str = "AUTH_INVALID"):
        self.message = message
        self.status_code = status_code
        self.code = code
        super().__init__(message)


def verify_invite_code(candidate: str | None) -> bool:
    """邀请码校验：空值短路拒绝；常数时间比较防时序侧信道。"""
    import secrets

    settings = get_settings()
    if settings.register_mode != "invite":
        return True
    if not candidate or not candidate.strip():
        return False
    # encode 为 bytes：compare_digest 对非 ASCII str 抛 TypeError，bytes 恒安全
    try:
        candidate_bytes = candidate.strip().encode("utf-8")
    except UnicodeEncodeError:
        return False
    return any(
        secrets.compare_digest(candidate_bytes, code.encode("utf-8"))
        for code in settings.invite_code_list
    )


def create_access_token(user: User) -> str:
    settings = get_settings()
    expire = datetime.now(UTC) + timedelta(minutes=settings.access_token_expire_minutes)
    claims = {
        "sub": str(user.id),
        "username": user.username,
        "role": user.role,
        "ver": user.token_version or 0,
        "exp": expire,
    }
    return jwt.encode(claims, settings.jwt_secret, algorithm=ALGORITHM)


def decode_token(token: str) -> tuple[int, int]:
    """校验并返回 user_id；失败抛 AuthError。"""
    try:
        payload = jwt.decode(token, get_settings().jwt_secret, algorithms=[ALGORITHM])
    except JWTError as e:
        raise AuthError("登录已过期或无效，请重新登录", status_code=401) from e
    sub = payload.get("sub")
    if sub is None:
        raise AuthError("令牌主体无效", status_code=401)
    try:
        user_id = int(sub)
    except (TypeError, ValueError) as e:
        raise AuthError("令牌主体无效", status_code=401) from e
    return user_id, payload.get("ver", 0)


def register_user(
    db: Session, username: str, email: str, password: str, invite_code: str | None
) -> User:
    if not verify_invite_code(invite_code):
        raise AuthError("邀请码无效或缺失", status_code=403, code="INVITE_CODE_INVALID")
    if len(username) < 2 or len(username) > 50:
        raise AuthError("用户名长度需在 2-50 之间")
    if "@" not in email or len(email) > 120:
        raise AuthError("邮箱格式不正确")
    if len(password) < 8:
        raise AuthError("密码至少 8 位")
    if db.query(User).filter((User.username == username) | (User.email == email)).first():
        raise AuthError("用户名或邮箱已被使用", status_code=409)
    user = User(username=username, email=email)
    user.set_password(password)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate(db: Session, identifier: str, password: str) -> User:
    """用户名或邮箱 + 密码登录。"""
    user = db.query(User).filter((User.username == identifier) | (User.email == identifier)).first()
    if user is None or not user.verify_password(password):
        raise AuthError("用户名或密码错误", status_code=401)
    return user
