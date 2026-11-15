"""视频渠道并发闸门（2026-10-08 过载事故修复）。

背景：某 agent 8 秒间隔连发 12 个 i2v 全压 ch40 本地单卡（100%/18.4GB），
排队+生成爆掉 provider 20 分钟超时与 arq job_timeout，重试进一步放大队列。

语义：任务照常入库（queued），worker 拿到后才竞争闸门——拿不到就在闸门前
排队（不提交到上游服务），前序任务释放才真正提交。

实现：Redis INCR/DECR 计数信号量 + TTL 兜底（进程崩溃泄漏自愈）。
无 Redis 上下文（测试/直调）直通——闸门是尽力而为的护栏。
"""

from __future__ import annotations

import asyncio
import logging
import time

from backend.app.core.timeouts import (  # 单点声明（序关系硬约束）
    GATE_QUEUE_TIMEOUT_S,
)
from backend.app.core.timeouts import (
    GATE_TTL_S as VIDEO_GATE_TTL_S,
)

logger = logging.getLogger(__name__)

VIDEO_GATE_LIMIT = 2  # 同渠道视频并发上限
VIDEO_GATE_KEY = "video_gate:ch:{channel_id}"


def gate_key(channel_id: int | str) -> str:
    return VIDEO_GATE_KEY.format(channel_id=channel_id)


async def _call(maybe_coro):  # noqa: ANN001, ANN202
    """兼容同步/异步 Redis 客户端（arq 的 ArqRedis incr 在部分版本是同步方法）。"""
    if asyncio.iscoroutine(maybe_coro):
        return await maybe_coro
    return maybe_coro


async def acquire_video_gate(
    redis,  # noqa: ANN001 — arq ctx["redis"]
    channel_id: int | str,
    *,
    limit: int = VIDEO_GATE_LIMIT,
    timeout_s: float = GATE_QUEUE_TIMEOUT_S,
    poll_s: float = 8.0,
) -> bool:
    """竞争渠道闸门；超时放弃（调用方落库「排队超时」）。

    闸门是尽力而为的护栏：Redis 任何故障（连接/协议异常）直接放行——
    宁可失去限流，不可让护栏杀死正常任务（护栏不是单点）。
    """
    key = gate_key(channel_id)
    try:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            n = await _call(redis.incr(key))
            if n == 1:
                await _call(redis.expire(key, VIDEO_GATE_TTL_S))
            if n <= limit:
                return True
            await _call(redis.decr(key))  # 没抢到，归还
            await asyncio.sleep(poll_s)
    except Exception:  # noqa: BLE001 — 护栏故障放行
        logger.warning("video gate ch=%s redis error, fail-open", channel_id, exc_info=True)
        return True
    logger.warning("video gate ch=%s queue timeout after %.0fs", channel_id, timeout_s)
    return False


async def release_video_gate(redis, channel_id: int | str) -> None:  # noqa: ANN001
    key = gate_key(channel_id)
    try:
        n = await _call(redis.decr(key))
        if n < 0:  # 计数漂移保护（TTL 过期后残留 decr）
            await _call(redis.set(key, 0, ex=VIDEO_GATE_TTL_S))
    except Exception:  # noqa: BLE001 — 释放失败只记日志（TTL 兜底自愈）
        logger.warning("video gate ch=%s release error", channel_id, exc_info=True)
