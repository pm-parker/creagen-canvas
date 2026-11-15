"""本地 MiniMax H3 Video Provider（asus 4090 · OpenAI 兼容 chat→video 适配器）。

协议（2026-10-02 实测）：
- POST {base_url}/v1/chat/completions，messages[0].content = 视频 prompt
- i2v：顶层 image 字段传 URL（PUBLIC_BASE_URL + /outputs 路径，asus 需可达本服务）
- 同步阻塞返回（t2v ~60s / i2v ~5min / 720p 上限 ~15min）→ 超时 1200s
- 响应 content 含产物 URL → GET 下载 mp4 到 outputs/videos/

无轮询、无额度概念：4090 单卡串行，排队在适配器侧。
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any

import httpx

from backend.app.providers.registry import ProviderBase

logger = logging.getLogger("canvas.provider.h3local")

H3_TIMEOUT = httpx.Timeout(1200.0, connect=10.0)  # 本地生成 1~15 分钟
_URL_RE = re.compile(r"https?://\S+/v1/generations/\S+")

ENV_BASE = "H3_BASE_URL"  # 缺省 http://localhost:8006


def _default_base() -> str:
    import os

    return os.environ.get(ENV_BASE, "http://localhost:8006").rstrip("/")


def _public_base() -> str:
    """本服务对外基址（worker 侧把 /outputs 图转成 asus 可达的 URL）。"""
    import os

    return os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")


class H3LocalVideoProvider(ProviderBase):
    domain = "video"

    def __init__(
        self,
        model: str,
        channel: Any = None,  # noqa: ANN401
        key_plain: str = "",
        base_url: str = "",
        outputs_dir: Path | None = None,
    ) -> None:
        self.model = model
        self.channel = channel
        self.key_plain = key_plain
        raw = (base_url or _default_base()).rstrip("/")
        self.base_url = raw[: -3] if raw.endswith("/v1") else raw  # 兼容渠道配 /v1 后缀
        self.outputs_dir = outputs_dir or Path("outputs/videos")

    async def test_connection(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(
                    f"{self.base_url}/v1/models",
                    headers=self._headers(),
                )
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def list_models(self) -> list[str]:
        return ["minimax-h3-video"]

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.key_plain or 'local'}",
                "Content-Type": "application/json"}

    def _fail(self, code: str, error: str, hint: str = "") -> dict:
        out = {"status": "failed", "error": error, "error_code": code}
        if hint:
            out["error_hint"] = hint
        return out

    def _classify(self, status: int) -> dict | None:
        if status == 401:
            return self._fail("CHANNEL_KEY_INVALID", "本地 H3 服务密钥无效")
        if status == 404:
            return self._fail("H3_ENDPOINT_NOT_FOUND", "本地 H3 接口不存在", "检查渠道 base_url")
        if status == 429:
            return self._fail("PROVIDER_RATE_LIMITED", "本地 GPU 忙（单卡串行）", "稍后重试")
        if status >= 500:
            return self._fail("PROVIDER_SERVER_ERROR", f"本地 H3 服务错误 HTTP {status}")
        return None

    # 单卡并发容量：15s i2v 独跑 13min；双并发各 >20min 必撞超时线（2026-10-08
    # 两批任务实证：串行全活，双并发全灭+GPU 白烧）。闸门限 1 = 最高吞吐零超时。
    gate_limit = 1

    async def cancel_remote_job(self, task_id: int, *, interrupt: bool = False) -> bool:
        """撤销 shim/ComfyUI 侧 job（僵尸根治）：精确删 pending，interrupt 仅收尸场景。

        经 shim 的 DELETE /internal/jobs/{task_id}；shim 重启丢映射时幂等返回。
        短超时（5s）——撤销是尽力而为的清理，不阻塞主流程。
        """
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.delete(f"{self.base_url}/internal/jobs/{task_id}",
                                        params={"interrupt": str(bool(interrupt)).lower()})
                return r.status_code == 200
        except httpx.HTTPError:
            logger.warning("h3-local cancel_remote_job(%s) unreachable", task_id)
            return False

    async def run(self, task: Any, on_event: Any = None) -> dict:  # noqa: ANN401
        params = task.params_dict() if hasattr(task, "params_dict") else {}

        # i2v 首帧：上游图片节点 reference_urls[0] → 公网 URL
        image_url: str | None = None
        refs = params.get("reference_urls") or []
        if isinstance(refs, list) and refs:
            r0 = str(refs[0])
            base = _public_base()
            if r0.startswith(("http://", "https://")):
                image_url = r0
            elif r0.startswith("/outputs/") and base:
                image_url = base + r0
            elif r0.startswith("/outputs/"):
                return self._fail(
                    "PUBLIC_BASE_URL_MISSING",
                    "图生视频需要配置 PUBLIC_BASE_URL（GPU 机器需能访问本服务）",
                    "在 .env 设置 PUBLIC_BASE_URL=http://<本机IP>:18080",
                )

        # fl2v 尾帧 (2026-10-18 打通): params.last_frame_url → shim last_image
        # 支持形态: 完整 URL 或 /outputs/ 站内路径 (自动拼 PUBLIC_BASE_URL)
        last_url: str | None = None
        lf = params.get("last_frame_url")
        if lf:
            lf = str(lf)
            base_lf = _public_base()
            if lf.startswith(("http://", "https://")):
                last_url = lf
            elif lf.startswith("/outputs/") and base_lf:
                last_url = base_lf + lf

        body: dict = {
            "model": self.model,
            "messages": [{"role": "user", "content": task.prompt}],
        }
        # 期望时长透传（shim 3-15s clamp；画布/分镜可设 5/10s）
        try:
            _dur = int(params.get("duration") or 0)
        except (TypeError, ValueError):
            _dur = 0
        if _dur > 0:
            body["duration"] = _dur
        if image_url:
            body["image"] = image_url
        if last_url:
            if not image_url:
                return self._fail(
                    "FL2V_NEED_FIRST_FRAME",
                    "首尾帧模式需要同时提供首帧 (reference_urls[0]) 和尾帧 (last_frame_url)",
                    "检查任务参数",
                )
            body["last_image"] = last_url

        if on_event:
            on_event("phase", {"phase": "generating"})

        started = time.monotonic()
        async with httpx.AsyncClient(timeout=H3_TIMEOUT) as client:
            try:
                resp = await client.post(
                    f"{self.base_url}/v1/chat/completions",
                    headers={**self._headers(), "X-Creagen-Task-Id": str(task.id)},
                    json=body,
                )
            except httpx.TimeoutException:
                return self._fail(
                    "PROVIDER_TIMEOUT",
                    "本地 H3 生成超时（>20 分钟）",
                    "降低分辨率/时长后重试",
                )
            except httpx.HTTPError as e:
                name = type(e).__name__
                return self._fail("PROVIDER_FETCH_FAILED", f"本地 H3 服务不可达: {name}")

            if err := self._classify(resp.status_code):
                return err
            try:
                data = resp.json()
            except ValueError:
                return self._fail("PROVIDER_BAD_RESPONSE", "本地 H3 返回非 JSON")

            content = str((data.get("choices") or [{}])[0].get("message", {}).get("content", ""))
            m = _URL_RE.search(content)
            if not m:
                logger.warning(
                    "h3-local unexpected response: status=%s body=%s",
                    resp.status_code, str(data)[:500],
                )
                # 适配器把失败原因写进 content（"生成失败：..."）
                return self._fail("H3_GENERATION_FAILED", content.strip()[:300] or "未返回产物链接")

            # 下载产物
            video_url = m.group(0)
            try:
                dl = await client.get(video_url)
            except httpx.HTTPError as e:
                return self._fail("VIDEO_DOWNLOAD_FAILED", f"产物下载失败: {type(e).__name__}")
            if dl.status_code != 200:
                return self._fail("VIDEO_DOWNLOAD_FAILED", f"产物下载 HTTP {dl.status_code}")

            out_dir = self.outputs_dir
            out_dir.mkdir(parents=True, exist_ok=True)
            name = f"vid_{task.id}_{int(time.time())}.mp4"
            (out_dir / name).write_bytes(dl.content)

        logger.info("h3-local task %s done in %.0fs", task.id, time.monotonic() - started)
        return {"status": "completed", "output_url": f"/outputs/videos/{name}", "cost_amount": 0.0}
