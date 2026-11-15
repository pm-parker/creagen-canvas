"""模型配置价解析：模型配置优先 → 内置建议价回落（spec §5）。

resolve_model_pricing：查 provider_models 配置（指定渠道优先，同名归一）。
resolve_cost：命中配置按单位计价；未命中回落 calculate_cost（内置表→fallback）。
is_free 短路在 unit 之前（Review Focus 5）；price_input=0 是合法配置（Review Focus 3）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from backend.app.services.cost_calculator import calculate_cost


def resolve_model_pricing(
    db: Session, model_id: str, channel_id: int | None = None, domain: str | None = None
) -> dict[str, Any] | None:
    """查模型计价配置（spec §5.1 查找顺序）：
    指定渠道 → 该 model_id 任意启用渠道（同名归一回退）→ None。
    domain 过滤（五遍复检 G2）：同名模型跨域（image/llm）各有计价时互不串。
    """
    from backend.app.models import ProviderChannel, ProviderModel

    base_q = (
        db.query(ProviderModel)
        .join(ProviderChannel, ProviderModel.channel_id == ProviderChannel.id)
        .filter(
            ProviderModel.model_id == model_id,
            ProviderChannel.enabled == 1,
            ProviderModel.pricing_unit.isnot(None),
        )
    )
    if domain:
        base_q = base_q.filter(ProviderChannel.domain == domain)
    row = None
    if channel_id is not None:
        row = base_q.filter(ProviderModel.channel_id == channel_id).first()
    if row is None:
        row = base_q.first()  # 回退：同名归一（与 merge_model_param_defaults 同语义）
    if row is None:
        return None
    return {
        "is_free": bool(row.is_free),
        "unit": row.pricing_unit,
        "price_input": row.price_input,
        "price_output": row.price_output,
        "meta": ProviderModel._loads_or_empty(row.meta_json),
    }


def resolve_cost(
    db: Session,
    task_type: str,
    model: str,
    params: dict[str, Any] | None,
    channel_id: int | None = None,
    usage: dict[str, Any] | None = None,
    prompt: str | None = None,
) -> float:
    """单个任务成本（CNY）：配置价优先，回落内置建议价。公式见 spec §5.2。

    prompt：per_character 计价基数（TTS 文本在 task.prompt，不在 params）。
    """
    params = params or {}
    pricing = resolve_model_pricing(db, model, channel_id=channel_id, domain=task_type)
    if pricing is None:
        return calculate_cost(task_type, model, params)
    if pricing["is_free"]:
        return 0.0
    unit = pricing["unit"]
    price_in = pricing["price_input"]
    if price_in is None:
        return calculate_cost(task_type, model, params)
    if unit == "per_image":
        count = params.get("image_count", 1) or 1
        return round(float(price_in) * int(count), 4)
    if unit == "per_second":
        duration = params.get("duration", 6) or 6
        return round(float(price_in) * float(duration), 4)
    if unit == "per_character":
        text = prompt if prompt is not None else (params.get("text") or "")
        return round(float(price_in) * (len(str(text)) / 1000), 4)
    if unit == "per_token_1m":
        if not usage:
            return 0.0  # 预估阶段与现状一致（完成时由 worker 按 usage 精确补记）
        pt = float(usage.get("prompt_tokens") or 0)
        ct = float(usage.get("completion_tokens") or 0)
        price_out = float(pricing["price_output"] or 0)
        return round(pt / 1_000_000 * float(price_in) + ct / 1_000_000 * price_out, 4)
    if unit == "per_request":
        return round(float(price_in), 4)
    # 未知单位（防御）：回落内置表
    return calculate_cost(task_type, model, params)
