"""时间序列化约定：库内 naive DateTime 一律视为 UTC。

PG 连接会话时区为 UTC，func.now() 落库即 naive UTC；序列化若不补时区，
前端 new Date() 按本地解析会差 8 小时。所有 API 时间出口必须经 iso_utc()。
"""

from datetime import UTC, datetime


def iso_utc(dt: datetime | None) -> str | None:
    """naive 视为 UTC，输出带 +00:00 的 ISO8601；aware 原样保留；None 透传。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.isoformat()
