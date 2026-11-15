"""LLM 日预算表（ORM 化生产版原生 SQL 表：scope + day 唯一）。"""

from sqlalchemy import Column, Date, Integer, String, func

from backend.app.models.base import Base


class LLMBudget(Base):
    __tablename__ = "llm_budget"

    scope = Column(String(100), primary_key=True)
    day = Column(Date, primary_key=True, default=func.current_date())
    tokens_limit = Column(Integer, nullable=False, default=1_000_000)
    tokens_used = Column(Integer, nullable=False, default=0)
    calls = Column(Integer, nullable=False, default=0)
