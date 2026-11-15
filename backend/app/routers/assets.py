"""资产库：当前用户已完成任务的产物聚合（去重、按时间倒序、搜索、稳定分页）。"""

import json

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.app.core.db import get_db
from backend.app.core.times import iso_utc
from backend.app.models import Task, User
from backend.app.routers.auth import get_current_user

router = APIRouter(prefix="/api/assets", tags=["assets"])

# 单请求扫描原始行上限（去重前）。用户任务量级为百级，5000 足够余量。
_SCAN_CAP = 5000


def _ref_url(params_json: str | None) -> str | None:
    """P2-10: 从 params 提取参考图（编辑/图生视频的输入，对比视图数据源）。"""
    try:
        p = json.loads(params_json or "{}")
    except (ValueError, TypeError):
        return None
    if not isinstance(p, dict):
        return None
    for key in ("image", "first_frame_image", "edit_ref"):
        v = p.get(key)
        if isinstance(v, str) and v.startswith("/outputs/"):
            return v
        if isinstance(v, list):
            for item in v:
                if isinstance(item, str) and item.startswith("/outputs/"):
                    return item
    return None


@router.get("")
def list_assets(
    type: str | None = Query(None, alias="type"),
    search: str = Query("", max_length=200),
    limit: int = Query(60, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """已完成且有产物链接的任务，按 (output_url) 去重聚合。

    - type 过滤（image/video/audio）；不传 = 全部媒体（自动排除 llm 文本）
    - search 按提示词模糊匹配（P1-6）
    - ref_url 参考图（P2-10 对比视图：编辑前后 / 首帧）
    - 分页修复：SQL 只扫不跳，去重后在 Python 侧切片（原实现双重 offset 第 2 页恒空）
    """
    filters = [
        Task.user_id == user.id,
        Task.status == "completed",
        Task.output_url.isnot(None),
        Task.output_url != "",
    ]
    if type:
        filters.append(Task.type == type)
    else:
        filters.append(Task.type != "llm")
    if search:
        filters.append(Task.prompt.ilike(f"%{search}%"))

    rows = (
        db.query(
            Task.id, Task.type, Task.output_url, Task.prompt, Task.completed_at,
            Task.model, Task.cost_amount, Task.params,
        )
        .filter(*filters)
        .order_by(Task.completed_at.desc())
        .limit(_SCAN_CAP)
        .all()
    )

    seen: set[str] = set()
    assets: list[dict] = []
    for tid, ttype, url, prompt, completed_at, model, cost_amount, params_json in rows:
        if url in seen:
            continue
        seen.add(url)
        assets.append(
            {
                "task_id": tid,
                "type": ttype,
                "url": url,
                "prompt": (prompt or "")[:120],
                "completed_at": iso_utc(completed_at),
                "model": model or "",
                "cost_amount": round(cost_amount, 4) if cost_amount is not None else None,
                "ref_url": _ref_url(params_json),
            }
        )
    return {"assets": assets[offset : offset + limit], "total": len(assets)}
