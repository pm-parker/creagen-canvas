"""comfy_watch 单测：卡死检测/阈值/interrupt 收尸/键清理/多实例配置。"""

from __future__ import annotations

import pytest

from backend.app.services import comfy_watch


@pytest.fixture(autouse=True)
def _reset():
    comfy_watch._RUNNING_SINCE.clear()
    yield
    comfy_watch._RUNNING_SINCE.clear()


def _queue_resp(running=(("71", "pid-a"),), pending=()):
    return {
        "queue_running": [[n, pid, {}] for n, pid in running],
        "queue_pending": [[n, pid, {}] for n, pid in pending],
    }


class _FakeResp:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


def _cfg(urls="", stuck=1800):
    return type("S", (), {"comfyui_watch_urls": urls, "comfyui_stuck_seconds": stuck})()


def test_parse_urls_empty_disables(monkeypatch):
    monkeypatch.setattr(comfy_watch, "_settings", lambda: _cfg())
    import asyncio

    r = asyncio.run(comfy_watch.watch_comfyui_queues())
    assert r == {"watched": 0}


def test_below_threshold_no_interrupt(monkeypatch):
    calls = {"interrupt": 0}

    async def fake_get(url, **kw):  # noqa: ANN001
        return _FakeResp(_queue_resp())

    async def fake_post(url, **kw):  # noqa: ANN001
        calls["interrupt"] += 1
        return _FakeResp({})

    monkeypatch.setattr(comfy_watch.httpx, "AsyncClient", lambda **kw: _Client(fake_get, fake_post))
    monkeypatch.setattr(comfy_watch, "_settings", lambda: _cfg("http://x:8198"))
    import asyncio

    asyncio.run(comfy_watch.watch_comfyui_queues())
    assert calls["interrupt"] == 0
    assert len(comfy_watch._RUNNING_SINCE) == 1  # 已登记跟踪


class _Client:
    def __init__(self, get, post):
        self._get, self._post = get, post

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):  # noqa: ANN002
        return False

    async def get(self, url, **kw):
        return await self._get(url, **kw)

    async def post(self, url, **kw):
        return await self._post(url, **kw)


def test_stuck_beyond_threshold_interrupts_once(monkeypatch):
    calls = {"interrupt": 0}
    t = {"now": 1000.0}

    monkeypatch.setattr(comfy_watch.time, "monotonic", lambda: t["now"])

    async def fake_get(url, **kw):  # noqa: ANN001
        return _FakeResp(_queue_resp())

    async def fake_post(url, **kw):  # noqa: ANN001
        calls["interrupt"] += 1
        return _FakeResp({})

    monkeypatch.setattr(comfy_watch.httpx, "AsyncClient", lambda **kw: _Client(fake_get, fake_post))

    cfg = type("S", (), {"comfyui_watch_urls": "http://x:8198", "comfyui_stuck_seconds": 1800})()

    import asyncio

    asyncio.run(comfy_watch._watch_one("http://x:8198", cfg))
    t["now"] += 1700  # 仍在阈值内
    asyncio.run(comfy_watch._watch_one("http://x:8198", cfg))
    assert calls["interrupt"] == 0
    t["now"] += 200  # 累计 1900s > 1800
    r = asyncio.run(comfy_watch._watch_one("http://x:8198", cfg))
    assert calls["interrupt"] == 1
    assert r["interrupted"] == 1
    # 收尸后键清除（prompt 出队由 ComfyUI 完成，若仍在 running 则重新计时）
    t["now"] += 60
    asyncio.run(comfy_watch._watch_one("http://x:8198", cfg))
    assert calls["interrupt"] == 1  # 重新计时未超阈值


def test_unreachable_prunes_keys_and_survives(monkeypatch):
    async def fake_get(url, **kw):  # noqa: ANN001
        raise comfy_watch.httpx.ConnectError("down")

    async def fake_post(url, **kw):  # noqa: ANN001
        raise AssertionError("should not interrupt when unreachable")

    monkeypatch.setattr(comfy_watch.httpx, "AsyncClient", lambda **kw: _Client(fake_get, fake_post))
    cfg = type("S", (), {"comfyui_watch_urls": "http://x:8198", "comfyui_stuck_seconds": 1800})()

    import asyncio

    comfy_watch._RUNNING_SINCE["http://x:8198|old-pid"] = 0.0  # 既有跟踪
    r = asyncio.run(comfy_watch._watch_one("http://x:8198", cfg))
    assert r.get("error")
    assert "http://x:8198|old-pid" not in comfy_watch._RUNNING_SINCE  # 不可达清键防泄漏


def test_finished_job_prunes_tracking(monkeypatch):
    state = {"resp": _queue_resp()}

    async def fake_get(url, **kw):  # noqa: ANN001
        return _FakeResp(state["resp"])

    async def fake_post(url, **kw):  # noqa: ANN001
        return _FakeResp({})

    monkeypatch.setattr(comfy_watch.httpx, "AsyncClient", lambda **kw: _Client(fake_get, fake_post))
    cfg = type("S", (), {"comfyui_watch_urls": "http://x:8198", "comfyui_stuck_seconds": 1800})()

    import asyncio

    asyncio.run(comfy_watch._watch_one("http://x:8198", cfg))
    assert len(comfy_watch._RUNNING_SINCE) == 1
    state["resp"] = _queue_resp(running=())  # 任务完成出队
    asyncio.run(comfy_watch._watch_one("http://x:8198", cfg))
    assert len(comfy_watch._RUNNING_SINCE) == 0  # 跟踪键清理
