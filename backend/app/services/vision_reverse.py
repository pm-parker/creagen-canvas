"""真·看图反推：默认视觉模型 + 多模态 content blocks + 预算治理。

spec 源起：反推 v1 只扩写旧提示词（LLM 没看过图）——质量无保障（2026-10-09
对话实证）。v2 把图（站内 /outputs/ 读出转 base64 data URL）真正送进 vision
标记模型，六要素（主体/外观/场景/光线/构图/风格）引导，一段式中文输出。
"""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy.orm import Session

from backend.app.models import User
from backend.app.services.agent_brain import strip_think
from backend.app.services.llm_budget import BudgetExceeded, budget_check, budget_consume

_TIMEOUT = httpx.Timeout(120.0, connect=10.0)

_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}

_SYSTEM_PROMPT = (
    "你是视觉提示词工程师。请仔细观察用户提供的图片，反推出一段可直接用于 AI 绘画"
    "复现该图的中文提示词。要求：① 覆盖六要素——主体、外观细节、场景环境、光线、"
    "构图、画面风格；② 严格以图中实际内容为准，不编造图里不存在的东西；"
    "③ 输出一段连续提示词（80-200 字），不要分点、不要解释、不要加引号。"
)


class VisionReverseError(ValueError):
    def __init__(self, code: str, message: str, hint: str = ""):
        self.code = code
        self.message = message
        self.hint = hint
        super().__init__(message)


def _outputs_root() -> Path:
    from backend.app.services.media_service import _outputs_root_default

    return _outputs_root_default()


def _read_local_image(image_url: str) -> tuple[bytes, str]:
    """站内 /outputs/ 图片 → (bytes, mime)。路径穿越/不存在拒绝。"""
    if not image_url.startswith("/outputs/") or ".." in image_url:
        raise VisionReverseError("IMAGE_INVALID", "仅支持站内 /outputs/ 图片路径")
    p = (_outputs_root() / image_url.removeprefix("/outputs/")).resolve()
    root = _outputs_root().resolve()
    if root not in p.parents:
        raise VisionReverseError("IMAGE_INVALID", "非法图片路径")
    if not p.is_file():
        raise VisionReverseError("IMAGE_INVALID", "图片不存在")
    mime = _MIME_BY_EXT.get(p.suffix.lower()) or mimetypes.guess_type(p.name)[0] or "image/png"
    return p.read_bytes(), str(mime)


def _resolve_vision_provider(db: Session, user_id: int):  # noqa: ANN202
    """默认视觉模型解析：显式配置（校验存活）→ 回落第一个可用 vision 标记模型。"""
    from backend.app.routers.settings import _first_vision_model, _read, _vision_alive
    from backend.app.services.provider_service import resolve_provider_for_task

    cfg = _read(db).get("vision")
    if not cfg or not _vision_alive(db, cfg, user_id):
        cfg = _first_vision_model(db)
    if not cfg:
        raise VisionReverseError(
            "VISION_NOT_CONFIGURED",
            "未配置默认视觉模型",
            "在「设置 → 默认模型 → 视觉」选择，或在渠道页为模型勾选「支持参考图」",
        )
    provider, ch = resolve_provider_for_task(
        db, user_id, "llm", cfg["model"], channel_id=cfg["channel_id"]
    )
    return provider, ch


async def reverse_prompt(db: Session, *, user: User, image_url: str) -> dict[str, Any]:
    """看图反推主入口：预算检查 → 读图转 data URL → 多模态调用 → 剥 think → 记账。"""
    try:
        budget_check(db, f"llm:{user.id}")
    except BudgetExceeded as e:
        raise VisionReverseError("BUDGET_EXCEEDED", str(e), "当日 LLM 预算已耗尽") from e

    provider, ch = _resolve_vision_provider(db, user.id)  # 未配置快速失败（优先于图校验）

    img_bytes, mime = _read_local_image(image_url)  # 再验图，才烧 token
    data_url = f"data:{mime};base64,{base64.b64encode(img_bytes).decode()}"
    from backend.app.providers.openai_compat import OpenAICompatProvider

    if not isinstance(provider, OpenAICompatProvider):
        raise VisionReverseError("BRAIN_PROVIDER_UNSUPPORTED", "该视觉渠道类型不支持多模态调用")

    body = {
        "model": provider.model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "请反推这张图片的绘画提示词。"},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ],
        "temperature": 0.3,
    }
    headers = {"Authorization": f"Bearer {provider.key_plain}"} if provider.key_plain else {}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            f"{provider.base_url}/chat/completions", headers=headers, json=body
        )
    if resp.status_code >= 400:
        raise VisionReverseError(
            "VISION_UPSTREAM_ERROR", f"视觉模型返回 {resp.status_code}", resp.text[:200]
        )
    data = resp.json()
    content = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    reversed_text = strip_think(content).strip()
    if not reversed_text:
        raise VisionReverseError("VISION_EMPTY_OUTPUT", "视觉模型未返回有效内容")

    usage = data.get("usage") or {}
    pt = int(usage.get("prompt_tokens") or 0)
    ct = int(usage.get("completion_tokens") or 0)
    if pt or ct:
        budget_consume(db, f"llm:{user.id}", pt, ct)

    return {
        "reversed": reversed_text,
        "model": provider.model,
        "channel_id": ch.id if ch else None,
    }
