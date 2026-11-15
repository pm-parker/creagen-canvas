"""内置 FakeProvider：无外部依赖走通任务闭环（开发/CI 专用）。"""

from __future__ import annotations

import asyncio
from typing import Any

from backend.app.providers.registry import ProviderBase


class FakeProvider(ProviderBase):
    domain = "image"

    def __init__(self, model: str, channel: Any = None):  # noqa: ANN401
        self.model = model
        self.channel = channel

    async def test_connection(self) -> bool:
        return True

    async def list_models(self) -> list[str]:
        return ["fake-image", "fake-video", "fake-llm", "fake-audio"]

    async def run(self, task: Any, on_event: Any = None) -> dict[str, Any]:  # noqa: ANN401
        await asyncio.sleep(0.1)
        if on_event:
            on_event("phase", {"phase": "generating", "message": "FakeProvider 生成中"})
        ext = {"png": "image", "mp4": "video", "txt": "text", "wav": "audio"}
        suffix = next((s for s, d in ext.items() if d == getattr(task, "type", "image")), "png")
        return {
            "status": "completed",
            "output_url": f"/static/fake/{task.id}.{suffix}",
        }


def register_builtin(registry) -> None:  # noqa: ANN001
    """四个域都注册 FakeProvider（仅当 ENABLE_FAKE_PROVIDER 时被 resolve 到）。"""
    registry.register("image", FakeProvider)
    registry.register("video", FakeProvider)
    registry.register("llm", FakeProvider)
    registry.register("audio", FakeProvider)
