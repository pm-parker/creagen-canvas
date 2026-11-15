"""视频轻加工路由（P2）：取帧 / 截取。同步端点（线程池执行，copy 路径秒级）。"""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.app.core.db import get_db
from backend.app.models import User
from backend.app.routers.auth import get_current_user
from backend.app.services import media_service
from backend.app.services.media_service import MediaError
from backend.app.services.rate_limiter import RateLimitExceeded, enforce

router = APIRouter(prefix="/api/media", tags=["media"])

_ERROR_STATUS = {
    "MEDIA_NOT_FOUND": 404,
    "MEDIA_PARAMS_INVALID": 422,
    "MEDIA_URL_INVALID": 422,
    "MEDIA_TOO_LARGE": 413,
    "MEDIA_FETCH_FAILED": 502,
    "FFMPEG_MISSING": 503,
    "FFMPEG_FAILED": 500,
}


def _outputs_root() -> Path:
    return Path("outputs")


def _rate_limit(user: User) -> None:
    """媒体处理限流：ffmpeg 是重 CPU 操作（H3 防 DoS）。"""
    try:
        enforce(
            f"media:{user.id}",
            "process",
            limit=12,
            window_seconds=60,
            message="媒体处理过于频繁，每分钟最多 12 次",
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


def _err(e: MediaError) -> HTTPException:
    return HTTPException(
        status_code=_ERROR_STATUS.get(e.code, 500),
        detail={"code": e.code, "message": e.message, "hint": e.hint},
    )


class FrameRequest(BaseModel):
    url: str = Field(..., min_length=1, max_length=2000)
    # time=-1 = 尾帧（接力续拍语义，与 Agent /frame 一致）；ge=-1 放开
    time: float = Field(0.0, ge=-1, le=36000)


class TrimRequest(BaseModel):
    url: str = Field(..., min_length=1, max_length=2000)
    start: float = Field(0.0, ge=0, le=36000)
    end: float = Field(..., gt=0, le=36000)


class SubtitleRequest(BaseModel):
    url: str = Field(..., min_length=1, max_length=2000)
    text: str = Field(..., min_length=1, max_length=10000)


class ZoomRequest(BaseModel):
    url: str = Field(..., min_length=1, max_length=2000)
    factor: float = Field(2.0, ge=1.2, le=4.0)


def _require_local_url(url: str) -> str:
    """审计批次三（P1）：media 端点 url 仅放行站内 /outputs/。

    任意 http(s) 会经 ensure_local 服务端下载（SSRF 读取原语）；合法任务产物
    全部本地落盘（b64/远端下载在 provider 层完成），远端输入无既有依赖。"""
    if not url.startswith("/outputs/") or ".." in url:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "MEDIA_URL_INVALID",
                "message": "仅支持站内 /outputs/ 素材路径（先上传或从生成产物引用）",
            },
        )
    return url


@router.post("/frame")
def extract_frame_endpoint(
    body: FrameRequest,
    db=Depends(get_db),  # noqa: ARG001 — 鉴权依赖需要会话
    user: User = Depends(get_current_user),
) -> dict:
    _rate_limit(user)
    try:
        video = media_service.ensure_local(_require_local_url(body.url), _outputs_root())
        if body.time < 0:  # -1 = 尾帧
            out = media_service.extract_last_frame(video)
        else:
            out = media_service.extract_frame(video, body.time, _outputs_root())
    except MediaError as e:
        raise _err(e) from e
    return {"url": f"/outputs/{out.relative_to(_outputs_root()).as_posix()}"}


class ComposeRequest(BaseModel):
    urls: list[str] = Field(..., min_length=2, max_length=20)
    bgm_url: str | None = Field(None, max_length=2000)
    transition: str = Field("none", max_length=20)  # none/fade/slideleft/circleopen/wipeleft


@router.post("/compose")
def compose_videos_endpoint(
    body: ComposeRequest,
    db=Depends(get_db),  # noqa: ARG001
    user: User = Depends(get_current_user),
) -> dict:
    """多段视频按序合成成片（首段分辨率归一；可选 BGM 25% 混音）——画布人工/Agent 共用。"""
    _rate_limit(user)
    try:
        for u in body.urls:
            _require_local_url(u)
        if body.bgm_url:
            _require_local_url(body.bgm_url)
        out = media_service.compose_videos(
            body.urls, body.bgm_url, _outputs_root(), transition=body.transition
        )
    except MediaError as e:
        raise _err(e) from e
    return {"url": f"/outputs/{out.relative_to(_outputs_root()).as_posix()}"}


@router.post("/subtitles")
def subtitles_endpoint(
    body: SubtitleRequest,
    db=Depends(get_db),  # noqa: ARG001
    user: User = Depends(get_current_user),
) -> dict:
    """P2-8: 字幕烧录（srt 或纯文本）。"""
    _rate_limit(user)
    try:
        video = media_service.ensure_local(_require_local_url(body.url), _outputs_root())
        out = media_service.burn_subtitles(video, body.text, _outputs_root())
    except MediaError as e:
        raise _err(e) from e
    return {"url": f"/outputs/{out.relative_to(_outputs_root()).as_posix()}"}


@router.post("/zoom")
def zoom_endpoint(
    body: ZoomRequest,
    db=Depends(get_db),  # noqa: ARG001
    user: User = Depends(get_current_user),
) -> dict:
    """P2-8: 视频放大（中心裁剪 + 放回原分辨率）。"""
    _rate_limit(user)
    try:
        video = media_service.ensure_local(_require_local_url(body.url), _outputs_root())
        out = media_service.zoom(video, body.factor, _outputs_root())
    except MediaError as e:
        raise _err(e) from e
    return {"url": f"/outputs/{out.relative_to(_outputs_root()).as_posix()}"}


@router.post("/trim")
def trim_endpoint(
    body: TrimRequest,
    db=Depends(get_db),  # noqa: ARG001
    user: User = Depends(get_current_user),
) -> dict:
    _rate_limit(user)
    try:
        video = media_service.ensure_local(_require_local_url(body.url), _outputs_root())
        out = media_service.trim(video, body.start, body.end, _outputs_root())
    except MediaError as e:
        raise _err(e) from e
    return {"url": f"/outputs/{out.relative_to(_outputs_root()).as_posix()}"}
