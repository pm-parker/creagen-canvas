"""单任务成本计算（CNY）。

derived from creagen prod app/services/cost_calculator.py @ 2026-09-27。
开源版无本地 GPU 推理：剔除本地模型定价；llm/audio 域 M1 以 token 预算治理（llm_budget），
现金计价按 0 记，后续版本扩展按量计费。
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("canvas.cost")

# ---- 生图（CNY / 张）—— 云端 API ----
IMAGE_PRICE: dict[str, float] = {
    "Kwai-Kolors/Kolors": 0.0,  # SiliconFlow 免费档
    "Tongyi-MAI/Z-Image-Turbo": 0.1,
    "Tongyi-MAI/Z-Image": 0.3,
    "baidu/ERNIE-Image-Turbo": 0.11,
    "Qwen/Qwen-Image": 0.3,
    "Qwen/Qwen-Image-Edit-2509": 0.3,
    "Qwen/Qwen-Image-Edit": 0.3,
    "minimax-image-01": 0.07,  # MiniMax image-01 ($0.01 ≈ ¥0.07)
    "z-image-turbo": 0.0,  # 本地 asus 4090（ComfyUI int8），无现金成本
}

# ---- 生视频（CNY / 秒）—— 云端 API ----
VIDEO_PRICE: dict[str, float] = {
    "minimax-hailuo-2.3-fast-768p": 0.225,
    "minimax-hailuo-2.3-fast-1080p": 0.385,
    "minimax-hailuo-2.3-768p": 0.333,
    "minimax-hailuo-2.3-1080p": 0.400,
    "MiniMax-Hailuo-2.3-Fast": 0.225,
    "MiniMax-Hailuo-2.3": 0.333,
    "seedance": 1.0,
    "minimax-h3-video": 0.0,  # H3 本地 GPU（asus 4090），无现金成本
}

IMAGE_FALLBACK_PRICE = 0.1
VIDEO_FALLBACK_PRICE = 0.225


def calculate_cost(task_type: str, model: str, params: dict[str, Any] | None = None) -> float:
    """单个生成任务的现金成本（CNY）。

    params 可含: image_count / duration(秒)
    """
    params = params or {}
    if task_type == "image":
        return _image_cost(model, params)
    if task_type == "video":
        return _video_cost(model, params)
    # llm / audio：M1 用 token 预算治理，现金成本记 0
    return 0.0


def _image_cost(model: str, params: dict[str, Any]) -> float:
    key = (model or "").lower().strip()
    price = IMAGE_PRICE.get(model, IMAGE_PRICE.get(key, IMAGE_FALLBACK_PRICE))
    try:
        count = int(params.get("image_count", 1))
    except (TypeError, ValueError):
        count = 1
    return round(price * count, 6)


def _video_cost(model: str, params: dict[str, Any]) -> float:
    key = (model or "").lower().strip()
    price = VIDEO_PRICE.get(model, VIDEO_PRICE.get(key, VIDEO_FALLBACK_PRICE))
    try:
        duration = float(params.get("duration", 6))  # MiniMax Hailuo 默认 6s
    except (TypeError, ValueError):
        duration = 6.0
    return round(price * duration, 6)


def parse_params(params_str: str | dict | None) -> dict[str, Any]:
    """params JSON 串/字典安全解析。"""
    if isinstance(params_str, dict):
        return params_str
    if isinstance(params_str, str) and params_str:
        try:
            data = json.loads(params_str)
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}
