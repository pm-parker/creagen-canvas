"""Minimax 视频 Provider：video_generation 提交 + query 轮询。

derived from creagen prod app/services/minimax_client.py @ 2026-09-27。
轮询 max_wait 封顶 600s（Review Focus #4），每轮上报 phase 事件。
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

import httpx

from backend.app.providers.registry import ProviderBase

logger = logging.getLogger("canvas.provider.minimax")

MINIMAX_VIDEO_URL = "https://api.minimaxi.com/v1/video_generation"
MINIMAX_VIDEO_QUERY_URL = "https://api.minimaxi.com/v1/query/video_generation"
POLL_INTERVAL = 5
MAX_WAIT = 600

_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
# 提交+轮询同 client：云端视频排队（高峰期 query 也慢）时 30s 会误杀——
# 与 openai_compat._timeout_for 同策略，本地渠道放宽
_LOCAL_TIMEOUT = httpx.Timeout(240.0, connect=10.0)


def _timeout_for(base_url: str) -> httpx.Timeout:
    from ipaddress import ip_address
    from urllib.parse import urlparse

    try:
        host = urlparse(base_url).hostname or ""
        ip = ip_address(host)
        return _LOCAL_TIMEOUT if (ip.is_private or ip.is_loopback) else _TIMEOUT
    except ValueError:
        return _LOCAL_TIMEOUT if host == "localhost" else _TIMEOUT


class MinimaxVideoProvider(ProviderBase):
    domain = "video"

    def __init__(
        self,
        model: str,
        channel: Any = None,  # noqa: ANN401
        key_plain: str = "",
        base_url: str = "",
        poll_interval: int = POLL_INTERVAL,
        max_wait: int = MAX_WAIT,
    ) -> None:
        self.model = model
        self.channel = channel
        self.key_plain = key_plain
        self.base_url = (base_url or MINIMAX_VIDEO_URL).rstrip("/") or MINIMAX_VIDEO_URL
        self.api_base = base_url.rstrip("/") if base_url else "https://api.minimaxi.com/v1"
        self.poll_interval = poll_interval
        self.max_wait = max_wait

    async def test_connection(self) -> bool:
        return True  # minimax 无轻量探活端点，提交时验证

    async def list_models(self) -> list[str]:
        return ["MiniMax-Hailuo-2.3-Fast", "MiniMax-Hailuo-2.3"]

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.key_plain}", "Content-Type": "application/json"}

    def _classify(self, status: int) -> dict | None:
        mapping = {
            401: ("CHANNEL_KEY_INVALID", "Minimax 密钥无效", "请在「设置 → 模型渠道」更新密钥"),
            429: ("PROVIDER_RATE_LIMITED", "触发 Minimax 限流", "请稍后重试"),
        }
        if status in mapping:
            code, msg, hint = mapping[status]
            return {"status": "failed", "error": msg, "error_code": code, "error_hint": hint}
        if status >= 500:
            return {
                "status": "failed",
                "error": f"Minimax 服务异常 {status}",
                "error_code": "PROVIDER_SERVER_ERROR",
                "error_hint": "请稍后重试",
            }
        return None

    async def run(self, task: Any, on_event: Any = None) -> dict:  # noqa: ANN401
        # 时长/分辨率透传（生产 minimax_client 契约：Hailuo 2.3 系列 duration ∈ {6,10}，
        # resolution 大写 P；非法值回落默认，不 400）
        params = task.params_dict() if hasattr(task, "params_dict") else {}
        try:
            duration = int(params.get("duration") or 0)
        except (TypeError, ValueError):
            duration = 6
        if duration not in (6, 10):
            duration = 6
        resolution = str(params.get("resolution") or "768P").upper()
        if resolution not in ("768P", "1080P"):
            resolution = "768P"

        # i2v 首帧图：上游图片节点 reference_urls[0]（本地 /outputs 转 data URL，远端直传）
        first_frame: str | None = None
        refs = params.get("reference_urls") or []
        if isinstance(refs, list) and refs:
            from backend.app.providers.assets import load_asset_bytes, to_data_url

            r0 = str(refs[0])
            try:
                if r0.startswith("/outputs/"):
                    first_frame = to_data_url(await load_asset_bytes(r0))
                elif r0.startswith(("http://", "https://")):
                    first_frame = r0
            except (httpx.HTTPError, OSError) as e:
                return self._fail(
                    "ASSET_FETCH_FAILED", f"首帧图获取失败: {type(e).__name__}", "请重试"
                )

        started = time.monotonic()
        async with httpx.AsyncClient(timeout=_timeout_for(self.api_base)) as client:
            # 提交
            try:
                submit_body: dict = {
                    "model": self.model,
                    "prompt": task.prompt,
                    "duration": duration,
                    "resolution": resolution,
                }
                if first_frame:
                    submit_body["first_frame_image"] = first_frame
                resp = await client.post(
                    f"{self.api_base}/video_generation",
                    headers=self._headers(),
                    json=submit_body,
                )
            except httpx.TimeoutException:
                return self._fail("PROVIDER_TIMEOUT", "Minimax 提交超时")
            if err := self._classify(resp.status_code):
                return err
            submit = resp.json()
            remote_task_id = (submit.get("data") or {}).get("task_id") or submit.get("task_id")
            if not remote_task_id:
                return {
                    "status": "failed",
                    "error": submit.get("base_resp", {}).get("status_msg", "提交失败"),
                    "error_code": "PROVIDER_SUBMIT_FAILED",
                    "error_hint": "请检查模型与参数",
                }

            # 轮询（封顶）
            waited = 0.0
            while waited < self.max_wait:
                await asyncio.sleep(self.poll_interval)
                waited = time.monotonic() - started
                if on_event:
                    on_event(
                        "phase",
                        {"phase": "polling", "message": f"视频生成中（已等待 {int(waited)}s）"},
                    )
                q = await client.get(
                    f"{self.api_base}/query/video_generation",
                    headers=self._headers(),
                    params={"task_id": remote_task_id},
                )
                if q.status_code >= 500:
                    continue
                if err := self._classify(q.status_code):
                    return err
                body = q.json()
                data = body.get("data") or {}
                # MiniMax 实际响应：status/file_id 在顶层（非嵌套在 data 内）
                status = data.get("status") or body.get("status")
                if status == "Success":
                    file_id = data.get("file_id") or body.get("file_id")
                    url = data.get("download_url") or data.get("file_url")
                    if not url and file_id:
                        fb = await client.get(
                            f"{self.api_base}/files/retrieve",
                            headers=self._headers(),
                            params={"file_id": file_id},
                        )
                        url = (fb.json().get("file") or {}).get("download_url")
                    if url:
                        # 视频落盘本地（MiniMax URL 24h 过期，产物需持久化）
                        local_url = await self._download_video(url, task)
                        return {"status": "completed", "output_url": local_url or url}
                    return self._fail(
                        "PROVIDER_EMPTY_RESULT",
                        "视频完成但未取得下载地址",
                        "渠道可能已变更响应格式",
                    )
                if status == "Fail":
                    err_msg = (
                        body.get("base_resp", {}).get("status_msg")
                        or "视频生成失败"
                    )
                    return self._fail("PROVIDER_SUBMIT_FAILED", f"视频生成失败: {err_msg}")
            return self._fail("PROVIDER_TIMEOUT", f"视频生成超时（>{self.max_wait}s）")

    async def _download_video(self, url: str, task: Any) -> str | None:
        """下载远端视频落盘 outputs/videos/（MiniMax URL 24h 过期）。"""
        from uuid import uuid4

        try:
            async with httpx.AsyncClient(timeout=300, follow_redirects=True) as dl:
                resp = await dl.get(url)
                resp.raise_for_status()
            out_dir = Path("outputs/videos")
            out_dir.mkdir(parents=True, exist_ok=True)
            name = f"video_{task.id}_{uuid4().hex[:10]}.mp4"
            (out_dir / name).write_bytes(resp.content)
            return f"/outputs/videos/{name}"
        except Exception as e:  # noqa: BLE001 — 下载失败兜底返回临时 URL（不能因落盘阻断任务完成）
            logger.warning("video download failed: %s %s", type(e).__name__, url[:80])
            return None

    def _fail(self, code: str, message: str, hint: str = "请稍后重试或更换渠道") -> dict:
        return {
            "status": "failed",
            "error": message,
            "error_code": code,
            "error_hint": hint,
        }


class MinimaxImageProvider(ProviderBase):
    """Minimax 图片生成（image-01，同步返回 base64）。

    derived from creagen prod services/minimax_client.py generate_image。
    """

    domain = "image"

    def __init__(
        self, model: str, channel: Any = None, key_plain: str = "", base_url: str = ""
    ) -> None:
        self.model = model or "image-01"
        self.channel = channel
        self.key_plain = key_plain
        self.api_base = "https://api.minimaxi.com/v1"

    async def test_connection(self) -> bool:
        return True

    async def list_models(self) -> list[str]:
        return ["image-01"]

    async def run(self, task: Any, on_event: Any = None) -> dict:  # noqa: ANN401
        params = task.params_dict() if hasattr(task, "params_dict") else {}
        payload: dict[str, Any] = {
            "model": "image-01",
            "prompt": task.prompt,
            "n": 1,
            "prompt_optimizer": bool(params.get("prompt_optimizer", True)),
        }
        aspect = params.get("aspect_ratio")
        if aspect and aspect != "1:1":
            payload["aspect_ratio"] = aspect

        async with httpx.AsyncClient(timeout=120) as client:
            try:
                resp = await client.post(
                    f"{self.api_base}/image_generation",
                    headers={
                        "Authorization": f"Bearer {self.key_plain}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
            except httpx.TimeoutException:
                return {
                    "status": "failed",
                    "error": "Minimax 图片生成超时",
                    "error_code": "PROVIDER_TIMEOUT",
                }
            if resp.status_code >= 400:
                return {
                    "status": "failed",
                    "error": f"Minimax 返回 {resp.status_code}",
                    "error_code": "PROVIDER_SERVER_ERROR",
                }
            data = resp.json()
            base_resp = data.get("base_resp", {})
            if base_resp.get("status_code") not in (0, None):
                return {
                    "status": "failed",
                    "error": base_resp.get("status_msg", "Minimax 错误"),
                    "error_code": "PROVIDER_SUBMIT_FAILED",
                }
            images = (data.get("data") or {}).get("images") or []
            b64 = images[0].get("image") if images and isinstance(images[0], dict) else None
            if not b64:
                return {
                    "status": "failed",
                    "error": "Minimax 未返回图片数据",
                    "error_code": "PROVIDER_EMPTY_RESULT",
                }

            # base64 落盘（复用 outputs 托管）
            import base64 as _b64

            out_dir = Path("outputs/images")
            out_dir.mkdir(parents=True, exist_ok=True)
            raw = _b64.b64decode(b64)
            from uuid import uuid4 as _uuid4

            name = f"minimax_{task.id}_{_uuid4().hex[:10]}.png"
            (out_dir / name).write_bytes(raw)
            return {"status": "completed", "output_url": f"/outputs/images/{name}"}
