"""结构化错误契约：全 API 统一 error envelope。

借鉴 BeefTV v1.5.4 错误归因模式：每个失败给出 code / message / hint（下一步动作）。
"""

from __future__ import annotations

import logging

from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("canvas.errors")


class ApiError(Exception):
    """带稳定错误码的业务异常——前端按 code 分支，不靠解析文案。"""

    status_code = 400
    code = "API_ERROR"
    message = "请求处理失败"
    hint = ""

    def __init__(
        self,
        message: str = "",
        *,
        code: str | None = None,
        status_code: int | None = None,
        hint: str = "",
    ) -> None:
        if message:
            self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code
        if hint:
            self.hint = hint
        super().__init__(self.message)


class ProviderNotConfiguredError(ApiError):
    status_code = 422
    code = "PROVIDER_NOT_CONFIGURED"
    message = "尚未配置可用的模型渠道"
    hint = "请在「设置 → 模型渠道」添加并启用渠道后重试"


class QueueUnavailableError(ApiError):
    status_code = 503
    code = "QUEUE_UNAVAILABLE"
    message = "任务队列暂不可用"
    hint = "请稍后重试；若持续出现请检查 Redis 服务"


class ProviderUnreachableError(ApiError):
    status_code = 502
    code = "PROVIDER_UNREACHABLE"
    message = "模型渠道连通失败"
    hint = "请检查 Base URL 与网络连通性"


async def api_error_handler(_: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": {"code": exc.code, "message": exc.message, "hint": exc.hint}},
    )


async def unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error: %s", exc)
    return JSONResponse(
        status_code=500,
        content={
            "detail": {
                "code": "INTERNAL_ERROR",
                "message": "服务内部错误",
                "hint": "请查看服务端日志；该错误通常不应出现",
            }
        },
    )
