"""provider 渠道路由：CRUD + 连通性测试（key 全程服务端）。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import case as sa_case
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from backend.app.core.crypto import decrypt_secret
from backend.app.core.db import get_db
from backend.app.core.errors import ApiError
from backend.app.models import ProviderChannel, Task, User
from backend.app.provider_presets import PROVIDER_PRESETS
from backend.app.routers.auth import get_current_user
from backend.app.schemas.provider import (
    ChannelCreateRequest,
    ChannelListResponse,
    ChannelResponse,
    ChannelTestResponse,
    ChannelUpdateRequest,
    ModelItem,
    ModelUpdateRequest,
)
from backend.app.services import provider_service
from backend.app.services.provider_service import ChannelError

router = APIRouter(prefix="/api/providers", tags=["providers"])


def _to_response(ch: ProviderChannel) -> ChannelResponse:
    if not ch.key_encrypted:
        masked = ""  # 本地渠道（无需密钥）
    else:
        try:
            masked = provider_service.mask_key(decrypt_secret(ch.key_encrypted))
        except Exception:  # noqa: BLE001 — 解密失败（如更换 ENCRYPTION_KEY）也不泄露密文
            masked = "***"
    return ChannelResponse(
        id=ch.id,
        domain=ch.domain,
        name=ch.name,
        base_url=ch.base_url,
        key_masked=masked,
        enabled=bool(ch.enabled),
        sort_order=ch.sort_order,
        user_id_owned=ch.user_id is not None,
        capabilities=ch._capabilities_list(),
        models=[
            ModelItem(
                id=m.id,
                model_id=m.model_id,
                display_name=m.display_name or m.model_id,
                enabled=bool(m.enabled),
                params_json=m.to_dict()["params_json"],
                meta_json=m.to_dict()["meta_json"],
                is_free=bool(m.is_free),
                pricing_unit=m.pricing_unit,
                price_input=m.price_input,
                price_output=m.price_output,
            )
            for m in ch.models or []
        ],
    )


@router.get("/presets")
def list_presets(user: User = Depends(get_current_user)) -> dict:
    """中立预设目录（BYOK）：常用开放服务商接入参考，点选即填，不绑定任何单一服务商。"""
    return {"presets": PROVIDER_PRESETS}


@router.get("/channels", response_model=ChannelListResponse)
def list_channels(db: Session = Depends(get_db), user: User = Depends(get_current_user)):

    # 系统渠道（user_id IS NULL，管理员配）+ 个人渠道，都可见
    items = (
        db.query(ProviderChannel)
        .filter(
            or_(
                ProviderChannel.user_id == user.id,
                ProviderChannel.user_id.is_(None),
            )
        )
        .order_by(ProviderChannel.sort_order, ProviderChannel.id)
        .all()
    )
    return {"channels": [_to_response(ch) for ch in items]}


@router.post("/channels", response_model=ChannelResponse, status_code=201)
def create_channel(
    body: ChannelCreateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        ch = provider_service.create_channel(
            db,
            user_id=user.id,
            domain=body.domain,
            name=body.name,
            base_url=body.base_url,
            key_plain=body.key,
            capabilities=body.capabilities,
            models=[m.model_dump() for m in body.models] if body.models else None,
        )
    except ChannelError as e:
        raise HTTPException(status_code=422, detail={"code": e.code, "message": e.message}) from e
    return _to_response(ch)


@router.patch("/channels/{channel_id}/models/{model_pk}", response_model=ChannelResponse)
def update_channel_model(
    channel_id: int,
    model_pk: int,
    body: ModelUpdateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """模型级配置部分更新（spec §7）：owner 渠道可改；系统渠道仅管理员。

    寻址用数字主键而非 model_id——SiliconFlow 风格 ID 含 '/'（Tongyi-MAI/Z-Image-Turbo），
    字符串寻址在路径上不可路由（五遍复检 G1）。
    """
    ch = provider_service.get_channel_for_user(db, user.id, channel_id)
    if ch is None and user.role == "admin":
        # 系统渠道（user_id NULL）：管理员可配
        ch = db.query(ProviderChannel).filter(ProviderChannel.id == channel_id).first()
    if ch is None:
        raise HTTPException(
            status_code=404, detail={"code": "CHANNEL_NOT_FOUND", "message": "渠道不存在"}
        )
    model = next((m for m in (ch.models or []) if m.id == model_pk), None)
    if model is None:
        raise HTTPException(
            status_code=404, detail={"code": "MODEL_NOT_FOUND", "message": "模型不存在"}
        )
    # 复检修复：①校验基于合并态（存量配置 + 本次 patch），防已免费模型被误拒；
    # ②model_fields_set 区分「字段未传」与「显式 null」——后者 = 清除配置
    fields = body.model_fields_set

    def _sent(field: str) -> bool:
        return field in fields

    effective_free = (
        bool(body.is_free) if _sent("is_free") and body.is_free is not None else bool(model.is_free)
    )
    effective_unit = body.pricing_unit if _sent("pricing_unit") else model.pricing_unit
    effective_price = body.price_input if _sent("price_input") else model.price_input
    try:
        provider_service.validate_model_pricing(effective_unit, effective_free, effective_price)
    except ChannelError as e:
        raise HTTPException(status_code=422, detail={"code": e.code, "message": e.message}) from e
    import json as _json

    if _sent("display_name") and body.display_name is not None:
        model.display_name = body.display_name.strip()
    if _sent("enabled") and body.enabled is not None:
        model.enabled = 1 if body.enabled else 0
    if _sent("params_json"):
        model.params_json = _json.dumps(body.params_json or {}, ensure_ascii=False)  # null=清除
    if _sent("meta_json"):
        model.meta_json = _json.dumps(body.meta_json or {}, ensure_ascii=False)  # null=清除
    if _sent("is_free") and body.is_free is not None:
        model.is_free = 1 if body.is_free else 0
    if _sent("pricing_unit"):
        model.pricing_unit = body.pricing_unit  # 显式 null = 清除（回落内置建议价）
    if _sent("price_input"):
        model.price_input = body.price_input
    if _sent("price_output"):
        model.price_output = body.price_output
    db.commit()
    db.refresh(ch)
    return _to_response(ch)


@router.patch("/channels/{channel_id}", response_model=ChannelResponse)
def update_channel(
    channel_id: int,
    body: ChannelUpdateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # 个人渠道本人可改；系统渠道（user_id NULL）仅管理员——查询需同时覆盖两类
    ch = provider_service.get_channel_for_test(db, user.id, channel_id)
    if ch is None or (ch.user_id is None and user.role != "admin"):
        raise HTTPException(
            status_code=404, detail={"code": "CHANNEL_NOT_FOUND", "message": "渠道不存在"}
        )
    ch = provider_service.update_channel(
        db,
        ch,
        name=body.name,
        base_url=body.base_url,
        key_plain=body.key,
        enabled=body.enabled,
        capabilities=body.capabilities,
    )
    return _to_response(ch)


@router.delete("/channels/{channel_id}", status_code=204)
def delete_channel(
    channel_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> None:
    ch = provider_service.get_channel_for_test(db, user.id, channel_id)
    if ch is None or (ch.user_id is None and user.role != "admin"):
        raise HTTPException(
            status_code=404, detail={"code": "CHANNEL_NOT_FOUND", "message": "渠道不存在"}
        )
    provider_service.delete_channel(db, ch)


@router.get("/health")
def channel_health(
    days: int = 7,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """渠道健康度（0.7.1）：近 N 天按模型聚合 总数/成功/失败/成功率/平均耗时。

    注：tasks 表未落 channel_id，按模型聚合（模型是用户视角的"渠道"实体）。
    健康度重设计（2026-10-07）：全渠道禁用的模型不再计入（历史任务噪音）。
    """
    days = max(1, min(days, 90))
    since = datetime.now(UTC) - timedelta(days=days)
    # 全渠道均禁用的 model_id（某渠道仍启用则保留——同名跨域不误伤）
    from backend.app.models import ProviderModel

    dead_rows = (
        db.query(ProviderModel.model_id)
        .group_by(ProviderModel.model_id)
        .having(func.sum(ProviderModel.enabled) == 0)
        .all()
    )
    dead_models = {r[0] for r in dead_rows}
    rows = (
        db.query(
            Task.model,
            func.count(Task.id),
            func.sum(sa_case((Task.status == "completed", 1), else_=0)),
            func.sum(sa_case((Task.status == "failed", 1), else_=0)),
            func.avg(Task.duration_ms),
        )
        .filter(Task.user_id == user.id, Task.created_at >= since)
        .group_by(Task.model)
        .all()
    )
    health = []
    for model, total, completed, failed, avg_ms in rows:
        if model in dead_models:
            continue
        total_i = int(total or 0)
        failed_i = int(failed or 0)
        completed_i = int(completed or 0)
        finished = completed_i + failed_i
        health.append(
            {
                "model": model,
                "total": total_i,
                "completed": completed_i,
                "failed": failed_i,
                "success_rate": round(completed_i / finished * 100, 1) if finished else None,
                "avg_duration_ms": int(avg_ms) if avg_ms is not None else None,
            }
        )
    health.sort(key=lambda e: (-(e["total"]), e["model"]))
    return {"days": days, "health": health}


@router.post("/channels/{channel_id}/test", response_model=ChannelTestResponse)
def test_channel(
    channel_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    ch = provider_service.get_channel_for_test(db, user.id, channel_id)
    if ch is None:
        raise HTTPException(
            status_code=404, detail={"code": "CHANNEL_NOT_FOUND", "message": "渠道不存在"}
        )
    try:
        result = provider_service.test_connection(db, ch)
    except ApiError as e:
        return ChannelTestResponse(ok=False, code=e.code, message=e.message)
    return ChannelTestResponse(**result)
