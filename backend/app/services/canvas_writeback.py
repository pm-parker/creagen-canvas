"""画布节点回写助手：按 client_id 精确定位节点。

derived from creagen prod app/services/canvas_writeback.py @ 2026-09-27。

定位规则：解析 metadata JSON 后比对 client_id 字段值——不得对原始 JSON 字符串
做子串匹配（客户端重存会重排键/重转义，空格差异就会让回写找不到目标节点）。
"""

from __future__ import annotations

import json
from typing import Any

# 节点创建时的画布 revision（服务端托管）：用于判定"客户端加载之后新建"的节点，
# 这类节点不得被陈旧的客户端保存删除（画布保存数据丢失 bug 的根因之一）。
CREATED_REV_KEY = "_created_rev"


def parse_canvas_meta(raw: str | None) -> dict[str, Any]:
    """metadata_json → dict（脏数据/非对象容错为空 dict）。"""
    try:
        parsed = json.loads(raw or "{}")
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def node_client_id(node: Any) -> str | None:
    """节点 metadata 里的 client_id（字符串化；缺失返回 None）。"""
    cid = parse_canvas_meta(getattr(node, "metadata_json", None)).get("client_id")
    return str(cid) if cid else None


def find_node_by_client_id(db, project_id: int, client_id: str | None):
    """在本画布内按 metadata.client_id 精确定位节点；找不到返回 None。"""
    if not client_id:
        return None
    from backend.app.models import CanvasNode

    wanted = str(client_id)
    # SQL 预筛只是性能优化，正确性由下面的解析比对保证
    nodes = (
        db.query(CanvasNode)
        .filter(
            CanvasNode.project_id == project_id,
            CanvasNode.metadata_json.contains(wanted, autoescape=True),
        )
        .all()
    )
    for n in nodes:
        if node_client_id(n) == wanted:
            return n
    return None
