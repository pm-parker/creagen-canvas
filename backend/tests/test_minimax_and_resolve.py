"""渠道解析与 Minimax 轮询契约（Review Focus #4/#5）。"""

import asyncio

import httpx
import pytest
import respx

from backend.app.providers.minimax_video import MinimaxVideoProvider
from backend.app.services.provider_service import resolve_provider_for_task

API = "https://api.minimaxi.com/v1"


def test_resolve_skips_disabled_channel(db_session, monkeypatch):
    """Review Focus #5：禁用渠道参与解析被跳过。"""
    monkeypatch.setenv("ENABLE_FAKE_PROVIDER", "false")
    from backend.app.core.config import get_settings

    get_settings.cache_clear()
    from backend.app.models import ProviderChannel, User

    try:
        user = User(username="u", email="u@x.io", password_hash="x")
        db_session.add(user)
        db_session.commit()
        ch = ProviderChannel(
            user_id=user.id,
            domain="llm",
            name="off",
            base_url="https://x",
            key_encrypted="v1:xxx",
            enabled=0,
        )
        db_session.add(ch)
        db_session.commit()

        with pytest.raises(Exception) as e:
            resolve_provider_for_task(db_session, user.id, "llm", "any")
        assert getattr(e.value, "code", "") == "PROVIDER_NOT_CONFIGURED"
    finally:
        get_settings.cache_clear()


def test_resolve_enabled_channel_returns_provider_with_key(db_session, monkeypatch):
    monkeypatch.setenv("ENABLE_FAKE_PROVIDER", "false")
    from backend.app.core.config import get_settings

    get_settings.cache_clear()
    from backend.app.core.crypto import encrypt_secret
    from backend.app.models import ProviderChannel, User

    try:
        user = User(username="u2", email="u2@x.io", password_hash="x")
        db_session.add(user)
        db_session.commit()
        ch = ProviderChannel(
            user_id=user.id,
            domain="llm",
            name="on",
            base_url="https://api.example.com/v1",
            key_encrypted=encrypt_secret("sk-test-xyz"),
            enabled=1,
        )
        db_session.add(ch)
        db_session.commit()

        provider, channel = resolve_provider_for_task(db_session, user.id, "llm", "gpt-x")
        assert channel is not None and channel.id == ch.id
        assert provider.key_plain == "sk-test-xyz"
    finally:
        get_settings.cache_clear()


@respx.mock
def test_minimax_poll_success_after_retries():
    """Review Focus #4：轮询至 Success；running 状态持续上报 phase。"""
    respx.post(f"{API}/video_generation").mock(
        return_value=httpx.Response(200, json={"data": {"task_id": "mt-1"}})
    )
    route = respx.get(f"{API}/query/video_generation")
    states = iter(["Processing", "Processing", "Success"])
    route.mock(
        side_effect=lambda req: httpx.Response(
            200,
            json={
                "data": {"status": next(states), "file_id": 9, "download_url": "https://cdn/v.mp4"}
            },
        )
    )

    phases: list[str] = []
    p = MinimaxVideoProvider(model="m", key_plain="k", poll_interval=0, max_wait=30)
    result = asyncio.run(p.run(_T(), on_event=lambda et, d: phases.append(d.get("phase", ""))))

    assert result["status"] == "completed"
    # 两种合法结果：落盘本地（生产）或兜底远端 URL（下载失败/mock 环境下）
    assert result["output_url"].startswith("/outputs/videos/") or result["output_url"] == "https://cdn/v.mp4"
    assert "polling" in phases


@respx.mock
def test_minimax_poll_timeout_caps():
    """轮询封顶：max_wait 到期 failed，不无限等待。"""
    respx.post(f"{API}/video_generation").mock(
        return_value=httpx.Response(200, json={"data": {"task_id": "mt-2"}})
    )
    respx.get(f"{API}/query/video_generation").mock(
        return_value=httpx.Response(200, json={"data": {"status": "Processing"}})
    )

    p = MinimaxVideoProvider(model="m", key_plain="k", poll_interval=0, max_wait=0)
    result = asyncio.run(p.run(_T()))
    assert result["status"] == "failed"
    assert result["error_code"] == "PROVIDER_TIMEOUT"


class _T:
    id = 1
    prompt = "一只奔跑的猫"
    type = "video"

    def __init__(self, params: dict | None = None):
        self._params = params or {}

    def params_dict(self):
        return self._params


@respx.mock
def test_minimax_submit_passes_duration_and_resolution():
    """BUG-C 回归：UI 选择的时长/分辨率必须进入提交 body（生产 minimax_client 契约：
    duration ∈ {6,10}，resolution 大写 P；非法值回落 6/768P）。"""
    submitted: dict = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        import json

        submitted.update(json.loads(request.content))
        return httpx.Response(200, json={"data": {"task_id": "mt-c1"}})

    respx.post(f"{API}/video_generation").mock(side_effect=_capture)
    respx.get(f"{API}/query/video_generation").mock(
        return_value=httpx.Response(200, json={"data": {"status": "Fail"}})
    )

    p = MinimaxVideoProvider(model="m", key_plain="k", poll_interval=0, max_wait=5)
    asyncio.run(p.run(_T({"duration": 10, "resolution": "1080p"})))
    assert submitted.get("duration") == 10
    assert submitted.get("resolution") == "1080P"

    # 非法时长（如旧 UI 的 5s）回落 API 默认 6s，不 400
    submitted.clear()
    asyncio.run(p.run(_T({"duration": 5})))
    assert submitted.get("duration") == 6
    assert submitted.get("resolution") == "768P"


@respx.mock
def test_minimax_submit_passes_first_frame_image(respx_mock, tmp_path, monkeypatch):
    """i2v：reference_urls[0] 作为首帧图透传——本地 /outputs 转 data URL。"""
    from backend.app.providers.minimax_video import MinimaxVideoProvider

    (tmp_path / "outputs" / "images").mkdir(parents=True)
    (tmp_path / "outputs" / "images" / "first.png").write_bytes(b"FIRSTFRAME")
    monkeypatch.chdir(tmp_path)

    submitted: dict = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        import json

        submitted.update(json.loads(request.content))
        return httpx.Response(200, json={"data": {"task_id": "mt-i2v"}})

    respx_mock.post(f"{API}/video_generation").mock(side_effect=_capture)
    respx_mock.get(f"{API}/query/video_generation").mock(
        return_value=httpx.Response(200, json={"data": {"status": "Fail"}})
    )

    p = MinimaxVideoProvider(model="MiniMax-Hailuo-2.3", key_plain="k", poll_interval=0, max_wait=5)
    asyncio.run(
        p.run(_T({"reference_urls": ["/outputs/images/first.png"], "duration": 6}))
    )

    import base64

    ffi = submitted.get("first_frame_image", "")
    assert ffi.startswith("data:image/"), submitted
    assert base64.b64decode(ffi.split(",", 1)[1]) == b"FIRSTFRAME"
