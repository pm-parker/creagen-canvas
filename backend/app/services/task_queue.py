"""Redis / arq 队列客户端。

derived from creagen prod app/services/task_queue.py @ 2026-09-27。
队列白名单收敛为 generate / maintenance（GPU 队列属生产版，不入本仓库）。
"""

from __future__ import annotations

import asyncio
import json
import logging
from urllib.parse import unquote, urlparse

from arq.connections import RedisSettings, create_pool
from redis import Redis
from redis.client import PubSub

from backend.app.core.config import get_settings

logger = logging.getLogger("canvas.queue")

QUEUE_GENERATE = "generate"
QUEUE_MAINTENANCE = "maintenance"
QUEUE_VIDEO = "video"
ALL_QUEUE_NAMES = (QUEUE_GENERATE, QUEUE_VIDEO, QUEUE_MAINTENANCE)

TASK_EVENT_CHANNEL = "canvas:task_events:{task_id}"
STORYBOARD_EVENT_CHANNEL = "canvas:storyboard_events:{sid}"

_arq_pool = None
_arq_lock = asyncio.Lock()
_sync_redis: Redis | None = None


class QueueUnavailableError(RuntimeError):
    """Redis 不可达——提交端应转为 503 QUEUE_UNAVAILABLE（Review Focus #4）。"""


def redis_settings_from_url(url: str) -> RedisSettings:
    parsed = urlparse(url)
    database = 0
    if parsed.path and parsed.path != "/":
        try:
            database = int(parsed.path.lstrip("/"))
        except ValueError:
            database = 0
    return RedisSettings(
        host=parsed.hostname or "127.0.0.1",
        port=parsed.port or 6379,
        database=database,
        username=unquote(parsed.username) if parsed.username else None,
        password=unquote(parsed.password) if parsed.password else None,
        ssl=parsed.scheme == "rediss",
        conn_retries=1,  # 快速失败：2s 内连不上即报 QUEUE_UNAVAILABLE，不挂起请求
        conn_timeout=2,
    )


def get_sync_redis() -> Redis:
    global _sync_redis
    if _sync_redis is None:
        _sync_redis = Redis.from_url(
            get_settings().redis_url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
    return _sync_redis


async def get_arq_pool():
    global _arq_pool
    if _arq_pool is not None:
        return _arq_pool
    async with _arq_lock:
        if _arq_pool is None:
            _arq_pool = await create_pool(redis_settings_from_url(get_settings().redis_url))
    return _arq_pool


async def close_arq_pool() -> None:
    global _arq_pool
    if _arq_pool is not None:
        await _arq_pool.close()
        _arq_pool = None


async def enqueue_generation_task(task_id: int, queue_name: str = QUEUE_GENERATE):
    try:
        redis = await get_arq_pool()
    except Exception as e:
        raise QueueUnavailableError(f"任务队列不可用: {e}") from e
    return await redis.enqueue_job("run_generation_task", task_id, _queue_name=queue_name)


async def enqueue_storyboard_kick(storyboard_id: int):
    """入队分镜编排推进任务（generate 队列：kick 只做状态推进与派发）。"""
    try:
        redis = await get_arq_pool()
    except Exception as e:
        raise QueueUnavailableError(f"任务队列不可用: {e}") from e
    return await redis.enqueue_job("run_storyboard_kick", storyboard_id, _queue_name=QUEUE_GENERATE)


async def enqueue_storyboard_compose(storyboard_id: int, bgm_url: str | None = None):
    """入队成片合成任务（generate 队列：ffmpeg 在 worker 容器执行）。"""
    try:
        redis = await get_arq_pool()
    except Exception as e:
        raise QueueUnavailableError(f"任务队列不可用: {e}") from e
    return await redis.enqueue_job(
        "run_storyboard_compose", storyboard_id, bgm_url, _queue_name=QUEUE_GENERATE
    )


async def enqueue_agent_step(run_id: int, delay: int = 0):
    """入队 Agent run 迭代（generate 队列：单次迭代只做脑调用+派发，短命）。

    delay>0 用于瞬时错误的退避重试（秒）。
    """
    try:
        redis = await get_arq_pool()
    except Exception as e:
        raise QueueUnavailableError(f"任务队列不可用: {e}") from e
    kwargs: dict = {"_queue_name": QUEUE_GENERATE}
    if delay > 0:
        from datetime import timedelta

        kwargs["_defer_by"] = timedelta(seconds=delay)
    return await redis.enqueue_job("run_agent_step", run_id, **kwargs)


def ping_sync() -> bool:
    try:
        return bool(get_sync_redis().ping())
    except Exception as exc:  # noqa: BLE001
        logger.warning("Redis ping failed: %s", exc)
        return False


def publish_task_event(task_id: int, event_type: str, data: dict) -> None:
    """任务事件广播（SSE 消费）。发布失败不阻塞任务主流程。"""
    try:
        get_sync_redis().publish(
            TASK_EVENT_CHANNEL.format(task_id=task_id),
            json.dumps({"type": event_type, "data": data}, ensure_ascii=False),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("publish_task_event %s failed: %s", task_id, exc)


AGENT_RUN_EVENT_CHANNEL = "agent_run:{run_id}"


def publish_agent_run_event(run_id: int, event_type: str, data: dict) -> None:
    """Agent run 事件广播（SSE 消费）。发布失败不阻塞迭代主流程。"""
    try:
        get_sync_redis().publish(
            AGENT_RUN_EVENT_CHANNEL.format(run_id=run_id),
            json.dumps({"type": event_type, "data": data}, ensure_ascii=False),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("publish_agent_run_event %s failed: %s", run_id, exc)


def subscribe_agent_run_events(run_id: int) -> PubSub:
    client = Redis.from_url(
        get_settings().redis_url,
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=None,
    )
    pubsub = client.pubsub(ignore_subscribe_messages=True)
    pubsub.subscribe(AGENT_RUN_EVENT_CHANNEL.format(run_id=run_id))
    return pubsub


def subscribe_task_events(task_id: int) -> PubSub:
    """返回已订阅该任务频道的 pubsub（SSE 端点使用）。

    专用长连接，不设 socket_timeout：快速失败客户端（socket_timeout=2）的 pubsub
    在空闲 2s 即抛 TimeoutError 断流——真实渠道生成 4-5s 期间无事件，SSE 必断。
    """
    client = Redis.from_url(
        get_settings().redis_url,
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=None,
    )
    pubsub = client.pubsub(ignore_subscribe_messages=True)
    pubsub.subscribe(TASK_EVENT_CHANNEL.format(task_id=task_id))
    return pubsub


def publish_storyboard_event(sid: int, event_type: str, data: dict) -> None:
    """分镜事件广播（SSE 消费）。发布失败不阻塞编排主流程。"""
    try:
        get_sync_redis().publish(
            STORYBOARD_EVENT_CHANNEL.format(sid=sid),
            json.dumps({"type": event_type, "data": data}, ensure_ascii=False),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("publish_storyboard_event %s failed: %s", sid, exc)


def subscribe_storyboard_events(sid: int) -> PubSub:
    """返回已订阅分镜频道的 pubsub（SSE 端点使用）；专用长连接语义同 subscribe_task_events。"""
    client = Redis.from_url(
        get_settings().redis_url,
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=None,
    )
    pubsub = client.pubsub(ignore_subscribe_messages=True)
    pubsub.subscribe(STORYBOARD_EVENT_CHANNEL.format(sid=sid))
    return pubsub
