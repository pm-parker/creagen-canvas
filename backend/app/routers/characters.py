"""角色资产库路由（0.9.3）：用户级跨片角色 CRUD + 带入分镜。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.core.db import get_db
from backend.app.models import User
from backend.app.models.character import CharacterAsset
from backend.app.routers.auth import get_current_user

router = APIRouter(prefix="/api/characters", tags=["characters"])


class CharacterCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1)
    ref_image_url: str | None = Field(default=None, max_length=500)


class CharacterUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    ref_image_url: str | None = Field(default=None, max_length=500)


def _owned(db: Session, user_id: int, cid: int) -> CharacterAsset:
    ch = db.get(CharacterAsset, cid)
    if ch is None or ch.user_id != user_id:
        raise HTTPException(
            status_code=404,
            detail={"code": "CHARACTER_NOT_FOUND", "message": "角色不存在"},
        )
    return ch


@router.get("")
def list_characters(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = (
        db.query(CharacterAsset)
        .filter(CharacterAsset.user_id == user.id)
        .order_by(CharacterAsset.updated_at.desc())
        .all()
    )
    return {"characters": [c.to_dict() for c in rows]}


@router.post("", status_code=201)
def create_character(
    body: CharacterCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    name = body.name.strip()
    if not name or not body.description.strip():
        raise HTTPException(
            status_code=422,
            detail={"code": "CHARACTER_FIELDS_INVALID", "message": "名称与外观描述不能为空"},
        )
    dup = (
        db.query(CharacterAsset)
        .filter(CharacterAsset.user_id == user.id, CharacterAsset.name == name)
        .first()
    )
    if dup is not None:
        raise HTTPException(
            status_code=409,
            detail={"code": "CHARACTER_NAME_EXISTS", "message": f"角色「{name}」已存在"},
        )
    ch = CharacterAsset(
        user_id=user.id,
        name=name,
        description=body.description.strip(),
        ref_image_url=(body.ref_image_url or "").strip() or None,
    )
    db.add(ch)
    db.commit()
    db.refresh(ch)
    return {"character": ch.to_dict()}


@router.patch("/{cid}")
def update_character(
    cid: int,
    body: CharacterUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    ch = _owned(db, user.id, cid)
    if body.name is not None:
        new_name = body.name.strip()
        if not new_name:
            raise HTTPException(
                status_code=422,
                detail={"code": "CHARACTER_FIELDS_INVALID", "message": "名称不能为空"},
            )
        dup = (
            db.query(CharacterAsset)
            .filter(
                CharacterAsset.user_id == user.id,
                CharacterAsset.name == new_name,
                CharacterAsset.id != cid,
            )
            .first()
        )
        if dup is not None:
            raise HTTPException(
                status_code=409,
                detail={"code": "CHARACTER_NAME_EXISTS", "message": f"角色「{new_name}」已存在"},
            )
        ch.name = new_name
    if body.description is not None:
        if not body.description.strip():
            raise HTTPException(
                status_code=422,
                detail={"code": "CHARACTER_FIELDS_INVALID", "message": "描述不能为空"},
            )
        ch.description = body.description.strip()
    if body.ref_image_url is not None:
        ch.ref_image_url = body.ref_image_url.strip() or None
    db.commit()
    db.refresh(ch)
    return {"character": ch.to_dict()}


@router.delete("/{cid}", status_code=204)
def delete_character(
    cid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    ch = _owned(db, user.id, cid)
    db.delete(ch)
    db.commit()
