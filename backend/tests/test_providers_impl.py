"""Provider 真实实现契约：OpenAI 兼容三域 + 错误分类 + key 不进日志（Review Focus #1）。"""

import asyncio as _asyncio
import json as _json
from pathlib import Path

import httpx
import respx

from backend.app.providers.openai_compat import OpenAICompatProvider

BASE = "https://api.example.com/v1"
KEY = "sk-live-secret-abcdef1234"  # noqa: E501


def _provider(task_type: str = "image", outputs_dir=None) -> OpenAICompatProvider:
    return OpenAICompatProvider(
        model="test-model",
        base_url=BASE,
        key_plain=KEY,
        task_type=task_type,
        outputs_dir=outputs_dir,
    )


def test_image_generation_returns_url(respx_mock, tmp_path):
    """远端 URL 应落盘本地（S3 temporary 链接有时效）。"""
    respx_mock.post(f"{BASE}/images/generations").mock(
        return_value=httpx.Response(200, json={"data": [{"url": "https://cdn/x.png"}]})
    )
    respx_mock.get("https://cdn/x.png").mock(
        return_value=httpx.Response(200, content=b"\x89PNG-fake")
    )

    async def run():
        return await _provider("image", outputs_dir=tmp_path).run(_fake_task("image"))

    import asyncio

    result = asyncio.run(run())
    assert result["status"] == "completed"
    out = result["output_url"]
    assert out.startswith("/outputs/images/") and out.endswith(".png")
    assert (tmp_path / Path(out).name).read_bytes() == b"\x89PNG-fake"


def test_image_edit_relative_outputs_path_reads_local(respx_mock, tmp_path, monkeypatch):
    """BUG-B 回归：局部重绘的 image/mask 为 /outputs/ 相对路径时，worker 直接读本地盘。
    修复前 httpx.get("/outputs/...") 抛 UnsupportedProtocol → ASSET_FETCH_FAILED。"""
    import base64

    (tmp_path / "outputs" / "uploads").mkdir(parents=True)
    (tmp_path / "outputs" / "images").mkdir(parents=True)
    (tmp_path / "outputs" / "uploads" / "mask.png").write_bytes(b"MASKPNG")
    (tmp_path / "outputs" / "images" / "src.png").write_bytes(b"SRCPNG")
    monkeypatch.chdir(tmp_path)

    sent: dict = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        sent["content_type"] = request.headers.get("content-type", "")
        sent["body"] = request.content
        b64 = base64.b64encode(b"EDITPNG").decode()
        return httpx.Response(200, json={"data": [{"b64_json": b64}]})

    respx_mock.post(f"{BASE}/images/edits").mock(side_effect=_capture)

    task = _fake_task(
        "image",
        params={
            "mode": "edit",
            "image": "/outputs/images/src.png",
            "mask": "/outputs/uploads/mask.png",
        },
    )

    import asyncio

    result = asyncio.run(_provider("image").run(task))
    assert result["status"] == "completed", result
    assert result["output_url"].startswith("/outputs/images/")
    # multipart 携带了本地读出的原图与蒙版
    assert b"SRCPNG" in sent["body"]
    assert b"MASKPNG" in sent["body"]
    assert "multipart/form-data" in sent["content_type"]


def test_llm_long_content_fully_returned(respx_mock):
    long_text = "长" * 500  # Review Focus #2：>220 字不截断
    respx_mock.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": long_text}}]})
    )

    import asyncio

    result = asyncio.run(_provider("llm").run(_fake_task("llm")))
    assert result["status"] == "completed"
    assert result["output_url"] == long_text


def test_audio_saves_local_file(respx_mock, tmp_path):
    respx_mock.post(f"{BASE}/audio/speech").mock(
        return_value=httpx.Response(200, content=b"ID3fakeaudio")
    )

    import asyncio

    p = _provider("audio")
    p.outputs_dir = tmp_path
    result = asyncio.run(p.run(_fake_task("audio")))

    assert result["status"] == "completed"
    assert result["output_url"].startswith("/outputs/audio/")
    assert len(list(tmp_path.rglob("*.mp3"))) == 1


def test_401_maps_to_key_invalid(respx_mock):
    respx_mock.post(f"{BASE}/images/generations").mock(
        return_value=httpx.Response(401, json={"error": {"message": "bad key"}})
    )

    import asyncio

    result = asyncio.run(_provider("image").run(_fake_task("image")))
    assert result["status"] == "failed"
    assert result["error_code"] == "CHANNEL_KEY_INVALID"


def test_404_model_not_found(respx_mock):
    respx_mock.post(f"{BASE}/images/generations").mock(
        return_value=httpx.Response(404, json={"error": {"message": "no such model"}})
    )

    import asyncio

    result = asyncio.run(_provider("image").run(_fake_task("image")))
    assert result["error_code"] == "PROVIDER_MODEL_NOT_FOUND"


def test_429_maps_to_rate_limited(respx_mock):
    respx_mock.post(f"{BASE}/images/generations").mock(
        return_value=httpx.Response(429, json={"error": {"message": "slow down"}})
    )

    import asyncio

    result = asyncio.run(_provider("image").run(_fake_task("image")))
    assert result["error_code"] == "PROVIDER_RATE_LIMITED"


def test_key_never_in_error_or_logs(respx_mock, caplog):
    """Review Focus #1：异常消息与日志不得含明文 key。"""
    respx_mock.post(f"{BASE}/images/generations").mock(
        return_value=httpx.Response(500, json={"error": {"message": "boom"}})
    )

    import asyncio

    with caplog.at_level("DEBUG"):
        result = asyncio.run(_provider("image").run(_fake_task("image")))

    assert result["status"] == "failed"
    assert KEY not in str(result)
    for record in caplog.records:
        assert KEY not in record.getMessage()


def test_5xx_maps_to_server_error_with_hint(respx_mock):
    respx_mock.post(f"{BASE}/images/generations").mock(
        return_value=httpx.Response(500, json={"error": {"message": "boom"}})
    )

    import asyncio

    result = asyncio.run(_provider("image").run(_fake_task("image")))
    assert result["error_code"] == "PROVIDER_SERVER_ERROR"
    assert result["error_hint"]  # 有下一步动作提示


# ---- helpers ----
def _fake_task(task_type: str, params: dict | None = None):
    class T:
        id = 1
        type = task_type
        prompt = "一只戴帽子的猫"

        def params_dict(self):
            if params is not None:
                return params
            return (
                {  # noqa: E731
                    "voice": "alloy"
                }
                if task_type == "audio"
                else {}
            )

    return T()


def test_prompt_seed_imports_when_empty(db_session):
    """审计回归：内置提示词库首启导入且分类齐全。"""
    from backend.app.seed_prompts import seed_prompts_if_empty

    n = seed_prompts_if_empty(db_session)
    assert n >= 30
    # 幂等
    assert seed_prompts_if_empty(db_session) == 0
    from sqlalchemy import func

    from backend.app.models import Prompt

    cats = db_session.query(func.count(func.distinct(Prompt.category))).scalar()
    assert cats >= 5


def test_size_and_negative_passthrough(respx_mock):
    """生图参数透传：size/negative_prompt 进请求体。"""
    route = respx_mock.post(f"{BASE}/images/generations").mock(
        return_value=httpx.Response(200, json={"data": [{"url": "https://cdn/y.png"}]})
    )
    respx_mock.get("https://cdn/y.png").mock(return_value=httpx.Response(200, content=b"png"))

    class T2:
        id = 2
        type = "image"
        prompt = "p"

        def params_dict(self):
            return {"size": "1024x1792", "negative_prompt": "blurry"}

    import asyncio

    result = asyncio.run(_provider("image", outputs_dir=Path("outputs/images-tmp")).run(T2()))
    assert result["status"] == "completed"
    sent = route.calls.last.request.read().decode()
    assert '"size":"1024x1792"' in sent.replace(" ", "")
    assert "blurry" in sent


def test_optimize_endpoint_uses_llm_channel(client, db_session, monkeypatch, respx_mock):
    """提示词优化器：走用户 llm 渠道（Fake 关闭时验证真实调用路径）。"""
    monkeypatch.setenv("ENABLE_FAKE_PROVIDER", "false")
    from backend.app.core.config import get_settings

    get_settings.cache_clear()
    try:
        reg = client.post(
            "/api/auth/register",
            json={
                "username": "optuser",
                "email": "opt@x.io",
                "password": "password123",
                "invite_code": "letmein",
            },
        )
        headers = {"Authorization": f"Bearer {reg.json()['access_token']}"}
        ch = client.post(
            "/api/providers/channels",
            json={"domain": "llm", "name": "opt", "base_url": BASE, "key": KEY},
            headers=headers,
        ).json()
        assert ch["id"]
        respx_mock.post(f"{BASE}/chat/completions").mock(
            return_value=httpx.Response(
                200, json={"choices": [{"message": {"content": "优化后的提示词"}}]}
            )
        )
        r = client.post(
            "/api/prompts/optimize",
            json={"prompt": "一只戴帽子的橘猫在月光下奔跑"},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        assert r.json().get("optimized") == "优化后的提示词", r.text
    finally:
        get_settings.cache_clear()


def test_siliconflow_b64_json_response_saved(respx_mock, tmp_path):
    """SiliconFlow b64_json 形态：解码落盘并可访问（生产实测响应形态）。"""
    import base64

    p = _provider("image")
    p.outputs_dir = tmp_path
    fake_png = base64.b64encode(b"\x89PNG fake").decode()
    respx_mock.post(f"{BASE}/images/generations").mock(
        return_value=httpx.Response(200, json={"images": [{"b64_json": fake_png}]})
    )

    import asyncio

    result = asyncio.run(p.run(_fake_task("image")))
    assert result["status"] == "completed"
    assert result["output_url"].startswith("/outputs/images/")
    # 落盘文件确实存在
    fname = result["output_url"].split("/")[-1]
    assert (tmp_path / fname).read_bytes() == b"\x89PNG fake"


def test_siliconflow_images_array_url_form(respx_mock, tmp_path):
    """SiliconFlow images[] + url 形态（与 OpenAI data[] 并存兼容）。"""
    respx_mock.post(f"{BASE}/images/generations").mock(
        return_value=httpx.Response(200, json={"images": [{"url": "https://cdn/sf.png"}]})
    )
    respx_mock.get("https://cdn/sf.png").mock(return_value=httpx.Response(200, content=b"png"))

    import asyncio

    result = asyncio.run(_provider("image", outputs_dir=tmp_path).run(_fake_task("image")))
    assert result["output_url"].startswith("/outputs/images/")


def test_openai_compat_images_endpoint(client):
    """对外 OpenAI 兼容端点：鉴权+参数+响应形态。"""
    reg = client.post(
        "/api/auth/register",
        json={
            "username": "oc",
            "email": "oc@x.io",
            "password": "password123",
            "invite_code": "letmein",
        },
    )
    headers = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    # 先建一个渠道（对外端点走用户真实渠道）
    client.post(
        "/api/providers/channels",
        json={"domain": "image", "name": "c1", "base_url": BASE, "key": KEY},
        headers=headers,
    )
    with respx.mock:
        respx.post(f"{BASE}/images/generations").mock(
            return_value=httpx.Response(200, json={"data": [{"url": "https://cdn/ok.png"}]})
        )
        respx.get("https://cdn/ok.png").mock(return_value=httpx.Response(200, content=b"png"))
        r = client.post(
            "/v1/images/generations",
            json={"model": "any", "prompt": "test prompt", "n": 1},
            headers=headers,
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "created" in body and isinstance(body["data"], list) and len(body["data"]) == 1

    # 未鉴权 → 401
    r2 = client.post("/v1/images/generations", json={"model": "x", "prompt": "y"})
    assert r2.status_code == 401


def test_image_edit_ref_mode_uses_generations_with_image(respx_mock, tmp_path, monkeypatch):
    """edit_ref 能力（SiliconFlow 风格）：/images/generations + image 参考（JSON，mask 忽略）。"""
    import base64

    (tmp_path / "outputs" / "images").mkdir(parents=True)
    (tmp_path / "outputs" / "images" / "src.png").write_bytes(b"REFPNG")
    monkeypatch.chdir(tmp_path)

    sent: dict = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        import json

        sent["ct"] = request.headers.get("content-type", "")
        sent["body"] = json.loads(request.content)
        return httpx.Response(200, json={"images": [{"url": "https://cdn/edited.png"}]})

    respx_mock.post(f"{BASE}/images/generations").mock(side_effect=_capture)
    respx_mock.get("https://cdn/edited.png").mock(
        return_value=httpx.Response(200, content=b"EDITED")
    )

    task = _fake_task(
        "image",
        params={"mode": "edit", "image": "/outputs/images/src.png", "mask": "/outputs/x.png"},
    )

    class _Ch:
        name = "SiliconFlow 生图"
        base_url = BASE
        capabilities = '["edit_ref"]'

        @staticmethod
        def _capabilities_list():
            return ["edit_ref"]

    from backend.app.providers.openai_compat import OpenAICompatProvider

    p = OpenAICompatProvider(
        model="Qwen/Qwen-Image-Edit", channel=_Ch, key_plain="k", base_url=BASE, task_type="image"
    )
    from typing import Any as _Any
    p_any: _Any = p
    p_any._caps = lambda: ["edit_ref"]

    import asyncio

    result = asyncio.run(p.run(task))
    assert result["status"] == "completed", result
    # 走 generations JSON，参考图以 data URL 内嵌，mask 不参与
    assert "application/json" in sent["ct"]
    assert sent["body"]["image"].startswith("data:image/")
    assert base64.b64decode(sent["body"]["image"].split(",", 1)[1]) == b"REFPNG"
    assert "mask" not in sent["body"]
    assert result["output_url"].startswith("/outputs/images/")


def test_image_generation_passes_reference_urls(respx_mock, tmp_path, monkeypatch):
    """i2i：reference_urls 透传为 image 字段——本地转 data URL，远端直传。"""
    (tmp_path / "outputs" / "images").mkdir(parents=True)
    (tmp_path / "outputs" / "images" / "ref.png").write_bytes(b"REFBYTES")
    monkeypatch.chdir(tmp_path)

    import base64
    import json as _json

    sent: dict = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        sent["body"] = _json.loads(request.content)
        return httpx.Response(200, json={"images": [{"url": "https://cdn/r.png"}]})

    respx_mock.post(f"{BASE}/images/generations").mock(side_effect=_capture)
    respx_mock.get("https://cdn/r.png").mock(return_value=httpx.Response(200, content=b"X"))

    task = _fake_task(
        "image",
        params={
            "reference_urls": [
                "/outputs/images/ref.png",  # 本地 → data URL
                "https://example.com/remote.png",  # 远端 → 直传
            ]
        },
    )

    import asyncio

    result = asyncio.run(_provider("image").run(task))
    assert result["status"] == "completed", result
    img = sent["body"]["image"]
    assert isinstance(img, list) and len(img) == 2
    assert img[0].startswith("data:image/")
    assert base64.b64decode(img[0].split(",", 1)[1]) == b"REFBYTES"
    assert img[1] == "https://example.com/remote.png"


def test_image_generation_reference_load_failure_fails(respx_mock, tmp_path, monkeypatch):
    """本地参考图缺失：按素材获取失败归因（用户明确给了参考，静默丢弃会误导）。"""
    monkeypatch.chdir(tmp_path)

    task = _fake_task("image", params={"reference_urls": ["/outputs/images/gone.png"]})

    import asyncio

    result = asyncio.run(_provider("image").run(task))
    assert result["status"] == "failed"
    assert result["error_code"] == "ASSET_FETCH_FAILED"


def test_llm_streaming_emits_cumulative_deltas(respx_mock):
    """流式 LLM：on_event 存在时走 stream=True，累计 delta 回调；<think> 段不外显，终稿剥离。"""
    sse = (
        b'data: {"choices":[{"delta":{"content":"<think>\\u8003\\u8651"}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":"\\u4e2d"}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":"</think>"}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":"\\u4f60\\u597d"}}]}\n\n'
        b"data: [DONE]\n\n"
    )
    respx_mock.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(200, content=sse, headers={"content-type": "text/event-stream"})
    )

    deltas: list[str] = []

    def on_event(etype: str, data: dict) -> None:
        if etype == "delta":
            deltas.append(data["text"])

    import asyncio

    result = asyncio.run(_provider("llm").run(_fake_task("llm"), on_event=on_event))
    assert result["status"] == "completed"
    assert result["output_url"] == "你好"
    # think 进行中不外显（空串）；闭合后只回正文；末帧为完整正文（累计语义）
    assert deltas and all(d == "" for d in deltas[:-1])
    assert deltas[-1] == "你好"


def test_llm_streaming_http_error_classified(respx_mock):
    respx_mock.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(401, content=b'{"error":"bad key"}')
    )
    import asyncio

    result = asyncio.run(_provider("llm").run(_fake_task("llm"), on_event=lambda *_: None))
    assert result["status"] == "failed"
    assert result["error_code"] == "CHANNEL_KEY_INVALID"


def test_multi_image_all_saved_not_just_first(respx_mock, tmp_path):
    """P2-9: 批量生成返回多张图——全部落盘，extra_urls 携带其余（免二次付费取回）。"""
    for i in range(3):
        respx_mock.get(f"https://cdn/m{i}.png").mock(
            return_value=httpx.Response(200, content=f"PNG{i}".encode())
        )
    respx_mock.post(f"{BASE}/images/generations").mock(
        return_value=httpx.Response(
            200,
            json={"data": [
                {"url": "https://cdn/m0.png"},
                {"url": "https://cdn/m1.png"},
                {"url": "https://cdn/m2.png"},
            ]},
        )
    )

    async def run():
        return await _provider("image", outputs_dir=tmp_path).run(_fake_task("image"))

    import asyncio

    result = asyncio.run(run())
    assert result["status"] == "completed"
    assert result["output_url"].endswith(".png")
    # 其余两张不丢弃
    extras = result.get("extra_urls", [])
    assert len(extras) == 2
    for u in extras:
        assert u.startswith("/outputs/images/")
        assert (tmp_path / Path(u).name).is_file()


def test_first_item_empty_falls_back_to_others(respx_mock, tmp_path):
    """P2-9: 第一项无地址但其余有效——不再整任务失败。"""
    respx_mock.get("https://cdn/ok.png").mock(
        return_value=httpx.Response(200, content=b"PNGOK")
    )
    respx_mock.post(f"{BASE}/images/generations").mock(
        return_value=httpx.Response(
            200, json={"data": [{}, {"url": "https://cdn/ok.png"}]}
        )
    )

    async def run():
        return await _provider("image", outputs_dir=tmp_path).run(_fake_task("image"))

    import asyncio

    result = asyncio.run(run())
    assert result["status"] == "completed"
    assert result["output_url"].startswith("/outputs/images/")


# ---------- Task 6：参数透传按域 schema ----------


def test_domain_params_passthrough(respx_mock, tmp_path):
    """按域 schema 透传：steps 到达 body；seed=0 不被丢弃（Review Focus 1）；未知键不上游。"""
    captured: dict = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = _json.loads(request.content)
        return httpx.Response(200, json={"data": [{"b64_json": "aW1n"}]})

    respx_mock.post(f"{BASE}/images/generations").mock(side_effect=_capture)
    provider = _provider("image", outputs_dir=tmp_path)

    class T:
        id = 1
        type = "image"
        prompt = "猫"

        def params_dict(self):
            return {"steps": 20, "seed": 0, "custom_x": 1}

    result = _asyncio.run(provider.run(T()))
    assert result["status"] == "completed"
    body = captured["body"]
    assert body["steps"] == 20
    assert body["seed"] == 0
    assert "custom_x" not in body


def test_llm_temperature_passthrough(respx_mock):
    """llm 域：temperature 数值类型保真透传。"""
    captured: dict = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = _json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    respx_mock.post(f"{BASE}/chat/completions").mock(side_effect=_capture)
    provider = _provider("llm")

    class T:
        prompt = "hi"

        def params_dict(self):
            return {"temperature": 0.7}

    result = _asyncio.run(provider.run(T(), on_event=None))
    assert result["status"] == "completed"
    assert captured["body"]["temperature"] == 0.7


def test_legacy_three_keys_still_passthrough(respx_mock, tmp_path):
    """兼容承诺：原三键 size/negative_prompt/i2i_denoise 行为保持。"""
    captured: dict = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = _json.loads(request.content)
        return httpx.Response(200, json={"data": [{"b64_json": "aW1n"}]})

    respx_mock.post(f"{BASE}/images/generations").mock(side_effect=_capture)
    provider = _provider("image", outputs_dir=tmp_path)

    class T:
        id = 1
        type = "image"
        prompt = "猫"

        def params_dict(self):
            return {"size": "1024x1024", "negative_prompt": "模糊", "i2i_denoise": 0.75}

    result = _asyncio.run(provider.run(T()))
    assert result["status"] == "completed"
    body = captured["body"]
    assert body["size"] == "1024x1024"
    assert body["negative_prompt"] == "模糊"
    assert body["i2i_denoise"] == 0.75
