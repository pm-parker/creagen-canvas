"""滑动窗口限流：Redis 计数，不可达时回落进程内存（单副本语义）。

derived from creagen prod app/services/rate_limiter.py @ 2026-09-27。
"""

from __future__ import annotations

import time

from backend.app.services.task_queue import get_sync_redis

_memory_store: dict[str, tuple[int, float]] = {}


class RateLimitExceeded(RuntimeError):
    def __init__(self, wait_seconds: int, message: str):
        super().__init__(message)
        self.wait_seconds = wait_seconds


def _key(scope: str, key: str) -> str:
    return f"canvas:ratelimit:{scope}:{key}"


def _memory_incr(scope: str, key: str, window_seconds: int) -> tuple[int, int]:
    now = time.time()
    store_key = _key(scope, key)
    count, expires_at = _memory_store.get(store_key, (0, now + window_seconds))
    if expires_at <= now:
        count, expires_at = 0, now + window_seconds
    count += 1
    _memory_store[store_key] = (count, expires_at)
    return count, max(0, int(expires_at - now))


def consume(scope: str, key: str, limit: int, window_seconds: int) -> tuple[int, int]:
    """计数 +1，返回 (当前次数, 窗口剩余秒)。Redis 不可达时回落内存。"""
    try:
        client = get_sync_redis()
        redis_key = _key(scope, key)
        count = int(client.incr(redis_key))
        if count == 1:
            client.expire(redis_key, window_seconds)
        ttl = int(client.ttl(redis_key))
        return count, max(0, ttl)
    except Exception:  # noqa: BLE001 — Redis 故障时退化为内存限流
        return _memory_incr(scope, key, window_seconds)


def clear(scope: str, key: str) -> None:
    """清除计数（如登录成功后清失败计数）。"""
    k = _key(scope, key)
    try:
        get_sync_redis().delete(k)
    except Exception:  # noqa: BLE001
        _memory_store.pop(k, None)


def enforce(scope: str, key: str, limit: int, window_seconds: int, message: str) -> None:
    count, ttl = consume(scope, key, limit, window_seconds)
    if count > limit:
        raise RateLimitExceeded(ttl, message)
