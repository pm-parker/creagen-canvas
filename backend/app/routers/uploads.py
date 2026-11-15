"""素材上传：前端生成产物（如宫格切分 dataURL）落盘为可引用资源。"""

import base64
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.app.core.config import get_settings
from backend.app.core.db import get_db
from backend.app.models import UploadRecord, User
from backend.app.routers.auth import get_current_user
from backend.app.services.rate_limiter import RateLimitExceeded, enforce

router = APIRouter(prefix="/api/uploads", tags=["uploads"])

OUTPUT_DIR = Path("outputs/uploads")
# MIME → 扩展 + 大小上限（字节）：图片 10MB / 视频 200MB / 文本 1MB
ALLOWED: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "video/mp4": ".mp4",
    "text/plain": ".txt",
    "audio/mpeg": ".mp3",  # BGM 音轨（0.9.2）
    "audio/mp4": ".m4a",
    "audio/wav": ".wav",
}
SIZE_CAPS: dict[str, int] = {
    "default": 10 * 1024 * 1024,
    "video/mp4": 200 * 1024 * 1024,
    "text/plain": 1024 * 1024,
}


@router.post("", status_code=201)
def upload_data_url(
    body: dict,
    db: Session = Depends(get_db),
    user: "User" = Depends(get_current_user),
):
    """body: {data_url: "data:image/png;base64,...."} → {url: "/outputs/uploads/xxx.png"}"""

    def _err(code: str, message: str, status: int = 422):
        return HTTPException(status_code=status, detail={"code": code, "message": message})

    try:
        enforce(
            f"uploads:{user.id}",
            "process",
            limit=60,
            window_seconds=60,
            message="上传过于频繁，每分钟最多 60 个文件",
        )
    except RateLimitExceeded as e:
        raise _err("RATE_LIMITED", str(e), 429) from e

    data_url = (body.get("data_url") or "").strip()
    if not data_url.startswith("data:"):
        raise _err("DATA_URL_INVALID", "需为 data:image|audio|video/... 格式")
    meta, _, b64 = data_url.partition(",")
    mime = meta[5:].split(";")[0]
    ext = ALLOWED.get(mime)
    if ext is None:
        raise _err("MIME_UNSUPPORTED", f"不支持的类型: {mime}")
    try:
        raw = base64.b64decode(b64, validate=True)
    except Exception as e:  # noqa: BLE001
        raise _err("DATA_INVALID", "base64 解码失败") from e
    cap = SIZE_CAPS.get(mime, SIZE_CAPS["default"])
    if len(raw) > cap:
        raise _err("FILE_TOO_LARGE", f"文件超过 {cap // (1024 * 1024)}MB 上限", 413)

    # 审计批次二（P2）：每用户滚动配额——封堵 60/min × 200MB 的磁盘填充向量
    used = (
        db.query(func.coalesce(func.sum(UploadRecord.size_bytes), 0))
        .filter(UploadRecord.user_id == user.id)
        .scalar()
        or 0
    )
    quota = get_settings().upload_quota_bytes
    if used + len(raw) > quota:
        raise _err(
            "QUOTA_EXCEEDED",
            "上传空间已满"
            f"（已用 {used / 1024 / 1024 / 1024:.1f}GB"
            f" / 配额 {quota / 1024 / 1024 / 1024:.0f}GB），请清理不再使用的素材",
            413,
        )

    out_dir = OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}{ext}"
    (out_dir / name).write_bytes(raw)
    url = f"/outputs/uploads/{name}"
    db.add(
        UploadRecord(
            user_id=user.id, path=url, mime=mime, size_bytes=len(raw), created_at=datetime.utcnow()
        )
    )
    db.commit()
    return {"url": url}
