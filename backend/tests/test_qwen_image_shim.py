"""Qwen-Image-2.1 shim（fnos → ComfyUI @ Thor 179:8188）单元测试。



与 zimage_shim 同款 OpenAI 兼容契约，两处关键差异：

①产物经 ComfyUI /view HTTP 端点取图（shim 在 fnos，ComfyUI 在 179，不能读本地文件）

②Qwen-Image 2.1 独立负向提示词节点 + 透传 seed/steps/guidance/negative_prompt/i2i_denoise

"""



from __future__ import annotations

import base64
import importlib.util
import time
from pathlib import Path

import pytest

_SHIM = Path(__file__).resolve().parents[2] / "scripts" / "qwen_image_shim.py"

if not _SHIM.is_file():
    pytest.skip("shim script missing in public snapshot", allow_module_level=True)








def _load_shim():

    spec = importlib.util.spec_from_file_location("qwen_image_shim_test", _SHIM)

    assert spec is not None and spec.loader is not None  # mypy 收窄

    mod = importlib.util.module_from_spec(spec)

    import sys as _sys



    _sys.modules[spec.name] = mod  # pydantic 需经 sys.modules 解析注解

    spec.loader.exec_module(mod)

    return mod





PNG_1PX = base64.b64encode(

    bytes.fromhex(

        "89504e470d0a1a0a0000000d494844520000000100000001080600000"

        "01f15c4890000000d49444154789c626001000000ffff030000060005"

        "57bfabd40000000049454e44ae426082"

    )

).decode()





@pytest.fixture()

def shim(monkeypatch):

    mod = _load_shim()

    mod._JOBS.clear()

    mod._CANCELLED.clear()

    calls: dict = {"posts": [], "gets": [], "view_bytes": [], "uploads": []}



    def fake_post(url, json=None, timeout=None, files=None, data=None):  # noqa: ANN001

        calls["posts"].append((url, json))

        if "/upload/image" in url:

            calls["uploads"].append(url)

            raise AssertionError("t2i 不应触发上传")



        class R:

            status_code = 200



            def raise_for_status(self) -> None:

                return None



            def json(self):

                return {"prompt_id": "pid-q21"}



        return R()



    def fake_get(url, timeout=None, params=None):  # noqa: ANN001

        calls["gets"].append((url, params))



        class R:

            status_code = 200

            content = b"PNGBYTES"



            def raise_for_status(self) -> None:

                return None



            def json(self):

                if "/history/" in url:

                    return {

                        "pid-q21": {

                            "status": {"status_str": "success"},

                            "outputs": {

                                "8": {

                                    "images": [

                                        {

                                            "filename": "q21_00001_.png",

                                            "subfolder": "",

                                            "type": "output",

                                        }

                                    ]

                                }

                            },

                        }

                    }

                return {}



        return R()



    monkeypatch.setattr(mod.httpx, "post", fake_post)

    monkeypatch.setattr(mod.httpx, "get", fake_get)

    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)

    mod._test_calls = calls

    return mod





# ---- 工作流构造 ----





def test_workflow_t2i_shape(shim):

    """t2i 工作流：三加载器文件名正确、正负向分离、EmptySD3LatentImage、官方默认参数。"""

    wf = shim._workflow("一只猫", 1024, 768, 1)

    assert wf["1"]["class_type"] == "UNETLoader"

    assert wf["1"]["inputs"]["unet_name"] == shim.UNET

    assert wf["2"]["class_type"] == "CLIPLoader"

    assert wf["2"]["inputs"]["clip_name"] == shim.CLIP

    assert wf["2"]["inputs"]["type"] == "qwen_image"

    assert wf["3"]["class_type"] == "VAELoader"

    assert wf["3"]["inputs"]["vae_name"] == shim.VAE

    pos, neg = wf["5"]["inputs"], wf["5n"]["inputs"]

    assert pos["text"] == "一只猫" and neg["text"] == ""

    assert pos["clip"] == ["2", 0] and neg["clip"] == ["2", 0]

    assert wf["4"]["class_type"] == "EmptySD3LatentImage"

    ks = wf["6"]["inputs"]

    assert ks["steps"] == shim.STEPS == 25

    assert ks["cfg"] == shim.CFG_NO_NEG == 1.0  # 官方路径：无负向词 cfg=1

    assert ks["negative"] == ["5n", 0]

    assert ks["sampler_name"] == "euler" and ks["scheduler"] == "simple"

    assert "8" in wf and wf["8"]["class_type"] == "SaveImage"





def test_workflow_passthrough_params(shim):

    """seed/steps/guidance/negative_prompt 透传；seed=0 视为随机；

    负向词非空 → cfg 自动升（官方：仅在有负向词时 raise cfg）。"""

    wf = shim._workflow(

        "p", 512, 512, 1,

        seed=123, steps=8, guidance=3.0, negative_prompt="blurry",  # noqa: E501

    )

    ks = wf["6"]["inputs"]

    assert ks["seed"] == 123 and ks["steps"] == 8 and ks["cfg"] == 3.0

    assert wf["5n"]["inputs"]["text"] == "blurry"



    wf2 = shim._workflow("p", 512, 512, 1, seed=0)

    assert isinstance(wf2["6"]["inputs"]["seed"], int)

    assert wf2["6"]["inputs"]["cfg"] == 1.0



    # 有负向词但未显式给 guidance → cfg 自动升 2.5

    wf3 = shim._workflow("p", 512, 512, 1, negative_prompt="motion blur")

    assert wf3["6"]["inputs"]["cfg"] == 2.5

    assert wf3["5n"]["inputs"]["text"] == "motion blur"





def test_workflow_edit_shape(shim):

    """官方编辑工作流：TextEncodeQwenImage21 三输出直接进 KSampler，

    latent 用编码器自身的（自动跟随参考图尺寸），无需 EmptyLatentImage。"""

    wf = shim._edit_workflow("把背景换成雪夜", [{"name": "r1.png", "subfolder": ""}])

    assert wf["4a"]["class_type"] == "LoadImage"

    assert wf["4a"]["inputs"]["image"] == "r1.png"

    enc = wf["5"]["inputs"]

    assert enc["clip"] == ["2", 0] and enc["vae"] == ["3", 0]

    assert enc["prompt"] == "把背景换成雪夜"

    assert enc["images.image_1"] == ["4a", 0]

    assert enc["resolution"] == 1024

    ks = wf["6"]["inputs"]

    assert ks["positive"] == ["5", 0] and ks["negative"] == ["5", 1]

    assert ks["latent_image"] == ["5", 2]  # 编码器自带 latent，跟随参考图

    assert ks["steps"] == 25 and ks["cfg"] == 1.0  # 官方同款

    assert ks["sampler_name"] == "euler" and ks["scheduler"] == "simple"





def test_workflow_edit_multi_ref(shim):

    """多参考图：image_1/image_2 分别接线，上限 4 张（超出的截断）。"""

    refs = [{"name": f"r{i}.png", "subfolder": ""} for i in (1, 2)]

    wf = shim._edit_workflow("合成", refs)

    assert wf["5"]["inputs"]["images.image_1"] == ["4a", 0]

    assert wf["5"]["inputs"]["images.image_2"] == ["4b", 0]



    wf6 = shim._edit_workflow(

        "x", [{"name": f"r{i}.png", "subfolder": ""} for i in range(6)]

    )

    assert "image_5" not in wf6["5"]["inputs"]





def test_workflow_i2i_chain(shim):

    """显式 i2i_denoise → 保留 legacy 去噪路径：LoadImage→ImageScale→VAEEncode。

    （风格迁移/重绘场景；不带 i2i_denoise 的带图请求走官方编辑路径）"""

    ref = {"name": "ref.png", "subfolder": ""}

    wf = shim._denoise_workflow("改成夜景", 1024, 1024, 1, ref_image=ref, denoise=0.9)

    assert wf["4a"]["class_type"] == "LoadImage"

    assert wf["4a"]["inputs"]["image"] == "ref.png"

    assert wf["4b"]["class_type"] == "ImageScale"

    assert wf["4"]["class_type"] == "VAEEncode"

    assert wf["6"]["inputs"]["denoise"] == 0.9  # 上限钳制 0.95 内



    wf2 = shim._denoise_workflow("p", 512, 512, 1, ref_image=ref, denoise=0.1)

    assert wf2["6"]["inputs"]["denoise"] == 0.5  # 下限钳制





def test_parse_size_aligns_and_validates(shim):

    from fastapi import HTTPException



    assert shim._parse_size("1024x768") == (1024, 768)

    assert shim._parse_size("1000x700") == (960, 640)  # 对齐 64

    with pytest.raises(HTTPException):

        shim._parse_size("100x100")  # 低于 256

    with pytest.raises(HTTPException):

        shim._parse_size("bad")





# ---- HTTP 端点 ----





def test_models_endpoint(shim):

    from fastapi.testclient import TestClient



    r = TestClient(shim.app).get("/v1/models")

    ids = [m["id"] for m in r.json()["data"]]

    assert "qwen-image-2.1" in ids





def test_generations_t2i_returns_b64_via_view(shim):

    """t2i 全链：/prompt 提交 → /history 轮询 → /view HTTP 取图 → b64_json。"""

    from fastapi.testclient import TestClient



    r = TestClient(shim.app).post(

        "/v1/images/generations",

        json={"prompt": "灯塔", "n": 1, "size": "512x512"},

        headers={"X-Creagen-Task-Id": "555"},

    )

    assert r.status_code == 200, r.text

    items = r.json()["data"]

    assert items[0]["b64_json"] == base64.b64encode(b"PNGBYTES").decode()

    # 提交 /prompt 带工作流与 client_id；撤销映射登记

    post_url, post_json = shim._test_calls["posts"][0]

    assert post_url.endswith("/prompt") and "client_id" in post_json

    assert post_json["prompt"]["1"]["inputs"]["unet_name"] == shim.UNET

    assert shim._JOBS["555"]["pid"] == "pid-q21"

    # 取图走 /view 端点（非本地文件读——shim 与 ComfyUI 不同机）

    view_calls = [c for c in shim._test_calls["gets"] if "/view" in c[0]]

    assert view_calls and view_calls[0][1]["filename"] == "q21_00001_.png"

    assert view_calls[0][1]["type"] == "output"





def test_generations_image_routes_to_edit(shim, monkeypatch):

    """带图不带 i2i_denoise → 官方编辑路径（TextEncodeQwenImage21）。"""

    from fastapi.testclient import TestClient



    def ok_upload(url, json=None, files=None, data=None, timeout=None):  # noqa: ANN001

        class R:

            status_code = 200



            def raise_for_status(self) -> None:

                return None



            def json(self):

                if "/upload/image" in url:

                    return {"name": "ref.png", "subfolder": ""}

                shim._test_calls["posts"].append((url, json))

                return {"prompt_id": "pid-q21"}



        return R()



    monkeypatch.setattr(shim.httpx, "post", ok_upload)

    r = TestClient(shim.app).post(

        "/v1/images/generations",

        json={"prompt": "改成夜景", "image": f"data:image/png;base64,{PNG_1PX}"},

    )

    assert r.status_code == 200, r.text

    wf = shim._test_calls["posts"][0][1]["prompt"]

    assert wf["5"]["class_type"] == shim.EDIT_ENCODER

    assert wf["6"]["inputs"]["latent_image"] == ["5", 2]





def test_generations_image_denoise_routes_legacy(shim, monkeypatch):

    """带图 + 显式 i2i_denoise → legacy 去噪路径（风格迁移场景）。"""

    from fastapi.testclient import TestClient



    def ok_upload(url, json=None, files=None, data=None, timeout=None):  # noqa: ANN001

        class R:

            status_code = 200



            def raise_for_status(self) -> None:

                return None



            def json(self):

                if "/upload/image" in url:

                    return {"name": "ref.png", "subfolder": ""}

                shim._test_calls["posts"].append((url, json))

                return {"prompt_id": "pid-q21"}



        return R()



    monkeypatch.setattr(shim.httpx, "post", ok_upload)

    r = TestClient(shim.app).post(

        "/v1/images/generations",

        json={"prompt": "换风格", "image": f"data:image/png;base64,{PNG_1PX}",

              "i2i_denoise": 0.75},

    )

    assert r.status_code == 200, r.text

    wf = shim._test_calls["posts"][0][1]["prompt"]

    assert wf["4"]["class_type"] == "VAEEncode"

    assert wf["6"]["inputs"]["denoise"] == 0.75





def test_edits_inpaint_workflow(shim, monkeypatch):

    """OpenAI /images/edits（原图+蒙版 multipart）→ SetLatentNoiseMask 真·局部重绘。

    蒙版白=重绘区（OpenAI 约定）→ ImageToMask(red) 直接映射；denoise=1.0。"""

    from fastapi.testclient import TestClient



    def ok_upload(url, json=None, files=None, data=None, timeout=None):  # noqa: ANN001

        class R:

            status_code = 200



            def raise_for_status(self) -> None:

                return None



            def json(self):

                if "/upload/image" in url:

                    shim._test_calls["uploads"].append(url)

                    return {"name": f"up{len(shim._test_calls['uploads'])}.png", "subfolder": ""}

                shim._test_calls["posts"].append((url, json))

                return {"prompt_id": "pid-q21"}



        return R()



    monkeypatch.setattr(shim.httpx, "post", ok_upload)

    r = TestClient(shim.app).post(

        "/v1/images/edits",

        files={

            "image": ("img.png", b"IMAGEBYTES", "image/png"),

            "mask": ("mask.png", b"MASKBYTES", "image/png"),

        },

        data={"prompt": "把重绘区改成玫瑰", "size": "1024x1024"},

        headers={"X-Creagen-Task-Id": "808"},

    )

    assert r.status_code == 200, r.text

    assert r.json()["data"][0]["b64_json"] == base64.b64encode(b"PNGBYTES").decode()

    assert len(shim._test_calls["uploads"]) == 2  # 原图+蒙版都上传

    wf = shim._test_calls["posts"][0][1]["prompt"]

    assert wf["4a"]["class_type"] == "LoadImage"  # 原图

    assert wf["4b"]["class_type"] == "LoadImage"  # 蒙版

    assert wf["4c"]["class_type"] == "ImageToMask"

    assert wf["4c"]["inputs"]["channel"] == "red"

    assert wf["4d"]["class_type"] == "VAEEncode"

    assert wf["4e"]["class_type"] == "SetLatentNoiseMask"

    assert wf["4e"]["inputs"]["samples"] == ["4d", 0] and wf["4e"]["inputs"]["mask"] == ["4c", 0]

    assert wf["6"]["inputs"]["latent_image"] == ["4e", 0]

    assert wf["6"]["inputs"]["denoise"] == 1.0

    assert shim._JOBS["808"]["pid"] == "pid-q21"  # 撤销映射同款登记





def test_edits_requires_image_and_mask(shim, monkeypatch):

    """缺蒙版 → 422 PARAM_MISSING 语义（对齐平台“局部重绘需要原图与蒙版”）。"""

    from fastapi.testclient import TestClient



    r = TestClient(shim.app).post(

        "/v1/images/edits",

        files={"image": ("img.png", b"X", "image/png")},

        data={"prompt": "p"},

    )

    assert r.status_code == 422

    assert "蒙版" in r.json()["detail"]





def test_generations_multi_ref_uploads(shim, monkeypatch):

    """image 为列表 → 多张参考图分别上传并接线 images.image_1/2。"""

    from fastapi.testclient import TestClient



    uploads: list = []



    def ok_upload(url, json=None, files=None, data=None, timeout=None):  # noqa: ANN001

        class R:

            status_code = 200



            def raise_for_status(self) -> None:

                return None



            def json(self):

                if "/upload/image" in url:

                    uploads.append(url)

                    return {"name": f"r{len(uploads)}.png", "subfolder": ""}

                shim._test_calls["posts"].append((url, json))

                return {"prompt_id": "pid-q21"}



        return R()



    monkeypatch.setattr(shim.httpx, "post", ok_upload)

    two = f"data:image/png;base64,{PNG_1PX}"

    r = TestClient(shim.app).post(

        "/v1/images/generations",

        json={"prompt": "合成", "image": [two, two]},

    )

    assert r.status_code == 200, r.text

    assert len(uploads) == 2

    wf = shim._test_calls["posts"][0][1]["prompt"]

    assert wf["5"]["inputs"]["images.image_1"] == ["4a", 0]

    assert wf["5"]["inputs"]["images.image_2"] == ["4b", 0]





def test_generations_i2i_uploads_and_no_silent_downgrade(shim, monkeypatch):

    """i2i：参考图上传到 ComfyUI；上传失败必须 502 显式报错（不降级 t2i）。"""

    from fastapi.testclient import TestClient



    # data URL → 上传成功 → 工作流含 LoadImage；/prompt 照常返回 prompt_id

    def ok_upload(url, json=None, files=None, data=None, timeout=None):  # noqa: ANN001

        class R:

            status_code = 200



            def raise_for_status(self) -> None:

                return None



            def json(self):

                if "/upload/image" in url:

                    shim._test_calls["uploads"].append(url)

                    return {"name": "ref.png", "subfolder": ""}

                return {"prompt_id": "pid-q21"}



        return R()



    monkeypatch.setattr(shim.httpx, "post", ok_upload)

    r = TestClient(shim.app).post(

        "/v1/images/generations",

        json={"prompt": "改", "image": f"data:image/png;base64,{PNG_1PX}"},

    )

    assert r.status_code == 200

    assert shim._test_calls["uploads"]



    # 上传失败 → 502，绝不静默降级 t2i

    def bad_upload(url, json=None, files=None, data=None, timeout=None):  # noqa: ANN001

        if "/upload/image" in url:

            raise shim.httpx.HTTPError("boom")



        class R:

            status_code = 200



            def raise_for_status(self) -> None:

                return None



            def json(self):

                return {"prompt_id": "pid-q21"}



        return R()



    monkeypatch.setattr(shim.httpx, "post", bad_upload)

    r2 = TestClient(shim.app).post(

        "/v1/images/generations",

        json={"prompt": "改", "image": f"data:image/png;base64,{PNG_1PX}"},

    )

    assert r2.status_code == 502

    assert "参考图" in r2.json()["detail"]





def test_cancel_protocol(shim, monkeypatch):

    """撤销协议：DELETE /internal/jobs 精确删 pending + 协作取消；未知幂等。"""

    from fastapi.testclient import TestClient



    deletes: list = []



    def fake_request(method, url, json=None, timeout=None):  # noqa: ANN001

        if method == "DELETE":

            deletes.append((url, json))



    monkeypatch.setattr(shim.httpx, "request", fake_request)

    client = TestClient(shim.app)

    shim._JOBS["777"] = {"pid": "pid-777", "ts": time.time()}

    r = client.delete("/internal/jobs/777")

    assert r.json() == {"cancelled": True, "known": True, "prompt_id": "pid-777"}

    assert deletes and deletes[0][1] == {"delete": ["pid-777"]}

    assert "777" in shim._CANCELLED

    # 未知 job 幂等

    r2 = client.delete("/internal/jobs/404")

    assert r2.json() == {"cancelled": False, "known": False}





def test_wait_loop_cooperative_cancel(shim):

    """等待循环里已标记撤销 → 立即 499 退出，不再轮询。"""

    from fastapi.testclient import TestClient



    client = TestClient(shim.app)

    shim._CANCELLED.add("999")

    r = client.post(

        "/v1/images/generations",

        json={"prompt": "x"},

        headers={"X-Creagen-Task-Id": "999"},

    )

    assert r.status_code == 499





def test_history_error_surfaces(shim, monkeypatch):

    """ComfyUI 执行失败 → 500 带日志指引，不装成功。"""

    from fastapi.testclient import TestClient



    def err_get(url, timeout=None, params=None):  # noqa: ANN001

        class R:

            status_code = 200

            content = b""



            def raise_for_status(self) -> None:

                return None



            def json(self):

                if "/history/" in url:

                    return {

                        "pid-q21": {

                            "status": {"status_str": "error"},

                            "outputs": {},

                        }

                    }

                return {}



        return R()



    monkeypatch.setattr(shim.httpx, "get", err_get)

    r = TestClient(shim.app).post("/v1/images/generations", json={"prompt": "x"})

    assert r.status_code == 500

    assert "ComfyUI" in r.json()["detail"]
