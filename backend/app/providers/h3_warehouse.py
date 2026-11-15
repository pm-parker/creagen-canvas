"""算力仓 H3 原生接入（独立 Provider）：/v1/videos 异步协议。

调研实证（2026-10-07，本地算力仓实测）：
- 原生端点 POST /v1/videos {prompt, profile, image?, seed?, nfe?} → 202 任务对象
- 轮询 GET /v1/generations/{id}（queued→running→succeeded/failed，含 progress）
- 下载 GET /v1/generations/{id}/video
- **image 仅收 data URL**（http URL 报 UnidentifiedImageError/Incorrect padding）
- 原生 i2v SSIM 0.960 ✓；chat 端点 i2v 静默丢弃（0.150）——弃用 chat 端点
- profile 档位：640x384-5s · 832x480-5s · 1024x576-5s · 480x832-5s（竖屏）· 640x384-10s
- 诚实性纪律（2026-10-07 事故教训）：任何失败显式上报，禁止静默降级
"""

from __future__ import annotations

import asyncio
import base64
import os
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from backend.app.core.timeouts import H3W_DEADLINE_S as DEADLINE_S  # noqa: F401,N812
from backend.app.core.timeouts import H3W_DOWNLOAD_TIMEOUT_S
from backend.app.providers.registry import ProviderBase

# 本地 /outputs 根（测试可 monkeypatch）
OUTPUTS_ROOT = Path("outputs")

# 本地算力仓端点：环境变量 H3_BASE_URL 覆盖（缺省本机 8006）
DEFAULT_BASE = "http://localhost:8006"


def _default_base() -> str:
    return os.environ.get("H3_BASE_URL", DEFAULT_BASE).rstrip("/")

# 支持档位（算力仓硬约束，报错原文）
PROFILES_5S = ("640x384", "832x480", "1024x576", "480x832")
DEFAULT_5S = "1024x576"  # 5s 默认横屏高清
PROFILE_10S = "640x384"  # 10s 仅此档

POLL_INTERVAL_S = 5


def resolve_profile(params: dict[str, Any]) -> str:
    """duration/resolution → 算力仓 profile 档位。

    5s：分辨率透传（含竖屏 480x832），未知回落 1024x576；
    10s：仅 640x384（忽略分辨率请求）。
    """
    try:
        duration = int(params.get("duration") or 5)
    except (TypeError, ValueError):
        duration = 5
    if duration >= 10:
        return f"{PROFILE_10S}-10s"
    res = str(params.get("resolution") or "").strip()
    return f"{res if res in PROFILES_5S else DEFAULT_5S}-5s"


def _ref_to_data_url(ref_path: str) -> str:
    """本地 /outputs 参考图 → data URL（原生端点只认 base64）。失败显式抛错。"""
    local = OUTPUTS_ROOT / ref_path[len("/outputs/"):] if ref_path.startswith("/outputs/") else None
    if local is None or not local.is_file():
        raise FileNotFoundError(f"参考图不存在: {ref_path}")
    return "data:image/png;base64," + base64.b64encode(local.read_bytes()).decode()


class H3WarehouseProvider(ProviderBase):
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
        self.base_url = raw[: -3] if raw.endswith("/v1") else raw
        self.outputs_dir = outputs_dir or Path("outputs/videos")

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.key_plain}"} if self.key_plain else {}

    async def test_connection(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(f"{self.base_url}/health")
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def list_models(self) -> list[str]:
        return ["minimax-h3-video"]

    def _fail(self, code: str, message: str, hint: str = "请稍后重试") -> dict:
        return {"status": "failed", "error": message, "error_code": code, "error_hint": hint}

    async def run(self, task: Any, on_event: Any = None) -> dict:  # noqa: ANN401
        params = task.params_dict() if hasattr(task, "params_dict") else {}
        profile = resolve_profile(params)

        body: dict[str, Any] = {"prompt": task.prompt, "profile": profile}
        # seed/nfe 透传（算力仓原生参数面；seed=0 为合法 falsy 值）
        for key in ("seed", "nfe"):
            v = params.get(key)
            if v is not None:
                body[key] = v

        refs = params.get("reference_urls") or []
        if isinstance(refs, list) and refs:
            try:
                body["image"] = _ref_to_data_url(str(refs[0]))
            except (FileNotFoundError, OSError) as e:
                # 诚实性纪律：参考图缺失直接失败，禁止静默降级 t2v
                return self._fail(
                    "REFERENCE_MISSING", f"参考图不可用: {e}", "请重新生成参考图后重试"
                )

        if on_event:
            on_event("phase", {"phase": "generating"})

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{self.base_url}/v1/videos",
                    headers=self._headers(),
                    json=body,
                )
                if resp.status_code not in (200, 202):
                    detail = ""
                    try:
                        detail = resp.json().get("error", {}).get("message", "")
                    except (ValueError, AttributeError):
                        detail = resp.text[:120]
                    return self._fail(
                        "H3W_SUBMIT_FAILED",
                        f"算力仓提交失败: {detail or resp.status_code}",
                        "请检查 profile 参数或稍后重试",
                    )
                job = resp.json()
                job_id = job.get("id")
                if not job_id:
                    return self._fail("H3W_SUBMIT_FAILED", "算力仓未返回任务 id")

                # 轮询至终态
                deadline = time.monotonic() + DEADLINE_S
                while time.monotonic() < deadline:
                    if self._check_cancelled():
                        return self._fail(
                            "H3W_CANCELLED", "任务已取消（协作退出，远端 job 无撤销 API）"
                        )
                    await asyncio.sleep(POLL_INTERVAL_S)
                    st = await client.get(
                        f"{self.base_url}/v1/generations/{job_id}", headers=self._headers()
                    )
                    if st.status_code != 200:
                        continue  # 瞬时查询失败容忍重试
                    data = st.json()
                    status = data.get("status")
                    if status == "succeeded":
                        video_url = (data.get("result") or {}).get("video_url")
                        if not video_url:
                            return self._fail(
                                "H3W_NO_OUTPUT", "算力仓任务成功但未返回产物 URL"
                            )
                        # 下载独立 client：几十 MB 视频，30s 轮询超时不够
                        async with __import__("httpx").AsyncClient(
                            timeout=H3W_DOWNLOAD_TIMEOUT_S
                        ) as dl_client:
                            dl = await dl_client.get(video_url)
                        if dl.status_code != 200:
                            return self._fail(
                                "H3W_DOWNLOAD_FAILED",
                                f"产物下载失败 HTTP {dl.status_code}",
                            )
                        self.outputs_dir.mkdir(parents=True, exist_ok=True)
                        path = self.outputs_dir / f"vid_{task.id}_{uuid4().hex[:10]}.mp4"
                        path.write_bytes(dl.content)
                        return {
                            "status": "completed",
                            "output_url": f"/outputs/videos/{path.name}",
                            "meta": {
                                "duration": data.get("duration_seconds"),
                                "provider": "h3_warehouse",
                            },
                        }
                    if status == "failed":
                        err = str(data.get("error") or data.get("message") or "生成失败")
                        return self._fail("H3W_GENERATION_FAILED", f"算力仓: {err}")
                    if on_event and isinstance(data.get("progress"), (int, float)):
                        on_event("phase", {"phase": "generating", "progress": data["progress"]})
                return self._fail(
                    "H3W_TIMEOUT",
                    f"算力仓生成超时（{DEADLINE_S // 60} 分钟）",
                    "队列可能拥堵，请稍后重试",
                )
        except httpx.TimeoutException:
            return self._fail("H3W_TIMEOUT", "算力仓请求超时", "请稍后重试")
        except httpx.HTTPError as e:
            return self._fail(
                "H3W_UNREACHABLE", f"算力仓连接失败: {type(e).__name__}", "请检查网络与算力仓状态"
            )
