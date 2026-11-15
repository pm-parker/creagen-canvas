"""分镜模板路由（0.10）：用户级段结构快照 CRUD。"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.core.db import get_db
from backend.app.models import User
from backend.app.models.template import StoryboardTemplate
from backend.app.routers.auth import get_current_user

router = APIRouter(prefix="/api/storyboard-templates", tags=["storyboard-templates"])

_TEMPLATE_SEGMENT_KEYS = ("prompt", "camera_motion", "mood", "audio_cue", "target_duration_s")


def _owned(db: Session, user_id: int, tid: int) -> StoryboardTemplate:
    t = db.get(StoryboardTemplate, tid)
    if t is None or t.user_id != user_id:
        raise HTTPException(
            status_code=404,
            detail={"code": "TEMPLATE_NOT_FOUND", "message": "模板不存在"},
        )
    return t


def _normalize_segments(raw: list) -> list[dict]:
    out = []
    for _i, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            continue
        seg = {"segment_index": len(out) + 1}
        for key in _TEMPLATE_SEGMENT_KEYS:
            val = item.get(key)
            seg[key] = val if val else ("" if key != "target_duration_s" else None)
        if not str(seg["prompt"] or "").strip():
            seg["prompt"] = f"第 {len(out) + 1} 段（待补充）"
        out.append(seg)
    return out


class TemplateCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    segments: list = Field(min_length=1)


@router.get("")
def list_templates(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = (
        db.query(StoryboardTemplate)
        .filter(StoryboardTemplate.user_id == user.id)
        .order_by(StoryboardTemplate.updated_at.desc())
        .all()
    )
    return {"templates": [t.to_dict() for t in rows]}


@router.post("", status_code=201)
def create_template(
    body: TemplateCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    t = StoryboardTemplate(
        user_id=user.id,
        title=body.title.strip(),
        description=(body.description or "").strip() or None,
        segments_json=json.dumps(_normalize_segments(body.segments), ensure_ascii=False),
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return {"template": t.to_dict()}


@router.delete("/{tid}", status_code=204)
def delete_template(
    tid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    t = _owned(db, user.id, tid)
    db.delete(t)
    db.commit()
