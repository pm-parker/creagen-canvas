"""成本看板：任务维度成本聚合（源自生产版 status.py costs/stats，重写为独立 router）。

D3 二批：?days= 趋势窗口 + 上月同期环比基线 + 模型分布含 token 计量
+ /export CSV 明细导出 + /tasks 模型下钻。
"""

import csv
import io
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from backend.app.core.db import get_db
from backend.app.models import Task, User
from backend.app.routers.auth import get_current_user
from backend.app.services.cost_calculator import (
    IMAGE_FALLBACK_PRICE,
    IMAGE_PRICE,
    VIDEO_FALLBACK_PRICE,
    VIDEO_PRICE,
)

router = APIRouter(prefix="/api/costs", tags=["costs"])


@router.get("/prices")
def list_prices(user: User = Depends(get_current_user)):
    """生成单价表（CNY）：前端提交前成本预估用。"""
    return {
        "image": {k: v for k, v in sorted(IMAGE_PRICE.items())},
        "video": {k: v for k, v in sorted(VIDEO_PRICE.items())},
        "fallback": {"image": IMAGE_FALLBACK_PRICE, "video": VIDEO_FALLBACK_PRICE},
    }


@router.get("/stats")
def cost_stats(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    days: int = 7,
):
    """当前用户的成本聚合：总量/当月/今日 + 近 N 日分布 + 模型分布 + 上月同期基线。"""
    today = date.today()
    month_start = today.replace(day=1)
    window_days = max(1, min(int(days), 90))

    def _sum(q_filter) -> float:
        row = (
            db.query(func.coalesce(func.sum(Task.cost_amount), 0))
            .filter(and_(Task.user_id == user.id, q_filter))
            .first()
        )
        return round(float((row[0] if row else 0) or 0), 4)

    total_cost = _sum(Task.user_id == user.id)
    monthly_cost = _sum(Task.completed_at >= month_start)
    daily_cost = _sum(Task.completed_at >= today)

    # D3.1 上月同期：上月 1 日 → 上月同位日（与本月已过天数对齐，环比才可比）
    prev_month_start = (month_start - timedelta(days=1)).replace(day=1)
    prev_same_day = prev_month_start + (today - month_start)
    prev_month_cost = _sum(
        and_(Task.completed_at >= prev_month_start, Task.completed_at < prev_same_day)
    )

    daily_breakdown = []
    for i in range(window_days - 1, -1, -1):
        d = today - timedelta(days=i)
        day_start = datetime(d.year, d.month, d.day)
        day_end = day_start + timedelta(days=1)
        row = (
            db.query(
                func.coalesce(func.sum(Task.cost_amount), 0),
                func.count(Task.id),
                func.coalesce(func.sum(Task.tokens_used), 0),
            )
            .filter(
                and_(
                    Task.user_id == user.id,
                    Task.completed_at >= day_start,
                    Task.completed_at < day_end,
                    Task.status == "completed",
                )
            )
            .first()
        )
        if row is None:
            continue
        daily_breakdown.append(
            {
                "date": d.strftime("%m-%d"),
                "cost": round(float(row[0]), 4),
                "count": row[1],
                "tokens": int(row[2]),
            }
        )

    model_rows = (
        db.query(
            Task.model,
            func.coalesce(func.sum(Task.cost_amount), 0),
            func.count(Task.id),
            func.coalesce(func.sum(Task.tokens_used), 0),
        )
        .filter(
            Task.user_id == user.id,
            Task.completed_at >= month_start,
            Task.status == "completed",
        )
        .group_by(Task.model)
        .all()
    )
    # Task 9：模型级免费标记（同名任一渠道标记即免费）
    from backend.app.models import ProviderModel

    free_models = {
        r[0] for r in db.query(ProviderModel.model_id).filter(ProviderModel.is_free == 1).all()
    }

    month_completed = (
        db.query(Task)
        .filter(
            Task.user_id == user.id,
            Task.completed_at >= month_start,
            Task.status == "completed",
        )
        .count()
    )

    return {
        "daily_cost": daily_cost,
        "monthly_cost": monthly_cost,
        "total_cost": total_cost,
        "prev_month_cost": prev_month_cost,
        "daily_breakdown": daily_breakdown,
        "model_breakdown": [
            {
                "model": r[0],
                "cost": round(float(r[1]), 4),
                "count": r[2],
                "tokens": int(r[3]),
                "is_free": r[0] in free_models,
            }
            for r in model_rows
        ],
        "month_completed_count": month_completed,
    }


def _completed_query(db: Session, user: User, start: date | None, end: date | None):
    q = db.query(Task).filter(Task.user_id == user.id, Task.status == "completed")
    if start:
        q = q.filter(Task.completed_at >= datetime(start.year, start.month, start.day))
    if end:
        q = q.filter(Task.completed_at < datetime(end.year, end.month, end.day) + timedelta(days=1))
    return q


@router.get("/export")
def export_costs(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    start: date | None = None,
    end: date | None = None,
):
    """任务级成本明细 CSV（默认当月）：对账/归档用。"""
    month_start = date.today().replace(day=1)
    rows = _completed_query(db, user, start or month_start, end).order_by(Task.completed_at).all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "id", "created_at", "completed_at", "type", "model", "status",
        "cost_amount", "tokens_used", "duration_ms",
    ])
    for t in rows:
        writer.writerow([
            t.id,
            t.created_at.isoformat() if t.created_at else "",
            t.completed_at.isoformat() if t.completed_at else "",
            t.type,
            t.model,
            t.status,
            t.cost_amount if t.cost_amount is not None else "",
            t.tokens_used if t.tokens_used is not None else "",
            t.duration_ms if t.duration_ms is not None else "",
        ])

    effective_start = start or month_start
    end_label = (end or date.today()).strftime("%Y%m%d")
    filename = f"costs_{effective_start.strftime('%Y%m')}_{end_label}.csv"
    return _csv_response(buf.getvalue(), filename)


def _csv_response(content: str, filename: str):
    from fastapi.responses import Response

    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/tasks")
def cost_tasks(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    model: str | None = None,
    type: str | None = None,
    start: date | None = None,
    end: date | None = None,
    limit: int = 20,
    offset: int = 0,
):
    """模型/类型下钻：分页任务明细（成本页模型条目点击展开）。"""
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    q = _completed_query(db, user, start, end)
    if model:
        q = q.filter(Task.model == model)
    if type:
        q = q.filter(Task.type == type)
    total = q.count()
    rows = q.order_by(Task.id.desc()).offset(offset).limit(limit).all()
    return {
        "total": total,
        "items": [
            {
                "id": t.id,
                "created_at": t.created_at.isoformat() if t.created_at else "",
                "completed_at": t.completed_at.isoformat() if t.completed_at else "",
                "type": t.type,
                "model": t.model,
                "status": t.status,
                "cost_amount": t.cost_amount,
                "tokens_used": t.tokens_used,
                "duration_ms": t.duration_ms,
                "prompt": (t.prompt or "")[:80],
            }
            for t in rows
        ],
    }
