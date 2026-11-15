"""参数域 schema：四域标准语义键 + 透传声明 + 默认值注入。

spec: docs/superpowers/specs/2026-10-07-model-config.md §6
设计原则（spec §3）：配置层只存语义参数，协议差异由 Provider 适配层吸收——
本模块的键是语义键；它们到上游时变成什么（REST 字段/异步任务参数/workflow 变量）
由各 Provider 自行映射。新域/新键=改常量，零迁移。
"""

from __future__ import annotations

from typing import Any, TypedDict

from sqlalchemy.orm import Session


class ParamSpec(TypedDict):
    type: str  # "str" | "int" | "float" | "bool"
    passthrough: bool  # True=按域白名单透传上游；False=平台内部消费（如 image_count 乘数）
    placeholder: str  # 前端占位示例


PARAM_SCHEMA: dict[str, dict[str, ParamSpec]] = {
    "image": {
        "size": {"type": "str", "passthrough": True, "placeholder": "1024x1024"},
        "seed": {"type": "int", "passthrough": True, "placeholder": "0（随机）"},
        "steps": {"type": "int", "passthrough": True, "placeholder": "20"},
        "guidance": {"type": "float", "passthrough": True, "placeholder": "3.5"},
        "negative_prompt": {"type": "str", "passthrough": True, "placeholder": "不希望出现的内容"},
        "image_count": {"type": "int", "passthrough": False, "placeholder": "1"},
        "i2i_denoise": {"type": "float", "passthrough": True, "placeholder": "0.75"},
    },
    "video": {
        "duration": {"type": "int", "passthrough": True, "placeholder": "6"},
        "resolution": {"type": "str", "passthrough": True, "placeholder": "768p"},
        "with_audio": {"type": "bool", "passthrough": True, "placeholder": "true"},
        "camera_fixed": {"type": "bool", "passthrough": True, "placeholder": "false"},
        "seed": {"type": "int", "passthrough": True, "placeholder": "0（随机）"},
    },
    "llm": {
        "temperature": {"type": "float", "passthrough": True, "placeholder": "0.7"},
        "max_tokens": {"type": "int", "passthrough": True, "placeholder": "2048"},
        "top_p": {"type": "float", "passthrough": True, "placeholder": "0.9"},
    },
    "audio": {
        "voice": {"type": "str", "passthrough": True, "placeholder": "female-shaonv"},
        "speed": {"type": "float", "passthrough": True, "placeholder": "1.0"},
        "format": {"type": "str", "passthrough": True, "placeholder": "mp3"},
    },
}


def passthrough_keys(domain: str) -> tuple[str, ...]:
    """该域可透传上游的语义键（原三键 size/negative_prompt/i2i_denoise 保持兼容）。"""
    keys = PARAM_SCHEMA.get(domain, {})
    return tuple(k for k, spec in keys.items() if spec["passthrough"])


def param_placeholder(domain: str) -> dict:
    """前端 params_json 编辑框的占位示例（按域生成）。"""
    return {k: spec["placeholder"] for k, spec in PARAM_SCHEMA.get(domain, {}).items()}


# 模型默认参数不得覆盖的路由/协议保留键（防 params_json 劫持任务路由）
RESERVED_PARAM_KEYS = frozenset({"channel_id", "prompt_id"})


def merge_model_param_defaults(
    db: Session, domain: str, model: str, explicit: dict[str, Any] | None
) -> dict[str, Any]:
    """任务创建时注入模型默认参数：{**defaults, **explicit}（显式覆盖，浅合并）。

    defaults 中的路由/协议保留键（channel_id/prompt_id）被剔除——params_json
    不得劫持任务路由（Final review Fix 2）。未配置模型/坏 JSON → explicit 原样。
    同名模型跨渠道归一取首个（与 resolve_model_pricing 归一语义一致）。
    """
    from backend.app.models import ProviderChannel, ProviderModel

    defaults: dict[str, Any] = {}
    if model:
        q = db.query(ProviderModel).join(
            ProviderChannel, ProviderModel.channel_id == ProviderChannel.id
        )
        # 五遍复检 G2：按域过滤——同名模型在 image/llm 渠道各有默认参数时互不污染
        if domain:
            q = q.filter(ProviderChannel.domain == domain)
        row = q.filter(ProviderModel.model_id == model).first()
        if row is not None:
            defaults = ProviderModel._loads_or_empty(row.params_json)
            defaults = {k: v for k, v in defaults.items() if k not in RESERVED_PARAM_KEYS}
    return {**defaults, **(explicit or {})}
