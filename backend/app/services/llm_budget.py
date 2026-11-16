"""LLM 日 token 预算：按 (scope, day) 熔断。

derived from creagen prod app/services/llm_budget.py @ 2026-09-27，
由原生 SQL 改为 ORM（llm_budget 表），会话由调用方传入。
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from backend.app.models import LLMBudget

DEFAULT_DAILY_TOKEN_LIMIT = 1_000_000


class BudgetExceeded(Exception):
    """当日预算已耗尽。"""

    def __init__(self, scope: str, used: int, limit: int):
        self.scope = scope
        self.used = used
        self.limit = limit
        super().__init__(f"scope={scope} 日 token 预算已耗尽: {used}/{limit}")


def _today_row(db: Session, scope: str) -> LLMBudget:
    row = db.get(LLMBudget, {"scope": scope, "day": date.today()})
    if row is None:
        row = LLMBudget(scope=scope, day=date.today(), tokens_limit=DEFAULT_DAILY_TOKEN_LIMIT)
        db.add(row)
        db.flush()
    return row


def budget_check(db: Session, scope: str) -> int:
    """校验当日剩余额度，返回剩余 tokens；耗尽 raise BudgetExceeded。"""
    row = _today_row(db, scope)
    remaining = row.tokens_limit - row.tokens_used
    if remaining <= 0:
        raise BudgetExceeded(scope, row.tokens_used, row.tokens_limit)
    return remaining


def budget_consume(
    db: Session, scope: str, prompt_tokens: int, completion_tokens: int, calls: int = 1
) -> None:
    """累加当日 token 消耗（行不存在则按默认限额创建）。"""
    row = _today_row(db, scope)
    row.tokens_used += int(prompt_tokens) + int(completion_tokens)
    row.calls = (row.calls or 0) + int(calls)
    db.commit()


def budget_release(
    db: Session, scope: str, tokens: int, calls: int = 1
) -> None:
    """回退（释放）已计入的 token 消耗。

    任务在生命周期启动前就失败（预算熔断于生成前、渠道解析失败等）时，
    已预扣的额度必须退回——否则用户为一次失败的提交白白损失额度。
    """
    row = _today_row(db, scope)
    row.tokens_used = max(0, (row.tokens_used or 0) - int(tokens))
    row.calls = max(0, (row.calls or 0) - int(calls))
    db.commit()


def budget_status(db: Session, scope: str) -> dict[str, int]:
    row = _today_row(db, scope)
    return {
        "used": row.tokens_used,
        "limit": row.tokens_limit,
        "remaining": max(0, row.tokens_limit - row.tokens_used),
    }
