"""provider 渠道服务：加密落库 / 掩码回显 / 连通性测试。"""

from __future__ import annotations

import json
import re
import time

from sqlalchemy import or_
from sqlalchemy.orm import Session

from backend.app.core.crypto import decrypt_secret, encrypt_secret
from backend.app.models import ProviderChannel
from backend.app.models.provider import PROVIDER_DOMAINS
from backend.app.providers.registry import ProviderBase

_URL_RE = re.compile(r"^https?://[^\s]+$")


class ChannelError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def _safe_decrypt(token: str) -> str:
    """空密钥（本地渠道）直通；解密失败按空处理（如更换过 ENCRYPTION_KEY）。"""
    if not token:
        return ""
    try:
        return decrypt_secret(token)
    except Exception:  # noqa: BLE001
        return ""


def mask_key(key_plain: str) -> str:
    """sk-abcd1234 → sk-***1234；非可识别前缀只留末 4 位。"""
    if not key_plain:
        return ""
    prefix = key_plain.split("-", 1)[0] + "-***" if "-" in key_plain[:8] else "***"
    return f"{prefix}{key_plain[-4:]}"


def validate_domain(domain: str) -> None:
    if domain not in PROVIDER_DOMAINS:
        raise ChannelError("CHANNEL_DOMAIN_INVALID", f"不支持的渠道域: {domain}")


def validate_base_url(base_url: str) -> None:
    if not _URL_RE.match(base_url or ""):
        raise ChannelError("CHANNEL_URL_INVALID", "Base URL 需为 http(s):// 开头的合法地址")


def validate_model_pricing(unit: str | None, is_free: bool, price_input: float | None) -> None:
    """模型计价校验（创建/更新共用，五遍复检 G4 去重）：枚举 + 非免费时单价必填。"""
    from backend.app.models.provider import PRICING_UNITS

    if unit is not None and unit not in PRICING_UNITS:
        raise ChannelError(
            "MODEL_CONFIG_INVALID", f"pricing_unit 必须是 {'/'.join(PRICING_UNITS)}"
        )
    if unit is not None and not is_free and price_input is None:
        raise ChannelError(
            "MODEL_CONFIG_INVALID", "计价单位已配置时 price_input 必填（免费请设 is_free）"
        )


def create_channel(
    db: Session,
    *,
    user_id: int | None,
    domain: str,
    name: str,
    base_url: str,
    key_plain: str,
    capabilities: list[str] | None = None,
    models: list[dict] | None = None,
) -> ProviderChannel:
    validate_domain(domain)
    validate_base_url(base_url)
    ch = ProviderChannel(
        user_id=user_id,
        domain=domain,
        name=name.strip(),
        base_url=base_url.strip(),
        key_encrypted=encrypt_secret(key_plain) if key_plain else "",
        capabilities=json.dumps(sorted(set(capabilities or []))),
    )
    db.add(ch)
    db.flush()
    from backend.app.models import ProviderModel

    # 创建时直配模型目录（spec §7；解决原「需 DB 直插 provider_models」痛点）
    # 计价校验前置（五遍复检 G4：入库前拒绝，共用 validate_model_pricing）
    for m in models or []:
        mid = str(m.get("model_id") or "").strip()
        if not mid:
            continue
        validate_model_pricing(m.get("pricing_unit"), bool(m.get("is_free")), m.get("price_input"))
        db.add(
            ProviderModel(
                channel_id=ch.id,
                model_id=mid,
                display_name=m.get("display_name") or mid,
                enabled=1 if m.get("enabled", True) else 0,
                params_json=json.dumps(m.get("params_json") or {}, ensure_ascii=False),
                meta_json=json.dumps(m.get("meta_json") or {}, ensure_ascii=False),
                is_free=1 if m.get("is_free") else 0,
                pricing_unit=m.get("pricing_unit"),
                price_input=m.get("price_input"),
                price_output=m.get("price_output"),
            )
        )
    db.commit()
    db.refresh(ch)
    return ch


def list_channels(db: Session, user_id: int) -> list[ProviderChannel]:
    return (
        db.query(ProviderChannel)
        .filter(ProviderChannel.user_id == user_id)
        .order_by(ProviderChannel.sort_order, ProviderChannel.id)
        .all()
    )


def get_channel_for_test(db: Session, user_id: int, channel_id: int) -> ProviderChannel | None:
    """测试连通：系统渠道（user_id IS NULL）与个人渠道都可测（只读操作）。"""
    return (
        db.query(ProviderChannel)
        .filter(
            ProviderChannel.id == channel_id,
            or_(ProviderChannel.user_id == user_id, ProviderChannel.user_id.is_(None)),
        )
        .first()
    )


def get_channel_for_user(db: Session, user_id: int, channel_id: int) -> ProviderChannel | None:
    return (
        db.query(ProviderChannel)
        .filter(ProviderChannel.id == channel_id, ProviderChannel.user_id == user_id)
        .first()
    )


def update_channel(
    db: Session,
    ch: ProviderChannel,
    *,
    name: str | None = None,
    base_url: str | None = None,
    key_plain: str | None = None,
    enabled: bool | None = None,
    capabilities: list[str] | None = None,
) -> ProviderChannel:
    if name is not None:
        ch.name = name.strip()
    if base_url is not None:
        validate_base_url(base_url)
        ch.base_url = base_url.strip()
    if key_plain:
        ch.key_encrypted = encrypt_secret(key_plain)
    if enabled is not None:
        ch.enabled = 1 if enabled else 0
    if capabilities is not None:
        ch.capabilities = json.dumps(sorted(set(capabilities)))
    db.commit()
    db.refresh(ch)
    return ch


def delete_channel(db: Session, ch: ProviderChannel) -> None:
    db.delete(ch)
    db.commit()


def test_connection(db: Session, ch: ProviderChannel) -> dict:
    """服务端代理连通性测试（key 不出服务端）：探测 {base_url}/models。

    OpenAI 兼容语义：HTTP 2xx/401/403 均视为"可达"（401 只说明 key 问题，网络是通的）。
    超时/连接失败 → PROVIDER_UNREACHABLE。
    """
    import httpx

    from backend.app.core.errors import ProviderUnreachableError

    started = time.monotonic()
    url = ch.base_url.rstrip("/") + "/models"
    # 带 key 请求：部分服务商（如 SiliconFlow）对无 token 请求仍返回 HTTP 200 + 错误体，
    # 不带 key 会误判"连通"且模型目录永远同步为 0。
    try:
        key_plain = decrypt_secret(ch.key_encrypted)
    except Exception:  # noqa: BLE001 — 解密失败仍可做无鉴权探测
        key_plain = ""
    if isinstance(key_plain, bytes):
        key_plain = key_plain.decode()
    headers = {"Authorization": f"Bearer {key_plain}"} if key_plain else {}
    try:
        resp = httpx.get(url, timeout=10, follow_redirects=True, headers=headers)
    except httpx.HTTPError as e:
        raise ProviderUnreachableError(message=f"无法连接 {ch.base_url}: {type(e).__name__}") from e
    latency_ms = int((time.monotonic() - started) * 1000)
    reachable = resp.status_code < 500
    models_found = _count_models(resp) if reachable else None
    if reachable:
        _sync_models(db, ch, resp)
    return {"ok": reachable, "latency_ms": latency_ms, "models_found": models_found}


def _sync_models(db: Session, ch: ProviderChannel, resp) -> None:  # noqa: ANN001
    """连通成功后拉取 /models 目录 upsert（幂等：保留既有 enabled 状态）。"""

    from backend.app.models import ProviderModel

    try:
        items = resp.json().get("data") or []
    except ValueError:
        return
    if not isinstance(items, list):
        return

    # LLM 域：排除图片/视频/嵌入/重排/ASR 模型（无正向关键词——LLM 命名无统一模式）
    LLM_EXCLUDE = [
        "image", "video", "embed", "rerank", "asr", "tts", "whisper",
        "kolors", "flux", "sd3", "sdxl", "seedream", "hailuo", "irag",
    ]

    def in_domain(mid: str) -> bool:
        lower = mid.lower()
        if ch.domain == "llm":
            return not any(k in lower for k in LLM_EXCLUDE)
        return kws is None or any(k in lower for k in kws)

    # vision 能力启发式（远端标记）：/models 无能力元数据，按主流视觉模型命名
    # 模式打标；人工标记（meta.vision）优先，启发式不覆盖既有 meta
    VISION_PATTERNS = (
        "vl", "qvq", "vision", "glm-4v", "glm-4.1v", "glm-4.5v", "internvl",
        "minicpm-v", "minicpm-o", "llava", "gpt-4o", "gpt-4.1", "o4-mini",
        "claude-", "gemini-", "multimodal", "moondream", "florence",
    )

    VISION_EXCLUDE = ("embed", "rerank", "guard", "audio")

    def _looks_vision(mid: str) -> bool:
        lower = mid.lower()
        if any(x in lower for x in VISION_EXCLUDE):
            return False  # VL-Embedding/VL-Reranker 等同名系列非视觉对话模型
        return any(p in lower for p in VISION_PATTERNS)

    existing = {str(m.model_id): m for m in (ch.models or [])}
    # /models 无类型信息，按关键词启发式过滤非本域模型（避免生图页混入 LLM 模型名导致必败）
    domain_kw: dict[str, list[str]] = {
        "image": [
            "kolors",
            "flux",
            "stable",
            "sd3",
            "sdxl",
            "qwen-image",
            "seedream",
            "irag",
            "image",
            "photo",
        ],
        "video": ["video", "t2v", "i2v", "hailuo", "海螺"],
    }
    kws = domain_kw.get(str(ch.domain))
    for item in items:
        if not isinstance(item, dict) or "id" not in item:
            continue
        mid = str(item["id"])
        row = existing.get(str(mid))
        if row is not None:
            # 白名单收紧：曾启用但现判定不在本域 → 降级（用户手动启用的 0→1 不受影响）
            if row.enabled == 1 and not in_domain(mid):
                row.enabled = 0
            # 存量回填（2026-10-09）：meta 未含 vision 键且命中视觉模式 → 补标；
            # 人工标记（含显式 false）永不覆盖
            if ch.domain == "llm" and _looks_vision(mid):
                import json as _j2

                meta = _j2.loads(row.meta_json or "{}")
                if isinstance(meta, dict) and "vision" not in meta:
                    meta["vision"] = True
                    row.meta_json = _j2.dumps(meta, ensure_ascii=False)
            continue
        import json as _j

        meta = {"vision": True} if (ch.domain == "llm" and _looks_vision(mid)) else {}
        db.add(
            ProviderModel(
                channel_id=ch.id,
                model_id=mid,
                display_name=mid,
                enabled=1 if in_domain(mid) else 0,
                meta_json=_j.dumps(meta, ensure_ascii=False) if meta else "{}",
            )
        )
    db.commit()


def _count_models(resp) -> int | None:  # noqa: ANN001
    """尽力解析 OpenAI 兼容 /models 响应的模型数；非 JSON 视为 0。"""
    try:
        data = resp.json()
        items = data.get("data") if isinstance(data, dict) else None
        return len(items) if isinstance(items, list) else 0
    except ValueError:
        return 0


def _build_provider(domain: str, model: str, ch: ProviderChannel, key_plain: str) -> ProviderBase:
    from backend.app.providers.h3_warehouse import H3WarehouseProvider
    from backend.app.providers.minimax_h3_local import H3LocalVideoProvider
    from backend.app.providers.minimax_video import MinimaxImageProvider, MinimaxVideoProvider
    from backend.app.providers.openai_compat import OpenAICompatProvider

    # 算力仓 H3 原生接入（独立）：capabilities 标记 native_video_api → /v1/videos 异步协议
    if domain == "video" and "native_video_api" in ch._capabilities_list():
        return H3WarehouseProvider(
            model=model, channel=ch, key_plain=key_plain, base_url=ch.base_url
        )

    # 本地 H3（asus 4090 · OpenAI 兼容 chat→video 适配器）
    if model.startswith("minimax-h3") and domain == "video":
        return H3LocalVideoProvider(
            model=model, channel=ch, key_plain=key_plain, base_url=ch.base_url
        )

    if "minimax" in (ch.name + ch.base_url).lower():
        if domain == "video":
            return MinimaxVideoProvider(
                model=model, channel=ch, key_plain=key_plain, base_url=ch.base_url
            )
        if domain == "image":
            return MinimaxImageProvider(model=model, channel=ch, key_plain=key_plain)
    return OpenAICompatProvider(
        model=model, channel=ch, key_plain=key_plain, base_url=ch.base_url, task_type=domain
    )


def resolve_provider_for_task(
    db: Session, user_id: int, domain: str, model: str, channel_id: int | None = None
) -> tuple[ProviderBase, ProviderChannel | None]:
    """按域解析可用渠道并构造带 key 的 provider 实例。

    优先级：enabled=1 的渠道按 sort_order；无 → PROVIDER_NOT_CONFIGURED（Review Focus #5）；
    ENABLE_FAKE_PROVIDER=true 且无渠道时回落 FakeProvider（由 worker 层判断，本函数保持纯粹）。
    """
    from backend.app.core.config import get_settings
    from backend.app.core.errors import ProviderNotConfiguredError
    from backend.app.models import ProviderModel
    from backend.app.providers.fake import FakeProvider

    # 最优先：用户在 UI 明确选择的渠道
    if channel_id is not None:
        exact = (
            db.query(ProviderChannel)
            .filter(
                ProviderChannel.id == channel_id,
                ProviderChannel.enabled == 1,
                or_(ProviderChannel.user_id == user_id, ProviderChannel.user_id.is_(None)),
            )
            .first()
        )
        if exact is not None:
            key_plain = _safe_decrypt(exact.key_encrypted)
            provider = _build_provider(domain, model, exact, key_plain)
            return provider, exact

    # 次优先：模型目录中含该 model 的渠道（用户在 UI 选了具体模型即按此路由）
    matched = (
        db.query(ProviderChannel)
        .join(ProviderModel, ProviderModel.channel_id == ProviderChannel.id)
        .filter(
            ProviderChannel.domain == domain,
            ProviderChannel.enabled == 1,
            ProviderModel.model_id == model,
            or_(ProviderChannel.user_id == user_id, ProviderChannel.user_id.is_(None)),
        )
        .order_by(ProviderChannel.user_id.is_(None))
        .first()
    )
    if matched is not None:
        key_plain = _safe_decrypt(matched.key_encrypted)
        provider = _build_provider(domain, model, matched, key_plain)
        return provider, matched

    ch = (
        db.query(ProviderChannel)
        .filter(
            ProviderChannel.domain == domain,
            ProviderChannel.enabled == 1,
            or_(ProviderChannel.user_id == user_id, ProviderChannel.user_id.is_(None)),
        )
        .order_by(ProviderChannel.user_id.is_(None), ProviderChannel.sort_order, ProviderChannel.id)
        .first()
    )
    if ch is None:
        if get_settings().enable_fake_provider:
            return FakeProvider(model=model, channel=None), None
        raise ProviderNotConfiguredError(
            message=f"尚未配置可用的 {domain} 渠道",
            hint="请在「设置 → 模型渠道」添加并启用渠道后重试",
        )
    if not model:
        # 模型名为空：渠道模型目录的第一个启用模型 → 域默认模型
        first_model = (
            db.query(ProviderModel)
            .filter(ProviderModel.channel_id == ch.id, ProviderModel.enabled == 1)
            .first()
        )
        model = (
            first_model.model_id
            if first_model
            else {"image": "Kwai-Kolors/Kolors", "video": "video-01", "llm": "MiniMax-M2.7"}.get(
                domain, ""
            )
        )
    key_plain = _safe_decrypt(ch.key_encrypted)
    provider = _build_provider(domain, model, ch, key_plain)
    return provider, ch


def resolve_channel_for_user(db: Session, user_id: int, domain: str) -> ProviderChannel | None:
    """按用户+域取首个启用的渠道（对外 API 使用；个人优先，回退系统渠道）。"""
    return (
        db.query(ProviderChannel)
        .filter(
            or_(ProviderChannel.user_id == user_id, ProviderChannel.user_id.is_(None)),
            ProviderChannel.domain == domain,
            ProviderChannel.enabled == 1,
        )
        .order_by(ProviderChannel.sort_order, ProviderChannel.id)
        .first()
    )
