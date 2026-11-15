"""OpenAI 兼容协议 Provider：一个适配器覆盖 image / llm / audio 三域。

derived from creagen prod openai_compat 思路，重写为 ProviderBase 实现。
image: POST {base}/images/generations
llm:   POST {base}/chat/completions
audio: POST {base}/audio/speech（二进制，落 outputs/）
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from backend.app.providers.registry import ProviderBase
from backend.app.services.param_schema import passthrough_keys

logger = logging.getLogger("canvas.provider.openai")

_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
# LLM 大 JSON 生成（分镜拆分 6 段 × 40-80 词）常超 30s——读超时单独放宽
_LLM_TIMEOUT = httpx.Timeout(180.0, connect=10.0)
# 本地渠道（自建 shim/ComfyUI 桥）：排队串行执行，读超时放宽（4 并发排队时
# 第 4 个要等 ~40s+，30s 必超时——2026-10-07 多角度四连超时根因）
_LOCAL_TIMEOUT = httpx.Timeout(240.0, connect=10.0)


def _is_local_base(base_url: str) -> bool:
    from ipaddress import ip_address
    from urllib.parse import urlparse

    try:
        host = urlparse(base_url).hostname or ""
        ip = ip_address(host)
        return ip.is_private or ip.is_loopback
    except ValueError:
        return host == "localhost"


def _timeout_for(base_url: str) -> httpx.Timeout:
    return _LOCAL_TIMEOUT if _is_local_base(base_url) else _TIMEOUT


class OpenAICompatProvider(ProviderBase):
    async def cancel_remote_job(self, task_id: int, *, interrupt: bool = False) -> bool:
        """撤销协议：DELETE {base}/internal/jobs/{tid}（本地 shim 支持；
        云端渠道 404 → False 优雅降级）。"""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.delete(
                    f"{self.base_url.rstrip('/')}/internal/jobs/{task_id}",
                    params={"interrupt": str(bool(interrupt)).lower()},
                )
                return r.status_code == 200 and bool(r.json().get("cancelled"))
        except Exception:  # noqa: BLE001 — 撤销是尽力而为
            return False


    domain = "image"

    def __init__(
        self,
        model: str,
        channel: Any = None,  # noqa: ANN401 — ProviderChannel，避免循环 import
        key_plain: str = "",
        base_url: str = "",
        task_type: str = "image",
        outputs_dir: Path | None = None,
    ) -> None:
        self.model = model
        self.channel = channel
        self.key_plain = key_plain
        self.base_url = (base_url or "").rstrip("/")
        self.task_type = task_type
        self.outputs_dir = outputs_dir

    async def test_connection(self) -> bool:
        async with httpx.AsyncClient(timeout=_timeout_for(self.base_url)) as client:
            resp = await client.get(
                f"{self.base_url}/models",
                headers={"Authorization": f"Bearer {self.key_plain}"},
            )
            return resp.status_code < 500

    async def list_models(self) -> list[str]:
        async with httpx.AsyncClient(timeout=_timeout_for(self.base_url)) as client:
            resp = await client.get(
                f"{self.base_url}/models",
                headers={"Authorization": f"Bearer {self.key_plain}"},
            )
            resp.raise_for_status()
            data = resp.json().get("data", [])
            return [item["id"] for item in data if isinstance(item, dict) and "id" in item]

    async def run(
        self,
        task: Any,
        on_event: Any = None,  # noqa: ANN401
    ) -> dict[str, Any]:
        # X-Creagen-Task-Id：本地 shim 登记撤销映射用；云端渠道自动忽略
        headers = {
            "Authorization": f"Bearer {self.key_plain}",
            "X-Creagen-Task-Id": str(getattr(task, "id", "") or ""),
        }
        try:
            async with httpx.AsyncClient(timeout=_timeout_for(self.base_url)) as client:
                if self.task_type == "image":
                    params = task.params_dict() if hasattr(task, "params_dict") else {}
                    if params.get("mode") == "edit":
                        return await self._run_image_edit(client, headers, task, params)
                    return await self._run_image(client, headers, task)
                if self.task_type == "llm":
                    if on_event is not None:
                        return await self._run_llm_stream(client, headers, task, on_event)
                    return await self._run_llm(client, headers, task)
                if self.task_type == "audio":
                    return await self._run_audio(client, headers, task)
                return await self._fail(
                    task, "TASK_TYPE_INVALID", f"OpenAI 兼容渠道不支持 {self.task_type}"
                )
        except httpx.TimeoutException:
            return self._fail_result("PROVIDER_TIMEOUT", "渠道响应超时", "请稍后重试或检查网络")
        except httpx.HTTPError as e:
            # 只记 status 与端点，绝不记录请求头（含 key）
            logger.warning("provider http error: %s %s", type(e).__name__, self.base_url)
            return self._fail_result(
                "PROVIDER_UNREACHABLE", "渠道连接失败", "请检查 Base URL 与网络"
            )

    # ---- 三协议 ----
    async def _run_image(self, client: httpx.AsyncClient, headers: dict, task: Any) -> dict:
        body = {"model": self.model, "prompt": task.prompt, **self._image_count(task)}
        # 透传按域 schema（services/param_schema.py）：falsy 合法值（seed=0）照发；类型保真
        params = task.params_dict() if hasattr(task, "params_dict") else {}
        for key in passthrough_keys("image"):
            value = params.get(key)
            if value is not None and value != "":
                body[key] = value
        # i2i 参考图透传（SiliconFlow image 字段）：本地转 data URL，远端直传
        refs = params.get("reference_urls") or []
        if isinstance(refs, list) and refs:
            from backend.app.providers.assets import to_data_url

            images: list[str] = []
            try:
                for r in refs[:3]:
                    r_str = str(r)
                    if r_str.startswith("/outputs/"):
                        images.append(to_data_url(await self._load_asset_bytes(r_str)))
                    elif r_str.startswith(("http://", "https://")):
                        images.append(r_str)
            except (httpx.HTTPError, OSError) as e:
                return self._fail_result(
                    "ASSET_FETCH_FAILED", f"参考图获取失败: {type(e).__name__}", "请重试"
                )
            if images:
                body["image"] = images[0] if len(images) == 1 else images
        resp = await client.post(
            f"{self.base_url}/images/generations",
            headers=headers,
            json=body,
        )
        # P-fix 2026-10-30: 部分渠道仅收单参考图 (imageforge qwen-image-2.1 实测
        # image=str 200 / list 422)。多参考被 422 拒时自动降级为首图重试一次,
        # 不让整任务挂掉; 降级事件写日志, 输出质量由 fqa 视觉闸兜底。
        refs_truncated = False
        if resp.status_code == 422 and isinstance(body.get("image"), list):
            body["image"] = body["image"][0]
            refs_truncated = True
            resp = await client.post(
                f"{self.base_url}/images/generations",
                headers=headers,
                json=body,
            )
        if refs_truncated and resp.status_code < 400:
            print(
                f"[openai_compat] REFS_TRUNCATED_TO_1 model={self.model} "
                f"task={getattr(task, 'id', '?')} 渠道仅支持单参考图, 已降级为首图",
                flush=True,
            )
        if error := self._classify(resp):
            return error
        payload = resp.json()
        # 兼容两种响应形态：OpenAI 标准 data[] 与 SiliconFlow images[]
        items = payload.get("data") or payload.get("images") or []
        if not items:
            return self._fail_result(
                "PROVIDER_EMPTY_RESULT", "渠道未返回图片结果", "请稍后重试或更换模型"
            )
        # P2-9: 逐张落盘（不只取第一张）——批量生成的每一张都已付费，全部保留；
        # 第一项无效时用后续有效项兜底，不再整任务失败
        urls: list[str] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            u = it.get("url")
            if u and not str(u).startswith("/outputs/"):
                # 远端 URL（如 SiliconFlow S3 temporary 链接）有时效，落盘本地保证资产持久
                u = await self._save_remote(str(u), task)
            if not u and it.get("b64_json"):
                u = self._save_b64(str(it["b64_json"]), task)
            if u:
                urls.append(str(u))
        if not urls:
            return self._fail_result(
                "PROVIDER_EMPTY_RESULT", "渠道未返回图片地址", "请稍后重试或更换模型"
            )
        return {
            "status": "completed",
            "output_url": urls[0],
            "extra_urls": urls[1:5],  # 最多携带 4 张备选（首张为主图）
        }

    def _save_b64(self, b64: str, task) -> str:
        """b64_json 落盘 outputs/images/（derived from creagen prod siliconflow_client）。"""
        import base64 as _b64

        out_dir = self.outputs_dir or Path("outputs/images")
        out_dir.mkdir(parents=True, exist_ok=True)
        raw = _b64.b64decode(b64)
        name = f"img_{task.id}_{uuid4().hex[:10]}.png"
        (out_dir / name).write_bytes(raw)
        return f"/outputs/images/{name}"

    async def _load_asset_bytes(self, url: str) -> bytes:
        """素材获取：本地 /outputs 直读（httpx 不支持相对 URL），远端下载。"""
        from backend.app.providers.assets import load_asset_bytes

        return await load_asset_bytes(url)

    def _caps(self) -> list[str]:
        try:
            return list(self.channel._capabilities_list())  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001 — 无渠道/形状异常按无能力处理
            return []

    async def _run_image_edit(
        self, client: httpx.AsyncClient, headers: dict, task: Any, params: dict
    ) -> dict:
        """图像编辑双协议：edit=OpenAI /images/edits（multipart+mask）；
        edit_ref=SiliconFlow 风格 /images/generations + image 参考
        （JSON，整图语义编辑，mask 不适用）。
        """
        caps = self._caps()
        if "edit" not in caps and "edit_ref" in caps:
            return await self._run_image_edit_ref(client, headers, task, params)

        image_url = str(params.get("image") or "")
        mask_url = str(params.get("mask") or "")
        if not image_url or not mask_url:
            return self._fail_result(
                "PARAM_MISSING", "局部重绘需要原图与蒙版", "请重新框选重绘区域后提交"
            )
        try:
            img_bytes = await self._load_asset_bytes(image_url)
            mask_bytes = await self._load_asset_bytes(mask_url)
        except (httpx.HTTPError, OSError) as e:
            return self._fail_result(
                "ASSET_FETCH_FAILED", f"原图或蒙版获取失败: {type(e).__name__}", "请重试"
            )

        url = self.base_url.rstrip("/") + "/images/edits"
        files = {
            "image": ("image.png", img_bytes, "image/png"),
            "mask": ("mask.png", mask_bytes, "image/png"),
        }
        data = {
            "model": self.model,
            "prompt": task.prompt,
            "n": "1",
            "size": str(params.get("size") or "1024x1024"),
        }
        try:
            resp = await client.post(url, headers=headers, files=files, data=data, timeout=180)
        except httpx.HTTPError as e:
            logger.warning("image_edit http error: %s %s", type(e).__name__, self.base_url)
            return await self._fail(
                task, "PROVIDER_UNREACHABLE", f"无法连接 {self.base_url}: {type(e).__name__}"
            )
        if error := self._classify(resp):
            return error
        payload = resp.json()
        items = payload.get("data") or payload.get("images") or []
        first = items[0] if items and isinstance(items[0], dict) else None
        if not first:
            return self._fail_result("PROVIDER_EMPTY_RESULT", "渠道未返回重绘结果", "请稍后重试")
        out = first.get("url")
        if out and not out.startswith("/outputs/"):
            out = await self._save_remote(out, task)
        if not out and first.get("b64_json"):
            out = self._save_b64(first["b64_json"], task)
        if not out:
            return self._fail_result("PROVIDER_EMPTY_RESULT", "渠道未返回重绘图片", "请稍后重试")
        return {"status": "completed", "output_url": out}

    async def _run_image_edit_ref(
        self, client: httpx.AsyncClient, headers: dict, task: Any, params: dict
    ) -> dict:
        """参考图编辑（SiliconFlow 风格）：POST /images/generations，image 以 data URL 内嵌。

        该协议无蒙版语义（整图 + prompt 描述编辑意图），mask 参数忽略。
        """
        image_url = str(params.get("image") or "")
        if not image_url:
            return self._fail_result("PARAM_MISSING", "参考图编辑需要原图", "请重新提交")
        try:
            img_bytes = await self._load_asset_bytes(image_url)
        except (httpx.HTTPError, OSError) as e:
            return self._fail_result(
                "ASSET_FETCH_FAILED", f"原图获取失败: {type(e).__name__}", "请重试"
            )
        data_url = "data:image/png;base64," + base64.b64encode(img_bytes).decode()
        resp = await client.post(
            f"{self.base_url}/images/generations",
            headers=headers,
            json={"model": self.model, "prompt": task.prompt, "image": data_url},
        )
        if error := self._classify(resp):
            return error
        payload = resp.json()
        items = payload.get("data") or payload.get("images") or []
        first = items[0] if items and isinstance(items[0], dict) else None
        if not first:
            return self._fail_result("PROVIDER_EMPTY_RESULT", "渠道未返回编辑结果", "请稍后重试")
        url = first.get("url")
        if url and not url.startswith("/outputs/"):
            url = await self._save_remote(url, task)
        if not url and first.get("b64_json"):
            url = self._save_b64(first["b64_json"], task)
        if not url:
            return self._fail_result("PROVIDER_EMPTY_RESULT", "渠道未返回编辑图片", "请稍后重试")
        return {"status": "completed", "output_url": url}

    async def _save_remote(self, url: str, task: Any) -> str | None:
        """下载远端图片落盘 outputs/images/（失败返回原 URL 兜底）。"""

        try:
            async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
                resp = await client.get(url)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.warning("save_remote failed: %s %s", type(e).__name__, url[:80])
            return url  # 兜底：至少保留临时链接
        out_dir = self.outputs_dir or Path("outputs/images")
        out_dir.mkdir(parents=True, exist_ok=True)
        name = f"img_{task.id}_{uuid4().hex[:10]}.png"
        (out_dir / name).write_bytes(resp.content)
        return f"/outputs/images/{name}"

    @staticmethod
    def _display_text(cumulative: str) -> str:
        """流式外显文本：<think> 进行中不外显；闭合后只回正文。"""
        if "</think>" in cumulative:
            return cumulative.split("</think>", 1)[1].strip()
        if cumulative.lstrip().startswith("<think"):
            return ""
        return cumulative

    async def _run_llm(self, client: httpx.AsyncClient, headers: dict, task: Any) -> dict:
        body: dict = {
            "model": self.model,
            "messages": [{"role": "user", "content": task.prompt}],
        }
        # llm 域参数透传（temperature/max_tokens/top_p）——类型保真
        params = task.params_dict() if hasattr(task, "params_dict") else {}
        for key in passthrough_keys("llm"):
            value = params.get(key)
            if value is not None and value != "":
                body[key] = value
        resp = await client.post(
            f"{self.base_url}/chat/completions",
            headers=headers,
            json=body,
            timeout=_LLM_TIMEOUT,
        )
        if resp.status_code >= 400:
            logger.warning("llm 400 body: %s", resp.text[:300])
        if error := self._classify(resp):
            return error
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        # 推理模型（如 MiniMax-M2.7）输出含 <think> 段：剥离只留正文
        if "</think>" in content:
            content = content.split("</think>", 1)[1].strip()
        result: dict = {"status": "completed", "output_url": content}
        # D3.4 计量后端化：真实 usage 透传（流式路径无 usage，调用方回落估算）
        usage = data.get("usage") or {}
        if usage:
            pt = int(usage.get("prompt_tokens") or 0)
            ct = int(usage.get("completion_tokens") or 0)
            result["usage"] = {
                "prompt_tokens": pt,
                "completion_tokens": ct,
                "total_tokens": int(usage.get("total_tokens") or pt + ct),
            }
        return result

    async def _run_llm_stream(
        self,
        client: httpx.AsyncClient,
        headers: dict,
        task: Any,
        on_event: Any,
    ) -> dict:
        """流式 LLM：SSE chunk → 累计文本 on_event("delta", {text})（幂等语义，重放安全）。"""
        import json as _json

        req_body: dict = {
            "model": self.model,
            "messages": [{"role": "user", "content": task.prompt}],
            "stream": True,
        }
        params = task.params_dict() if hasattr(task, "params_dict") else {}
        for key in passthrough_keys("llm"):
            value = params.get(key)
            if value is not None and value != "":
                req_body[key] = value

        content = ""
        try:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=req_body,
            ) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode(errors="replace")[:200]
                    logger.warning("llm stream %s: %s", resp.status_code, body)
                    if error := self._classify(resp):
                        return error
                    return self._fail_result(
                        "PROVIDER_SERVER_ERROR", f"渠道返回 {resp.status_code}", "请稍后重试"
                    )
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    payload = line[6:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        delta = _json.loads(payload)["choices"][0]["delta"].get("content")
                    except (KeyError, IndexError, ValueError):
                        continue
                    if delta:
                        content += delta
                        on_event("delta", {"text": self._display_text(content)})
        except httpx.TimeoutException:
            return self._fail_result("PROVIDER_TIMEOUT", "渠道响应超时", "请稍后重试")
        except httpx.HTTPError as e:
            logger.warning("llm stream http error: %s %s", type(e).__name__, self.base_url)
            return self._fail_result("PROVIDER_UNREACHABLE", "渠道连接失败", "请检查网络")

        if not content:
            return self._fail_result("PROVIDER_EMPTY_RESULT", "渠道未返回内容", "请重试")
        final = content.split("</think>", 1)[1].strip() if "</think>" in content else content
        return {"status": "completed", "output_url": final}

    async def _run_audio(self, client: httpx.AsyncClient, headers: dict, task: Any) -> dict:
        audio_params = task.params_dict() if hasattr(task, "params_dict") else {}
        audio_body: dict = {
            "model": self.model,
            "input": task.prompt,
            "voice": audio_params.get("voice", "alloy"),
        }
        for key in ("speed", "format"):
            value = audio_params.get(key)
            if value is not None and value != "":
                audio_body[key] = value
        resp = await client.post(
            f"{self.base_url}/audio/speech",
            headers=headers,
            json=audio_body,
        )
        if error := self._classify(resp):
            return error
        out_dir = self.outputs_dir or Path("outputs/audio")
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"audio_{task.id}_{uuid4().hex[:10]}.mp3"
        path.write_bytes(resp.content)
        return {"status": "completed", "output_url": f"/outputs/audio/{path.name}"}

    # ---- 错误分类 ----
    def _classify(self, resp: httpx.Response) -> dict | None:
        if resp.status_code < 400:
            return None
        code_map = {
            401: "CHANNEL_KEY_INVALID",
            403: "CHANNEL_KEY_INVALID",
            404: "PROVIDER_MODEL_NOT_FOUND",
            429: "PROVIDER_RATE_LIMITED",
        }
        code = code_map.get(resp.status_code, "PROVIDER_SERVER_ERROR")
        hint = {
            "CHANNEL_KEY_INVALID": "请在「设置 → 模型渠道」更新密钥",
            "PROVIDER_MODEL_NOT_FOUND": "该模型可能已下线，请更换模型",
            "PROVIDER_RATE_LIMITED": "触发渠道限流，请稍后重试",
            "PROVIDER_SERVER_ERROR": "渠道服务异常，请稍后重试",
        }.get(code, "请稍后重试")
        return self._fail_result(code, f"渠道返回 {resp.status_code}", hint)

    def _fail_result(self, code: str, message: str, hint: str = "") -> dict:
        return {"status": "failed", "error": message, "error_code": code, "error_hint": hint}

    async def _fail(self, task: Any, code: str, message: str) -> dict:  # noqa: ANN001
        return self._fail_result(code, message)

    def _image_count(self, task: Any) -> dict:  # noqa: ANN001
        params = task.params_dict() if hasattr(task, "params_dict") else {}
        n = params.get("image_count", 1)
        return {"n": max(1, min(int(n), 4))}
