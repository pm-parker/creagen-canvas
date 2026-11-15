"""对外 OpenAI 兼容 API：外部工具可把 CreaGen Canvas 当生图端点调用。

derived from creagen prod routers/openai_compat.py（简化同步版）。
鉴权：Bearer <用户 JWT>（即 /api/auth/login 签发的 access_token）。
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from backend.app.core.db import get_db
from backend.app.models import User
from backend.app.routers.auth import get_current_user
from backend.app.services.rate_limiter import RateLimitExceeded, enforce

router = APIRouter(prefix="/v1", tags=["openai-compat"])

# 审计批次二（P2）：兼容端点直连用户付费渠道——必须限流；生成必须落任务表
# （成本看板/资产库可见），否则成为可观测性盲区
COMPAT_RATE_LIMIT = 20
COMPAT_RATE_WINDOW = 60


def _compat_rate_limit(user: User) -> None:
    try:
        enforce(
            f"compat:{user.id}",
            "compat",
            limit=COMPAT_RATE_LIMIT,
            window_seconds=COMPAT_RATE_WINDOW,
            message=f"生成过于频繁，每分钟最多 {COMPAT_RATE_LIMIT} 张",
        )
    except RateLimitExceeded as e:
        raise HTTPException(
            status_code=429,
            detail={"message": str(e), "type": "rate_limit_error"},
        ) from e


def _error_status(code: str) -> int:
    return {
        "CHANNEL_KEY_INVALID": 401,
        "PROVIDER_RATE_LIMITED": 429,
        "PROVIDER_TIMEOUT": 504,
    }.get(code, 500)


@router.post("/images/generations")
async def openai_images(
    body: dict,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """OpenAI 兼容文生图 → {created, data:[{url}]}。"""
    from backend.app.providers.openai_compat import OpenAICompatProvider

    model = body.get("model") or "default"
    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail={"message": "prompt 不能为空"})
    size = body.get("size") or "1024x1024"
    try:
        n = max(1, min(int(body.get("n") or 1), 4))
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=422,
            detail={"message": "n 必须是 1-4 的整数", "type": "invalid_request_error"},
        ) from None
    _compat_rate_limit(user)

    from backend.app.services.model_pricing import resolve_cost
    from backend.app.services.task_store import create_task

    started = time.time()
    urls: list[str] = []
    for _ in range(n):
        # 走用户已配置的渠道（resolve 渠道并解密）
        from backend.app.services.provider_service import resolve_channel_for_user

        channel = resolve_channel_for_user(db, user.id, "image")
        if channel is None:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "尚未配置可用的图片生成渠道，请在设置中配置",
                    "type": "invalid_request_error",
                },
            )
        # 配置价优先（含渠道精确计价），回落内置建议价
        per_cost = resolve_cost(db, "image", model, {"size": size}, channel_id=channel.id)
        key_plain = _decrypt(channel.key_encrypted)
        provider = OpenAICompatProvider(
            model=model,
            channel=channel,
            key_plain=key_plain,
            base_url=channel.base_url,
            task_type="image",
        )
        result = await provider.run(
            _SimpleTask(prompt=prompt, params={"size": size}),
            on_event=None,
        )
        if result.get("status") != "completed":
            raise HTTPException(
                status_code=_error_status(result.get("error_code", "PROVIDER_SERVER_ERROR")),
                detail={"message": result.get("error", "生成失败"), "type": "provider_error"},
            )
        urls.append(result["output_url"])
        # 同步生成无队列生命周期——直接落已完成任务行（成本/资产可见）
        from datetime import datetime

        task = create_task(
            db,
            user_id=user.id,
            type_="image",
            prompt=prompt,
            model=model,
            params={"size": size, "source": "openai_compat"},
            queue_name="compat",
        )
        task.status = "completed"
        task.output_url = str(result["output_url"])
        task.cost_amount = per_cost
        task.completed_at = datetime.utcnow()
        db.commit()

    return {
        "created": int(started),
        "data": [{"url": u} for u in urls],
        "latency_ms": int((time.time() - started) * 1000),
    }


class _SimpleTask:
    def __init__(self, prompt: str, params: dict):
        self.id = 0
        self.type = "image"
        self.prompt = prompt
        self._params = params

    def params_dict(self):
        return self._params


def _decrypt(token: str) -> str:
    from backend.app.core.crypto import decrypt_secret

    return decrypt_secret(token)
