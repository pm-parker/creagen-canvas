"""ComfyUI 队列卡死看护：维护 cron 探测 /queue → running 超时 → interrupt 收尸。

背景（2026-10-09 实证）：asus ComfyUI 单线程队列被卡死的 H3 任务堵死，
同机 z-image/h3 两 shim 全部挂起，人工经 8198 应急端口 interrupt 才恢复。
HANDOVER 残余项"ComfyUI 进程自身假死仍靠人工"的本模块化闭环。

设计约束：
- 地址走 env（COMFYUI_WATCH_URLS，逗号分隔；空=关闭）——内网地址不进仓库
- 阈值默认 1800s：覆盖 H3 视频合法长任务（实测 ≤20min），仅收真正卡死
- 跟踪用进程内存 dict（worker 单进程）；重启重计时可接受（检测最多延迟一个阈值窗口）
- interrupt 是全局的：仅在有 running 任务超阈值时调用（同人工收尸语义）
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

logger = logging.getLogger("canvas.services.comfy_watch")

# url|prompt_id -> 首见 monotonic 时间
_RUNNING_SINCE: dict[str, float] = {}

_QUEUE_TIMEOUT = 5.0


def _settings():  # noqa: ANN202 — 延迟导入避免配置循环
    from backend.app.core.config import get_settings

    return get_settings()


def _parse_urls(raw: str) -> list[str]:
    return [u.strip().rstrip("/") for u in raw.split(",") if u.strip()]


async def watch_comfyui_queues() -> dict[str, Any]:
    """入口（维护 cron 调用）：逐实例探测，返回汇总。"""
    urls = _parse_urls(getattr(_settings(), "comfyui_watch_urls", "") or "")
    if not urls:
        return {"watched": 0}
    summary: dict[str, Any] = {"watched": len(urls), "interrupted": 0}
    for base in urls:
        r = await _watch_one(base, _settings())
        summary["interrupted"] += int(r.get("interrupted") or 0)
    return summary


async def _watch_one(base: str, cfg: Any) -> dict[str, Any]:
    stuck_seconds = int(getattr(cfg, "comfyui_stuck_seconds", 1800) or 1800)
    try:
        async with httpx.AsyncClient(timeout=_QUEUE_TIMEOUT) as client:
            resp = await client.get(f"{base}/queue")
            running = (resp.json() or {}).get("queue_running") or []
    except (httpx.HTTPError, ValueError) as e:
        # 不可达：清理本实例跟踪键（防泄漏），静默降级——探测失败不影响维护主流程
        for k in [k for k in _RUNNING_SINCE if k.startswith(f"{base}|")]:
            _RUNNING_SINCE.pop(k, None)
        logger.debug("comfy watch %s unreachable: %s", base, type(e).__name__)
        return {"error": str(e)}

    now = time.monotonic()
    seen: set[str] = set()
    interrupted = 0
    for item in running:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        prompt_id = str(item[1])
        key = f"{base}|{prompt_id}"
        seen.add(key)
        first = _RUNNING_SINCE.setdefault(key, now)
        if now - first >= stuck_seconds:
            logger.warning(
                "comfy watch: %s running job %s stuck %.0fs >= %ss → interrupt 收尸",
                base, prompt_id[:8], now - first, stuck_seconds,
            )
            try:
                async with httpx.AsyncClient(timeout=_QUEUE_TIMEOUT) as client:
                    await client.post(f"{base}/interrupt")
                interrupted += 1
                _RUNNING_SINCE.pop(key, None)  # 收尸后重新计时（若仍卡会再次触发）
                _bump_counter(base)
            except httpx.HTTPError as e:
                logger.warning("comfy watch interrupt %s failed: %s", base, type(e).__name__)

    # 出队/完成的键清理
    for k in [k for k in _RUNNING_SINCE if k.startswith(f"{base}|") and k not in seen]:
        _RUNNING_SINCE.pop(k, None)
    return {"running": len(running), "interrupted": interrupted}


def _bump_counter(base: str) -> None:
    """收尸计数落 KV（运维可见性：管理员看 capability_config.comfy_interrupts）。"""
    try:
        from sqlalchemy import text as _t

        from backend.app.core.db import get_session_factory

        db = get_session_factory()()
        try:
            db.execute(
                _t(
                    "insert into capability_config (key, value_json, updated_at) "
                    "values ('comfy_interrupts', :v, now()) "
                    "on conflict (key) do update set value_json = :v, updated_at = now()"
                ),
                {"v": f'{{"{base}": {_next_count(base)}}}'},
            )
            db.commit()
        finally:
            db.close()
    except Exception:  # noqa: BLE001 — 计数失败不影响收尸
        logger.debug("comfy watch counter write failed", exc_info=True)


def _next_count(base: str) -> int:
    import json as _j

    try:
        from sqlalchemy import text as _t

        from backend.app.core.db import get_session_factory

        db = get_session_factory()()
        try:
            raw = db.execute(
                _t("select value_json from capability_config where key = 'comfy_interrupts'")
            ).scalar()
            return int(_j.loads(raw or "{}").get(base) or 0) + 1
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        return 1
