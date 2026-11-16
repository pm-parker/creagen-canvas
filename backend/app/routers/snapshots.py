"""画布版本快照：创建 / 列表 / 恢复（滚动保留最近 20 份）。"""

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.app.core.db import get_db
from backend.app.core.times import iso_utc
from backend.app.models import CanvasConnection, CanvasNode, CanvasProject, CanvasSnapshot, User
from backend.app.routers.auth import get_current_user

router = APIRouter(prefix="/api/canvas", tags=["snapshots"])

MAX_SNAPSHOTS = 20


def _own(db: Session, project_id: int, user: "User") -> CanvasProject:
    project = db.get(CanvasProject, project_id)
    if project is None or project.user_id != user.id:
        raise HTTPException(
            status_code=404,
            detail={"code": "PROJECT_NOT_FOUND", "message": "画布不存在"},
        )
    return project


def _dump_nodes(db: Session, project_id: int) -> str:
    nodes = db.query(CanvasNode).filter(CanvasNode.project_id == project_id).all()
    return json.dumps([n.to_dict() for n in nodes], ensure_ascii=False)


def _dump_connections(db: Session, project_id: int) -> str:
    conns = db.query(CanvasConnection).filter(CanvasConnection.project_id == project_id).all()
    return json.dumps([c.to_dict() for c in conns], ensure_ascii=False)


@router.get("/projects/{project_id}/snapshots")
def list_snapshots(
    project_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    _own(db, project_id, user)
    items = (
        db.query(CanvasSnapshot)
        .filter(CanvasSnapshot.project_id == project_id)
        .order_by(CanvasSnapshot.version.desc())
        .all()
    )
    return {
        "snapshots": [
            {
                "id": s.id,
                "version": s.version,
                "label": s.label,
                "created_at": iso_utc(s.created_at),
            }
            for s in items
        ]
    }


@router.post("/projects/{project_id}/snapshots", status_code=201)
def create_snapshot(
    project_id: int,
    body: dict | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """创建当前画布快照。body 可选 {label}；自动滚动删除超出 20 份的旧快照。"""
    _own(db, project_id, user)
    latest = (
        db.query(func.max(CanvasSnapshot.version))
        .filter(CanvasSnapshot.project_id == project_id)
        .scalar()
    )
    version = (latest or 0) + 1
    snap = CanvasSnapshot(
        project_id=project_id,
        version=version,
        label=(body or {}).get("label") or f"版本 {version}",
        nodes_json=_dump_nodes(db, project_id),
        connections_json=_dump_connections(db, project_id),
        created_by=user.id,
    )
    db.add(snap)
    db.commit()
    db.refresh(snap)

    # 滚动清理：仅保留最近 MAX_SNAPSHOTS 份
    all_snaps = (
        db.query(CanvasSnapshot)
        .filter(CanvasSnapshot.project_id == project_id)
        .order_by(CanvasSnapshot.version.desc())
        .all()
    )
    for old in all_snaps[MAX_SNAPSHOTS:]:
        db.delete(old)
    db.commit()

    return {"snapshot": {"id": snap.id, "version": version, "label": snap.label}}


@router.post("/projects/{project_id}/snapshots/{snapshot_id}/restore")
def restore_snapshot(
    project_id: int,
    snapshot_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """恢复快照：以快照内容全量替换当前节点/连线（恢复前自动建一份"恢复前"快照）。"""
    project = _own(db, project_id, user)
    snap = db.get(CanvasSnapshot, snapshot_id)
    if snap is None or snap.project_id != project.id:
        raise HTTPException(
            status_code=404, detail={"code": "SNAPSHOT_NOT_FOUND", "message": "快照不存在"}
        )

    # 恢复前先快照当前态（安全网）
    pre_label = f"恢复前（v{snap.version} 之前）"
    pre_version = (
        db.query(func.max(CanvasSnapshot.version))
        .filter(CanvasSnapshot.project_id == project_id)
        .scalar()
    ) or 0
    db.add(
        CanvasSnapshot(
            project_id=project_id,
            version=pre_version + 1,
            label=pre_label,
            nodes_json=_dump_nodes(db, project_id),
            connections_json=_dump_connections(db, project_id),
            created_by=user.id,
        )
    )
    db.commit()

    # 全量替换节点/连线（client_id 保留在快照里，恢复后客户端可继续稳定 upsert）
    node_defs = json.loads(snap.nodes_json)
    conn_defs = json.loads(snap.connections_json)
    db.query(CanvasNode).filter(CanvasNode.project_id == project.id).delete()
    id_map: dict[int, int] = {}
    for n in node_defs:
        meta = dict(n.get("metadata") or {})
        node = CanvasNode(
            project_id=project.id,
            type=n["type"],
            title=n.get("title", ""),
            position_x=n["position"]["x"],
            position_y=n["position"]["y"],
            width=n.get("width"),
            height=n.get("height"),
            metadata_json=json.dumps(meta, ensure_ascii=False),
        )
        db.add(node)
        db.flush()  # 拿新 id
        old_id = n.get("id")
        if old_id is not None:
            id_map[int(old_id)] = node.id
    for c in conn_defs:
        db.add(
            CanvasConnection(
                project_id=project.id,
                # 连线端点随节点重建映射（否则指向已不存在的旧 id → 悬空边）
                from_node_id=id_map.get(int(c["from_node_id"]), int(c["from_node_id"])),
                to_node_id=id_map.get(int(c["to_node_id"]), int(c["to_node_id"])),
            )
        )
    # 恢复 = 服务端改动：bump revision，其他客户端下次保存会拿到 409 而非静默覆盖恢复态
    project.revision = (project.revision or 0) + 1
    db.commit()

    nodes = db.query(CanvasNode).filter(CanvasNode.project_id == project.id).all()
    return {
        "restored": True,
        "nodes": [n.to_dict() for n in nodes],
        "connections": [
            c.to_dict()
            for c in db.query(CanvasConnection)
            .filter(CanvasConnection.project_id == project.id)
            .all()
        ],
    }
