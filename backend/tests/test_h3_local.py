"""本地 MiniMax H3 Provider（asus 4090 · OpenAI 兼容 chat→video 适配器）。

协议（2026-10-02 实测）：
- POST {base}/v1/chat/completions，messages[0].content = 视频 prompt
- i2v：顶层 image 字段传 URL（asus 需可达本服务 /outputs/）
- 同步阻塞返回（t2v ~60s / i2v ~5min），content 含产物 URL
- GET {base}/v1/generations/{id}/video 下载 mp4
"""

REGISTER = {
    "username": "h3user",
    "email": "h3@x.io",
    "password": "password123",
    "invite_code": "letmein",
}


def _headers(client):
    client.post("/api/auth/register", json=REGISTER)
    r = client.post(
        "/api/auth/login", json={"identifier": "h3user", "password": "password123"}
    )
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _fake_task(prompt="一只猫跳舞", params=None):
    from types import SimpleNamespace

    return SimpleNamespace(
        id=1, prompt=prompt, negative_prompt="",
        params_dict=lambda: params or {},
    )


def _ok_response(video_url="http://gpu:8006/v1/generations/abc/video"):
    import httpx

    content = f"视频已生成：{video_url}\nSHA256：deadbeef"
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-x", "object": "chat.completion", "model": "minimax-h3-video",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                         "finish_reason": "stop"}],
        },
        request=httpx.Request("POST", "http://gpu:8006/v1/chat/completions"),
    )


class TestH3LocalProvider:
    def _provider(self, tmp_path, monkeypatch, base="http://gpu:8006"):
        monkeypatch.setenv("H3_BASE_URL", base)
        monkeypatch.setenv("PUBLIC_BASE_URL", "http://canvas:18080")
        from backend.app.providers.minimax_h3_local import H3LocalVideoProvider

        return H3LocalVideoProvider(
            model="minimax-h3-video", channel=None, key_plain="local",
            base_url=base, outputs_dir=tmp_path,
        )

    def test_t2v_completes_and_downloads(self, tmp_path, monkeypatch):
        import httpx

        p = self._provider(tmp_path, monkeypatch)

        async def fake_post(_self, url, **kw):  # noqa: ANN001
            assert kw["json"]["messages"][0]["content"] == "一只猫跳舞"
            assert "image" not in kw["json"]
            return _ok_response()

        async def fake_get(_self, url, **kw):  # noqa: ANN001
            return httpx.Response(200, content=b"FAKE_MP4",
                                  request=httpx.Request("GET", url))

        with monkeypatch.context() as m:
            m.setattr(httpx.AsyncClient, "post", fake_post)
            m.setattr(httpx.AsyncClient, "get", fake_get)
            import asyncio

            result = asyncio.run(p.run(_fake_task()))
        assert result["status"] == "completed"
        assert result["output_url"].startswith("/outputs/videos/")
        out = tmp_path / Path(result["output_url"]).name
        assert out.read_bytes() == b"FAKE_MP4"

    def test_i2v_image_field_is_public_url(self, tmp_path, monkeypatch):
        import httpx

        p = self._provider(tmp_path, monkeypatch)
        captured = {}

        async def fake_post(_self, url, **kw):  # noqa: ANN001
            captured.update(kw["json"])
            return _ok_response()

        async def fake_get(_self, url, **kw):  # noqa: ANN001
            return httpx.Response(200, content=b"MP4",
                                  request=httpx.Request("GET", url))

        with monkeypatch.context() as m:
            m.setattr(httpx.AsyncClient, "post", fake_post)
            m.setattr(httpx.AsyncClient, "get", fake_get)
            import asyncio

            task = _fake_task(params={"reference_urls": ["/outputs/images/cat.png"]})
            result = asyncio.run(p.run(task))
        assert result["status"] == "completed"
        # 本地路径转公网 URL（asus 可达）
        assert captured["image"] == "http://canvas:18080/outputs/images/cat.png"

    def test_content_without_url_fails(self, tmp_path, monkeypatch):
        import httpx

        p = self._provider(tmp_path, monkeypatch)

        async def fake_post(_self, url, **kw):  # noqa: ANN001
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "生成失败：显存不足"}}]},
                request=httpx.Request("POST", url),
            )

        with monkeypatch.context() as m:
            m.setattr(httpx.AsyncClient, "post", fake_post)
            import asyncio

            result = asyncio.run(p.run(_fake_task()))
        assert result["status"] == "failed"
        assert "显存不足" in result["error"]

    def test_timeout_classified(self, tmp_path, monkeypatch):
        import httpx

        p = self._provider(tmp_path, monkeypatch)

        async def fake_post(_self, url, **kw):  # noqa: ANN001
            raise httpx.ReadTimeout("boom")

        with monkeypatch.context() as m:
            m.setattr(httpx.AsyncClient, "post", fake_post)
            import asyncio

            result = asyncio.run(p.run(_fake_task()))
        assert result["status"] == "failed"
        assert result["error_code"] == "PROVIDER_TIMEOUT"


from pathlib import Path  # noqa: E402
