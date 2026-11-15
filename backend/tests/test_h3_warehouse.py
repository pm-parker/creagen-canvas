"""算力仓 H3 原生接入（/v1/videos 异步协议）：独立 Provider。

调研实证（2026-10-07）：原生端点 i2v SSIM 0.960（image 仅收 data URL）；
profile 档位 640x384-5s/832x480-5s/1024x576-5s/480x832-5s/640x384-10s；
chat 端点 i2v 静默丢弃（SSIM 0.150）——弃用。
"""

import asyncio
import json as _json
import os

import httpx

from backend.app.providers.h3_warehouse import H3WarehouseProvider, resolve_profile

BASE = os.environ.get("H3_BASE_URL", "http://localhost:8006")


def _provider(**kw):
    return H3WarehouseProvider(model="minimax-h3-video", base_url=BASE, **kw)


class _T:
    id = 777
    type = "video"
    prompt = "a fox in bamboo forest"

    def __init__(self, params=None):
        self._p = params or {}

    def params_dict(self):
        return self._p


# ---------- profile 映射 ----------


def test_profile_mapping_defaults():
    assert resolve_profile({"duration": 5}) == "1024x576-5s"  # 5s 默认横屏高清
    assert resolve_profile({}) == "1024x576-5s"
    assert resolve_profile({"duration": 10}) == "640x384-10s"  # 10s 仅此档
    assert resolve_profile({"duration": 5, "resolution": "480x832"}) == "480x832-5s"  # 竖屏
    assert resolve_profile({"duration": 5, "resolution": "640x384"}) == "640x384-5s"
    # 非法组合/未知分辨率回落安全档
    assert resolve_profile({"duration": 10, "resolution": "480x832"}) == "640x384-10s"
    assert resolve_profile({"duration": 5, "resolution": "bogus"}) == "1024x576-5s"


# ---------- 提交/轮询/下载全链 ----------


def test_run_t2v_full_cycle(respx_mock, tmp_path):
    submit = respx_mock.post(f"{BASE}/v1/videos").mock(
        return_value=httpx.Response(
            202, json={"id": "job1", "status": "queued", "object": "video.generation"}
        )
    )
    states = iter(
        [
            httpx.Response(200, json={"id": "job1", "status": "running", "progress": 50}),
            httpx.Response(200, json={"id": "job1", "status": "succeeded", "result": {
                "video_url": f"{BASE}/v1/generations/job1/video", "content_type": "video/mp4",
            }}),
        ]
    )
    respx_mock.get(f"{BASE}/v1/generations/job1").mock(side_effect=lambda req: next(states))
    respx_mock.get(f"{BASE}/v1/generations/job1/video").mock(
        return_value=httpx.Response(200, content=b"FAKE_MP4_BYTES")
    )
    p = _provider(outputs_dir=tmp_path)
    result = asyncio.run(p.run(_T()))
    assert result["status"] == "completed"
    assert result["output_url"].startswith("/outputs/videos/")
    out = tmp_path / result["output_url"].rsplit("/", 1)[1]
    assert out.read_bytes() == b"FAKE_MP4_BYTES"
    body = _json.loads(submit.calls.last.request.content)
    assert body["profile"] == "1024x576-5s"
    assert "image" not in body


def test_run_i2v_converts_local_ref_to_data_url(respx_mock, tmp_path, monkeypatch):
    """i2v：本地 /outputs 参考图读出转 data URL（原生端点只认 base64）。"""
    ref = tmp_path / "ref.png"
    ref.write_bytes(b"\x89PNG-fake")
    monkeypatch.setattr("backend.app.providers.h3_warehouse.OUTPUTS_ROOT", tmp_path)
    submit = respx_mock.post(f"{BASE}/v1/videos").mock(
        return_value=httpx.Response(202, json={"id": "job2", "status": "queued"})
    )
    respx_mock.get(f"{BASE}/v1/generations/job2").mock(
        return_value=httpx.Response(200, json={"id": "job2", "status": "succeeded", "result": {
            "video_url": f"{BASE}/v1/generations/job2/video"
        }})
    )
    respx_mock.get(f"{BASE}/v1/generations/job2/video").mock(
        return_value=httpx.Response(200, content=b"V2")
    )
    p = _provider(outputs_dir=tmp_path)
    result = asyncio.run(p.run(_T({"duration": 10, "reference_urls": ["/outputs/ref.png"]})))
    assert result["status"] == "completed"
    body = _json.loads(submit.calls.last.request.content)
    assert body["image"].startswith("data:image/png;base64,")
    assert body["profile"] == "640x384-10s"


def test_run_failure_is_honest(respx_mock, tmp_path):
    """失败必须诚实带回算力仓的错误信息（禁止静默降级）。"""
    respx_mock.post(f"{BASE}/v1/videos").mock(
        return_value=httpx.Response(202, json={"id": "job3", "status": "queued"})
    )
    respx_mock.get(f"{BASE}/v1/generations/job3").mock(
        return_value=httpx.Response(
            200, json={"id": "job3", "status": "failed", "error": "UnidentifiedImageError"}
        )
    )
    p = _provider(outputs_dir=tmp_path)
    result = asyncio.run(p.run(_T()))
    assert result["status"] == "failed"
    assert "UnidentifiedImageError" in result["error"]


def test_run_submit_rejected(respx_mock, tmp_path):
    respx_mock.post(f"{BASE}/v1/videos").mock(
        return_value=httpx.Response(400, json={"error": {"message": "profile 仅支持 ..."}})
    )
    p = _provider(outputs_dir=tmp_path)
    result = asyncio.run(p.run(_T()))
    assert result["status"] == "failed"
    assert "profile" in result["error"]


# ---------- 渠道路由（capabilities 标记 → 独立接入） ----------


def test_build_provider_routes_by_capability(db_session):
    from backend.app.models import ProviderChannel
    from backend.app.providers.h3_warehouse import H3WarehouseProvider
    from backend.app.services.provider_service import _build_provider

    ch = ProviderChannel(
        domain="video", name="算力仓 H3", base_url="http://gpu-pool.internal:8006/v1",
        key_encrypted="", capabilities='["native_video_api"]',
    )
    db_session.add(ch)
    db_session.flush()
    p = _build_provider("video", "minimax-h3-video", ch, "")
    assert isinstance(p, H3WarehouseProvider)
