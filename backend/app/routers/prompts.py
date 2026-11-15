"""提示词库路由：列表/搜索/创建/收藏（W1 迁移的 prompts 模型首度暴露）。"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from backend.app.core.db import get_db
from backend.app.models import Prompt, PromptFavorite, User
from backend.app.routers.auth import get_current_user

router = APIRouter(prefix="/api/prompts", tags=["prompts"])


@router.post("/reverse")
async def reverse_prompt_endpoint(
    body: dict,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """真·看图反推（v2）：默认视觉模型多模态调用——六要素中文提示词。

    v1（optimize 扩写旧词）由前端在 VISION_NOT_CONFIGURED 时回落使用。
    """
    from backend.app.services.rate_limiter import RateLimitExceeded, enforce
    from backend.app.services.vision_reverse import VisionReverseError, reverse_prompt

    url = str(body.get("url") or "")
    try:
        enforce(f"reverse:{user.id}", "llm", limit=10, window_seconds=60, message="反推过于频繁")
    except RateLimitExceeded as e:
        raise HTTPException(429, detail={"code": "RATE_LIMITED", "message": str(e)}) from e
    try:
        r = await reverse_prompt(db, user=user, image_url=url)
    except VisionReverseError as e:
        status = 400 if e.code in ("VISION_NOT_CONFIGURED", "IMAGE_INVALID") else 502
        if e.code == "BUDGET_EXCEEDED":
            status = 429
        raise HTTPException(
            status, detail={"code": e.code, "message": e.message, "hint": e.hint}
        ) from e
    return r


@router.get("/categories")
def list_categories(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """全库分类统计（种子+个人，含计数）——供筛选栏展示，不依赖当前页。"""
    from sqlalchemy import func as sa_func

    rows = (
        db.query(Prompt.category, sa_func.count(Prompt.id))
        .filter(
            or_(Prompt.user_id.is_(None), Prompt.user_id == user.id),
            Prompt.category.isnot(None),
            Prompt.category != "",
        )
        .group_by(Prompt.category)
        .order_by(sa_func.count(Prompt.id).desc())
        .all()
    )
    return {"categories": [{"category": cat, "count": cnt} for cat, cnt in rows]}


@router.get("")
def list_prompts(
    search: str = "",
    category: str | None = Query(None),
    tag: str | None = Query(None),
    model: str | None = Query(None),
    favorites_only: bool = Query(False),
    has_thumb: bool = Query(False),
    show_mature: bool = Query(False),
    sort: str | None = Query(None),
    limit: int = Query(24, le=200),
    offset: int = 0,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """提示词列表（种子共享 + 个人私有），带收藏状态、多维度筛选与排序。"""
    query = db.query(Prompt).filter(or_(Prompt.user_id.is_(None), Prompt.user_id == user.id))
    # 内容分级：共享 mature 默认隐藏（个人条目不受限——自托管中立立场）
    if not show_mature:
        query = query.filter(
            or_(Prompt.user_id == user.id, Prompt.rating.is_(None), Prompt.rating != "mature")
        )
    if search:
        like = f"%{search}%"
        query = query.filter(or_(Prompt.title.contains(like), Prompt.content.contains(like)))
    if category:
        query = query.filter(Prompt.category == category)
    if tag:
        query = query.filter(Prompt.tags.contains(f'"{tag}"'))
    if model:
        query = query.filter(Prompt.model.ilike(f"%{model}%"))
    if has_thumb:
        # 首页灵感墙：只取带效果图的条目（thumb_url 非空且非空白）
        query = query.filter(
            Prompt.thumb_url.isnot(None), Prompt.thumb_url != ""
        )
    if favorites_only:
        query = query.join(
            PromptFavorite,
            (PromptFavorite.prompt_id == Prompt.id) & (PromptFavorite.user_id == user.id),
        )
    # 排序：默认有图优先+最新；支持 random（发现模式）
    if sort == "random":
        from sqlalchemy import func as sa_func

        query = query.order_by(sa_func.random())
    else:
        query = query.order_by(
            Prompt.thumb_url.isnot(None).desc(),
            Prompt.updated_at.desc().nullslast(),
            Prompt.id.desc(),
        )
    items = (
        query.offset(offset)
        .limit(limit)
        .all()
    )
    # 当前用户收藏集合（一次查询避免 N+1）
    fav_ids: set[int] = set()
    if items:
        fav_ids = {
            row[0]
            for row in db.query(PromptFavorite.prompt_id)
            .filter(
                PromptFavorite.user_id == user.id,
                PromptFavorite.prompt_id.in_([p.id for p in items]),
            )
            .all()
        }
    out = []
    for p in items:
        d = p.to_dict()
        d["is_favorited"] = p.id in fav_ids
        d["user_id"] = p.user_id  # 前端据此判断可编辑（None=种子共享）
        out.append(d)
    total = query.count()
    return {"prompts": out, "total": total}


@router.post("", status_code=201)
def create_prompt(
    body: dict,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    content = (body.get("content") or "").strip()
    if not content:
        raise _bad("CONTENT_REQUIRED", "提示词内容不能为空")
    thumb = body.get("thumb_url")
    if thumb and not _valid_thumb(thumb):
        raise _bad("THUMB_URL_INVALID", "效果图需为本地 /outputs/ 路径（先上传再引用）")
    import json as _json

    p = Prompt(
        user_id=user.id,
        title=(body.get("title") or content[:30]).strip(),
        content=content,
        category=body.get("category"),
        tags=_json.dumps(_normalize_tags(body.get("tags")), ensure_ascii=False),
        model=(str(body["model"])[:50] if body.get("model") else None),
        thumb_url=thumb,
        source="manual",
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return {"prompt": p.to_dict()}


def _normalize_tags(raw) -> list[str]:
    """标签去空/去重/截断（≤8 个，每个 ≤20 字）。"""
    if not isinstance(raw, list):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for t in raw:
        if not isinstance(t, str):
            continue
        t = t.strip()[:20]
        if t and t not in seen:
            seen.add(t)
            out.append(t)
        if len(out) >= 8:
            break
    return out


def _valid_thumb(url) -> bool:
    """效果图只收本地 /outputs/ 路径（上传接口产物；防外链挂图）。"""
    return isinstance(url, str) and url.startswith("/outputs/") and ".." not in url


def _bad(code: str, message: str, status: int = 422):
    from fastapi import HTTPException

    return HTTPException(status_code=status, detail={"code": code, "message": message})


@router.patch("/{prompt_id}/rating")
def set_prompt_rating(
    prompt_id: int,
    body: dict,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """内容分级标记：种子共享条目（user_id NULL）仅管理员；个人条目 owner。

    rating 取值：null（清除/普通）| "mature"。枚举外 422。
    """
    from fastapi import HTTPException

    rating = body.get("rating")
    if rating not in (None, "mature"):
        raise HTTPException(
            status_code=422,
            detail={"code": "RATING_INVALID", "message": "rating 仅支持 null 或 \'mature\'"},
        )
    p = db.get(Prompt, prompt_id)
    if p is None or (p.user_id is not None and p.user_id != user.id):
        raise HTTPException(
            status_code=404, detail={"code": "PROMPT_NOT_FOUND", "message": "提示词不存在"}
        )
    if p.user_id is None and user.role != "admin":
        raise HTTPException(
            status_code=403,
            detail={
                "code": "ADMIN_ONLY",
                "message": "共享条目的分级标记仅管理员可操作",
            },
        )
    p.rating = rating
    db.commit()
    return {"id": p.id, "rating": p.rating}


@router.delete("/{prompt_id}", status_code=204)
def delete_prompt(
    prompt_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    """删除个人提示词（种子共享条目不可删）。"""
    p = db.get(Prompt, prompt_id)
    if p is None:
        raise _bad("PROMPT_NOT_FOUND", "提示词不存在", status=404)
    if p.user_id is None:
        raise _bad("PROMPT_FORBIDDEN", "内置种子提示词不可删除", status=403)
    if p.user_id != user.id:
        raise _bad("PROMPT_FORBIDDEN", "只能删除自己的提示词", status=403)
    db.delete(p)
    db.commit()


@router.patch("/{prompt_id}")
def update_prompt(
    prompt_id: int,
    body: dict,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """编辑个人提示词（内容/标题/分类）。"""
    p = db.get(Prompt, prompt_id)
    if p is None:
        raise _bad("PROMPT_NOT_FOUND", "提示词不存在", status=404)
    if p.user_id is None or p.user_id != user.id:
        raise _bad("PROMPT_FORBIDDEN", "只能编辑自己的提示词", status=403)
    content = (body.get("content") or "").strip()
    if not content:
        raise _bad("CONTENT_REQUIRED", "提示词内容不能为空")
    if body.get("thumb_url") and not _valid_thumb(body["thumb_url"]):
        raise _bad("THUMB_URL_INVALID", "效果图需为本地 /outputs/ 路径（先上传再引用）")
    import json as _json

    p.content = content
    if body.get("title"):
        p.title = str(body["title"])[:500]
    if body.get("category") is not None:
        p.category = str(body["category"])
    if body.get("tags") is not None:
        p.tags = _json.dumps(_normalize_tags(body["tags"]), ensure_ascii=False)
    if body.get("model") is not None:
        p.model = str(body["model"])[:50]
    if body.get("thumb_url") is not None:
        p.thumb_url = str(body["thumb_url"])
    db.commit()
    db.refresh(p)
    return {"prompt": p.to_dict()}


@router.post("/{prompt_id}/favorite", status_code=201)
def favorite(prompt_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if not db.get(Prompt, prompt_id):
        raise _bad("PROMPT_NOT_FOUND", "提示词不存在", status=404)
    fav = (
        db.query(PromptFavorite)
        .filter(PromptFavorite.user_id == user.id, PromptFavorite.prompt_id == prompt_id)
        .first()
    )
    if not fav:
        db.add(PromptFavorite(user_id=user.id, prompt_id=prompt_id))
        db.commit()
    return {"favorited": True}  # noqa: E501


@router.delete("/{prompt_id}/favorite", status_code=200)
def unfavorite(  # noqa: ANN201
    prompt_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    db.query(PromptFavorite).filter(
        PromptFavorite.user_id == user.id, PromptFavorite.prompt_id == prompt_id
    ).delete()
    db.commit()
    return {"favorited": False}


def bulk_create_prompts(db: Session, items: list, user_id: int | None = None) -> dict:
    """批量创建核心逻辑：content 去重（个人范围内），兼容 prompt 键，单次上限 500。"""
    existing = {
        row[0]
        for row in db.query(Prompt.content).filter(
            or_(Prompt.user_id.is_(None), Prompt.user_id == user_id)
        ).all()
    }
    created, skipped = 0, 0
    for item in items[:500]:
        if not isinstance(item, dict):
            skipped += 1
            continue
        content = str(item.get("content") or item.get("prompt") or "").strip()
        if not content or content in existing:
            skipped += 1
            continue
        title = str(item.get("title") or content[:30]).strip()
        db.add(
            Prompt(
                user_id=user_id,
                title=title[:500],
                content=content,
                category=item.get("category") or "导入",
                source="import",
            )
        )
        existing.add(content)
        created += 1
    db.commit()
    return {"created": created, "skipped": skipped}


@router.post("/import", status_code=201)
def import_prompts(
    body: dict,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """批量导入提示词。items: [{title?, content, category?}]，按 content 去重（个人范围）。"""
    items = body.get("items")
    if not isinstance(items, list) or not items:
        raise _bad("ITEMS_REQUIRED", "items 需为非空数组")
    return bulk_create_prompts(db, items, user_id=user.id)


@router.post("/import-url", status_code=201)
def import_prompts_from_url(
    body: dict,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """从远程 JSON URL 导入提示词（服务端拉取，避开浏览器 CORS）。

    SSRF 防护：目标经 url_guard 校验（拒私网/环回/保留段），重定向逐跳复检。
    """
    import httpx

    from backend.app.services.url_guard import UrlBlocked, assert_public_http_url

    url = (body.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        raise _bad("URL_INVALID", "url 需为 http(s) 地址")
    try:
        assert_public_http_url(url)
    except UrlBlocked as e:
        raise _bad(e.code, e.message) from e
    try:
        # 手动跟随重定向（≤3 跳），每一跳都过 SSRF 校验
        current = url
        resp = None
        for _ in range(4):
            resp = httpx.get(current, timeout=20, follow_redirects=False)
            if resp.is_redirect and resp.headers.get("location"):
                from urllib.parse import urljoin

                current = urljoin(current, resp.headers["location"])
                try:
                    assert_public_http_url(current)
                except UrlBlocked as e:
                    raise _bad(e.code, e.message) from e
                continue
            break
        resp.raise_for_status()
        payload = resp.json()
    except httpx.HTTPError as e:
        raise _bad("FETCH_FAILED", f"拉取失败: {type(e).__name__}") from e
    except ValueError as e:
        raise _bad("JSON_INVALID", "远程内容不是合法 JSON") from e

    items = payload if isinstance(payload, list) else None
    if items is None and isinstance(payload, dict):
        items = payload.get("prompts") or payload.get("data")
    if not isinstance(items, list):
        raise _bad("JSON_SHAPE_INVALID", "JSON 顶层需为数组或含 prompts/data 数组")
    return bulk_create_prompts(db, items, user_id=user.id)


@router.post("/optimize")
def optimize_prompt(
    body: dict,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """AI 提示词优化器：走用户 llm 渠道重写提示词（对标 BeefTV 内置优化器插件）。"""
    prompt = (body.get("prompt") or "").strip()
    if len(prompt) < 4:
        raise _bad("PROMPT_TOO_SHORT", "提示词太短，至少 4 个字符")
    style = body.get("style") or "通用增强"

    # 审计批次三（P2）：optimize 直连用户 LLM 渠道——限流 + 日预算与任务路径同治
    from backend.app.services.rate_limiter import RateLimitExceeded, enforce

    try:
        enforce(
            f"optimize:{user.id}",
            "process",
            limit=10,
            window_seconds=60,
            message="优化过于频繁，每分钟最多 10 次",
        )
    except RateLimitExceeded as e:
        from fastapi import HTTPException as _HE

        raise _HE(status_code=429, detail={"code": "RATE_LIMITED", "message": str(e)}) from e
    from backend.app.services.llm_budget import BudgetExceeded, budget_check, budget_consume

    try:
        budget_check(db, f"llm:{user.id}")
    except BudgetExceeded as e:
        from fastapi import HTTPException as _HE

        raise _HE(
            status_code=422,
            detail={"code": "BUDGET_EXCEEDED", "message": str(e), "hint": "当日 LLM 预算已耗尽"},
        ) from e

    from backend.app.core.errors import ProviderNotConfiguredError
    from backend.app.providers.openai_compat import OpenAICompatProvider
    from backend.app.services.provider_service import resolve_provider_for_task

    # 模型解析链（2026-10-07 修复）：前端指定 > 系统默认 LLM > 空（resolver
    # 自动取渠道目录第一个启用模型）。此前硬编码 gpt-4o-mini——用户渠道无此
    # 模型时兜底渠道+坏模型名必 400，且用户无从知道在哪配置。
    model = (body.get("model") or "").strip()
    if not model:
        import json as _dj

        from backend.app.models import CapabilityConfig

        row = db.get(CapabilityConfig, "default_models")
        if row:
            try:
                defaults = _dj.loads(str(row.value_json or "{}"))
                m = (defaults or {}).get("llm")
                if isinstance(m, dict) and isinstance(m.get("model"), str) and m["model"].strip():
                    model = m["model"].strip()
            except (ValueError, AttributeError):
                pass

    try:
        provider, _ch = resolve_provider_for_task(db, user.id, "llm", model)
    except ProviderNotConfiguredError as e:
        raise _bad(e.code, e.message) from e
    used_model = getattr(provider, "model", "") or model
    ch_label = str(getattr(_ch, "name", "") or "内置")
    if not isinstance(provider, OpenAICompatProvider):
        # FakeProvider（开发/演示）：返回伪优化保持流程可走
        return {"optimized": f"{prompt}，高清细节，电影感光效，构图完整", "original": prompt}

    system = (
        "你是顶级 AI 绘画/视频提示词工程师。把用户提示词改写为更具体、更有画面感、"
        f"更适合文生图模型的版本。风格方向：{style}。"
        "规则：保留原意与主体；补充画面细节（主体/环境/光线/构图/质感/色调）；"
        "不要解释，不要加引号，直接输出优化后的中文提示词；长度 80-200 字。"
    )
    import asyncio

    result = asyncio.run(
        provider.run(
            _OptimizeTask(system=system, user=prompt),
            on_event=None,
        )
    )
    # 与 worker 同语义：真实调用后记账（伪优化路径不记）；D3.4 起优先用真实 usage，
    # 无 usage 时回落 len*2 粗估（保持熔断语义不变）
    _usage = result.get("usage") or {}
    budget_consume(
        db,
        f"llm:{user.id}",
        int(_usage.get("prompt_tokens") or len(prompt) * 2),
        int(_usage.get("completion_tokens") or 0),
        calls=1,
    )
    if result.get("status") != "completed":
        detail = result.get("error", "优化失败")
        raise _bad(
            result.get("error_code", "OPTIMIZE_FAILED"),
            f"{detail}（渠道「{ch_label}」· 模型 {used_model or '自动'}；"
            "可在 设置→模型渠道 调整）",
        )
    return {"optimized": result["output_url"], "original": prompt}


class _OptimizeTask:
    """适配 OpenAICompatProvider.run(task) 的 llm 任务形状。"""

    id = 0
    type = "llm"

    def __init__(self, system: str, user: str):
        self.prompt = f"{system}\n\n用户提示词：{user}"

    def params_dict(self):
        return {}
