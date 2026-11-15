"""系统设置路由：默认模型（capability_config KV，管理员写全员读）。"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from backend.app.core.db import get_db
from backend.app.models import CapabilityConfig, ProviderChannel, User
from backend.app.models.provider import PROVIDER_DOMAINS
from backend.app.routers.auth import get_current_user

router = APIRouter(prefix="/api/settings", tags=["settings"])

KV_KEY = "default_models"


class DomainDefault(BaseModel):
    channel_id: int
    model: str = Field(..., min_length=1)


class DefaultModelsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")  # 未知域键直接 422
    # 四域键 + vision（视觉能力标记的 llm 模型，反推等图生文场景的默认）
    image: DomainDefault | None = None
    video: DomainDefault | None = None
    llm: DomainDefault | None = None
    audio: DomainDefault | None = None
    vision: DomainDefault | None = None


def _read(db: Session) -> dict:
    row = db.get(CapabilityConfig, KV_KEY)
    if row is None:
        return {}
    try:
        value = json.loads(str(row.value_json or "{}"))
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}


def _write(db: Session, value: dict) -> None:
    row = db.get(CapabilityConfig, KV_KEY)
    serialized = json.dumps(value, ensure_ascii=False)
    if row is None:
        db.add(CapabilityConfig(key=KV_KEY, value_json=serialized))
    else:
        row.value_json = serialized
    db.commit()


@router.get("/default-models")
def get_default_models(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    defaults = _read(db)
    # vision 读取侧回落：未配置/失效（含对当前用户不可见的个人渠道）时选第一个可用
    if not defaults.get("vision") or not _vision_alive(db, defaults["vision"], user.id):
        fb = _first_vision_model(db)
        if fb:
            defaults["vision"] = fb
    return {"defaults": defaults}


def _vision_alive(db: Session, item: dict, user_id: int | None = None) -> bool:

    from backend.app.models import ProviderChannel, ProviderModel

    ch = db.get(ProviderChannel, item.get("channel_id") or -1)
    if ch is None or ch.domain != "llm" or not ch.enabled:
        return False
    # 用户维度：个人渠道对他人不可见（2026-10-09 walker 实证——配置者可用，
    # 其他用户须回落系统级 vision 模型，否则 resolver 带错模型名打错渠道）
    if user_id is not None and ch.user_id is not None and ch.user_id != user_id:
        return False
    m = (
        db.query(ProviderModel)
        .filter(
            ProviderModel.channel_id == ch.id,
            ProviderModel.model_id == (item.get("model") or ""),  # 括号：or 优先级歧义实证
            ProviderModel.enabled == 1,
        )
        .first()
    )
    return bool(m and ProviderModel._loads_or_empty(m.meta_json).get("vision"))


def _first_vision_model(db: Session) -> dict | None:
    """全库扫描第一个启用的 vision 标记 llm 模型（渠道按 sort_order）。"""
    from backend.app.models import ProviderChannel, ProviderModel

    rows = (
        db.query(ProviderModel, ProviderChannel)
        .join(ProviderChannel, ProviderChannel.id == ProviderModel.channel_id)
        .filter(
            ProviderChannel.domain == "llm",
            ProviderChannel.enabled == 1,
            ProviderModel.enabled == 1,
        )
        .order_by(ProviderChannel.sort_order, ProviderModel.model_id)
        .all()
    )
    for m, ch in rows:
        if ProviderModel._loads_or_empty(m.meta_json).get("vision"):
            return {"channel_id": ch.id, "model": m.model_id}
    return None


@router.put("/default-models")
def put_default_models(
    body: DefaultModelsPayload,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    if user.role != "admin":
        raise HTTPException(
            status_code=403, detail={"code": "FORBIDDEN", "message": "需要管理员权限"}
        )
    payload = body.model_dump(exclude_none=True)
    for domain, item in payload.items():
        if domain not in PROVIDER_DOMAINS and domain != "vision":
            raise HTTPException(
                status_code=422, detail={"code": "DOMAIN_INVALID", "message": f"未知域: {domain}"}
            )
        if not item["model"].strip():
            raise HTTPException(
                status_code=422, detail={"code": "MODEL_EMPTY", "message": "模型名不能为空"}
            )
        # vision 不是渠道域：候选渠道必须 llm 域，且模型带 vision 能力标记
        need_domain = "llm" if domain == "vision" else domain
        ch = db.get(ProviderChannel, item["channel_id"])
        if ch is None or ch.domain != need_domain:
            raise HTTPException(
                status_code=422,
                detail={"code": "CHANNEL_INVALID", "message": f"{domain} 默认渠道不存在或不匹配"},
            )
        if domain == "vision":
            from backend.app.models import ProviderModel

            m = (
                db.query(ProviderModel)
                .filter(ProviderModel.channel_id == ch.id, ProviderModel.model_id == item["model"])
                .first()
            )
            meta = ProviderModel._loads_or_empty(m.meta_json) if m else {}
            if not meta.get("vision"):
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "MODEL_NOT_VISION",
                        "message": "该模型未标记 vision 能力（可在模型编辑面板勾选）",
                    },
                )
    _write(db, payload)
    return {"defaults": payload}


# ---------- Agent API Key（画布级 Agent API 认证）----------


def _new_api_key() -> str:
    import secrets

    return f"cg-{secrets.token_urlsafe(24)}"


def _mask(key: str) -> str:
    return key[:8] + "*" * 12 + key[-4:]


@router.post("/api-key", status_code=201)
def generate_api_key(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    """生成/轮换 Agent API Key（旧 key 立即失效）。"""
    user.api_key = _new_api_key()
    db.commit()
    return {"api_key": user.api_key}


@router.get("/api-key")
def get_api_key(
    reveal: bool = Query(False),
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
) -> dict:
    """当前 key（默认脱敏；reveal=true 显式取回明文——自托管取回能力，登录态保护）。"""
    if not user.api_key:
        return {"api_key": None}
    return {"api_key": user.api_key if reveal else _mask(user.api_key)}


@router.delete("/api-key")
def revoke_api_key(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    """撤销 API Key。"""
    user.api_key = None
    db.commit()
    return {"revoked": True}


def get_user_by_api_key(db: Session, key: str) -> User | None:
    """X-API-Key → User（Agent API 认证入口）。"""
    if not key or not key.startswith("cg-"):
        return None
    return db.query(User).filter(User.api_key == key).first()
