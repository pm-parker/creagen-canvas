"""Provider 共享素材工具：本地 /outputs 直读（httpx 不支持相对 URL）与 data URL 编码。"""

from __future__ import annotations

from pathlib import Path

import httpx


async def load_asset_bytes(url: str) -> bytes:
    if url.startswith("/outputs/"):
        local = Path("outputs") / url[len("/outputs/"):]
        if not local.is_file():
            raise FileNotFoundError(f"素材不存在: {url}")
        return local.read_bytes()
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as dl:
        resp = await dl.get(url)
        resp.raise_for_status()
        return resp.content


def to_data_url(data: bytes, mime: str = "image/png") -> str:
    import base64

    return f"data:{mime};base64," + base64.b64encode(data).decode()
