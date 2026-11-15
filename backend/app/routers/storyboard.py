"""分镜（Storyboard）路由（0.8.0-M1/M2）：CRUD + LLM 拆分 + 一键生成编排 + SSE 进度。"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.core.db import get_db
from backend.app.core.times import iso_utc
from backend.app.models import User
from backend.app.models.storyboard import Storyboard, StoryboardSegment
from backend.app.routers.auth import get_current_user
from backend.app.services import storyboard_service
from backend.app.services.param_schema import merge_model_param_defaults

router = APIRouter(prefix="/api/storyboards", tags=["storyboards"])


def _owned(db: Session, user_id: int, sid: int) -> Storyboard:
    sb = db.get(Storyboard, sid)
    if sb is None or sb.user_id != user_id:
        raise HTTPException(
            status_code=404,
            detail={"code": "STORYBOARD_NOT_FOUND", "message": "分镜不存在"},
        )
    return sb


def _segments_dict(db: Session, sid: int) -> list[dict]:
    segs = (
        db.query(StoryboardSegment)
        .filter(StoryboardSegment.storyboard_id == sid)
        .order_by(StoryboardSegment.segment_index)
        .all()
    )
    return [
        {
            "id": g.id,
            "segment_index": g.segment_index,
            "prompt": g.prompt,
            "camera_motion": g.camera_motion,
            "mood": g.mood,
            "audio_cue": g.audio_cue,
            "status": g.status,
            "task_id": g.task_id,
            "keyframe_url": g.keyframe_url,
            "keyframe_task_id": g.keyframe_task_id,
            "target_duration_ms": g.target_duration_ms,
            "output_video_url": g.output_video_url,
        }
        for g in segs
    ]


class StoryboardCreate(BaseModel):
    title: str | None = None
    short_description: str = Field(min_length=4, max_length=4000)
    story_summary: str | None = None
    segments_count: int = Field(default=6, ge=1, le=60)
    segment_duration: int = Field(default=6, ge=3, le=15)
    resolution: str = "480p"


class StoryboardUpdate(BaseModel):
    title: str | None = None
    short_description: str | None = Field(default=None, min_length=4, max_length=4000)
    story_summary: str | None = None
    segments_count: int | None = Field(default=None, ge=1, le=60)
    segment_duration: int | None = Field(default=None, ge=3, le=15)
    resolution: str | None = None
    character_ref_image_url: str | None = Field(default=None, max_length=500)
    bgm_url: str | None = Field(default=None, max_length=500)
    style_prompt: str | None = Field(default=None, max_length=500)


def _require_outputs_path(value: str, field_label: str) -> str:
    """P1 SSRF 守卫：站内素材引用仅限 /outputs/ 前缀。

    任意 http(s) 会经 ensure_local 服务端下载且产物经 /outputs 静态暴露
    （完整 SSRF 读取原语）；上传统一走 /api/uploads → /outputs/uploads/。空串放行（清除语义）。"""
    v = value.strip()
    if v and not v.startswith("/outputs/"):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "URL_INVALID",
                "message": f"{field_label} 仅支持站内 /outputs/ 路径（请先通过上传入口）",
            },
        )
    return v



class SegmentsReplace(BaseModel):
    segments: list[dict]


class FromTemplateRequest(BaseModel):
    template_id: int
    title: str | None = Field(default=None, max_length=200)


@router.post("/from-template", status_code=201)
def create_storyboard_from_template(
    body: FromTemplateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """从模板创建分镜：复制段结构（prompt/镜头/情绪/音频/逐段时长），产物与状态为空。"""
    import json as _ft_json

    from backend.app.models.template import StoryboardTemplate

    t = db.get(StoryboardTemplate, body.template_id)
    if t is None or t.user_id != user.id:
        raise HTTPException(
            status_code=404,
            detail={"code": "TEMPLATE_NOT_FOUND", "message": "模板不存在"},
        )
    try:
        template_segs = _ft_json.loads(t.segments_json or "[]")
        template_segs = template_segs if isinstance(template_segs, list) else []
    except _ft_json.JSONDecodeError:
        template_segs = []
    if not template_segs:
        raise HTTPException(
            status_code=422,
            detail={"code": "TEMPLATE_EMPTY", "message": "模板没有分段结构"},
        )

    sb = Storyboard(
        user_id=user.id,
        title=(body.title or "").strip() or f"{t.title} · 新片",
        short_description=t.description or f"从模板「{t.title}」创建",
        segments_count=len(template_segs),
    )
    db.add(sb)
    db.flush()
    for idx, item in enumerate(template_segs, start=1):
        dur = item.get("target_duration_s")
        db.add(
            StoryboardSegment(
                storyboard_id=sb.id,
                segment_index=int(item.get("segment_index") or 0) or idx,
                prompt=str(item.get("prompt") or ""),
                camera_motion=str(item.get("camera_motion") or ""),
                mood=str(item.get("mood") or ""),
                audio_cue=str(item.get("audio_cue") or ""),
                target_duration_ms=int(dur) * 1000 if dur else None,
            )
        )
    db.commit()
    db.refresh(sb)
    return {"storyboard": {**sb.to_dict(), "segments": _segments_dict(db, sb.id)}}


@router.post("")
def create_storyboard(
    body: StoryboardCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    sb = Storyboard(
        user_id=user.id,
        title=body.title or "未命名分镜",
        short_description=body.short_description,
        story_summary=body.story_summary,
        segments_count=body.segments_count,
        segment_duration=body.segment_duration,
        resolution=body.resolution,
    )
    db.add(sb)
    db.commit()
    db.refresh(sb)
    return {"storyboard": {**sb.to_dict(), "segments": []}}


@router.get("")
def list_storyboards(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = (
        db.query(Storyboard)
        .filter(Storyboard.user_id == user.id)
        .order_by(Storyboard.updated_at.desc())
        .all()
    )
    return {
        "storyboards": [
            {
                "id": sb.id,
                "title": sb.title,
                "short_description": sb.short_description,
                "segments_count": sb.segments_count,
                "status": sb.status,
                "updated_at": iso_utc(sb.updated_at),
            }
            for sb in rows
        ]
    }


@router.get("/{sid}")
def get_storyboard(sid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    sb = _owned(db, user.id, sid)
    return {"storyboard": {**sb.to_dict(), "segments": _segments_dict(db, sid)}}


@router.patch("/{sid}")
def update_storyboard(
    sid: int,
    body: StoryboardUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    sb = _owned(db, user.id, sid)
    if body.character_ref_image_url is not None:
        body.character_ref_image_url = _require_outputs_path(
            body.character_ref_image_url, "角色定妆照"
        )
    for field in (
        "title",
        "short_description",
        "story_summary",
        "segments_count",
        "segment_duration",
        "resolution",
        "character_ref_image_url",
    ):
        val = getattr(body, field)
        if val is not None:
            setattr(sb, field, val)
    if body.bgm_url is not None:
        # BGM 存 metadata（零迁移）；空串清除
        import json as _bgm_json

        try:
            meta = _bgm_json.loads(sb.metadata_json or "{}")
            meta = meta if isinstance(meta, dict) else {}
        except _bgm_json.JSONDecodeError:
            meta = {}
        val = body.bgm_url.strip()
        if val:
            meta["bgm_url"] = _require_outputs_path(val, "BGM 音轨")
        else:
            meta.pop("bgm_url", None)
        sb.metadata_json = _bgm_json.dumps(meta, ensure_ascii=False)
    if body.style_prompt is not None:
        import json as _style_json

        try:
            meta = _style_json.loads(sb.metadata_json or "{}")
            meta = meta if isinstance(meta, dict) else {}
        except _style_json.JSONDecodeError:
            meta = {}
        val = body.style_prompt.strip()
        if val:
            meta["style_prompt"] = val
        else:
            meta.pop("style_prompt", None)
        sb.metadata_json = _style_json.dumps(meta, ensure_ascii=False)
    db.commit()
    db.refresh(sb)
    return {"storyboard": {**sb.to_dict(), "segments": _segments_dict(db, sid)}}


@router.delete("/{sid}", status_code=204)
def delete_storyboard(
    sid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    sb = _owned(db, user.id, sid)
    db.query(StoryboardSegment).filter(StoryboardSegment.storyboard_id == sid).delete()
    db.delete(sb)
    db.commit()


@router.put("/{sid}/segments")
def replace_segments(
    sid: int,
    body: SegmentsReplace,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """审校保存：整表替换分镜（重排 index），按内容签名保留既有产物（走查 P0）。"""
    sb = _owned(db, user.id, sid)
    if sb.status == "generating":
        raise HTTPException(
            status_code=422,
            detail={"code": "STORYBOARD_GENERATING", "message": "全片生成中，暂不可保存分镜"},
        )

    sig_fields = ("prompt", "camera_motion", "mood", "audio_cue")

    def _sig(item: dict) -> str:
        return "|".join(str(item.get(k) or "").strip() for k in sig_fields)

    old = (
        db.query(StoryboardSegment)
        .filter(StoryboardSegment.storyboard_id == sid)
        .order_by(StoryboardSegment.segment_index)
        .all()
    )
    unused = list(old)
    rebuilt = []
    for i, item in enumerate(body.segments, start=1):
        prompt = str(item.get("prompt") or "").strip() or f"第 {i} 段"
        seg = StoryboardSegment(
            storyboard_id=sid,
            segment_index=i,
            prompt=prompt,
            camera_motion=str(item.get("camera_motion") or ""),
            mood=str(item.get("mood") or ""),
            audio_cue=str(item.get("audio_cue") or ""),
        )
        # consume-once 贪婪匹配：内容未变的段沿用旧产物/状态（支持重排序跟随内容）；
        # 载荷中的 status/output_video_url 一律忽略——产物归属只由后端匹配决定
        match = next((o for o in unused if _sig({**item}) == _sig_of(o, sig_fields)), None)
        if match is not None:
            unused.remove(match)
            for f in (
                "status", "task_id", "output_video_url", "last_frame_url",
                "retry_count", "error_message", "duration_ms",
            ):
                setattr(seg, f, getattr(match, f))
        # 逐段时长：意图属性，不入签名、无条件取载荷值（None=回落分镜级）
        raw_dur = item.get("target_duration_s")
        if raw_dur is not None:
            if raw_dur not in (4, 6, 10):
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "TARGET_DURATION_INVALID",
                        "message": "分段时长仅支持 4 / 6 / 10 秒",
                    },
                )
            seg.target_duration_ms = int(raw_dur) * 1000
        rebuilt.append(seg)

    edited = sum(
        1
        for new, old in zip(rebuilt, old, strict=False)
        if _sig_of(new, sig_fields) != _sig_of(old, sig_fields)
    )
    try:
        sb_meta = _sb_metadata(sb)
    except Exception:  # noqa: BLE001
        sb_meta = {}
    sb_meta["last_edit_stats"] = {"edited": edited, "total": len(rebuilt)}
    sb.metadata_json = json.dumps(sb_meta, ensure_ascii=False)

    db.query(StoryboardSegment).filter(StoryboardSegment.storyboard_id == sid).delete()
    db.flush()
    for seg in rebuilt:
        db.add(seg)
    sb.segments_count = len(body.segments)
    db.commit()
    return {"segments": _segments_dict(db, sid)}


def _sig_of(seg: StoryboardSegment, sig_fields: tuple[str, ...]) -> str:
    return "|".join(str(getattr(seg, k) or "").strip() for k in sig_fields)


class SplitRequest(BaseModel):
    confirm: bool = Field(default=True, description="确认覆盖现有分镜")


class StoryboardGenerateRequest(BaseModel):
    model: str = ""
    confirm: bool = False
    concurrency: int = 1  # 1-4；云引擎可并行，H3 保持 1


def _sb_metadata(sb: Storyboard) -> dict:
    import json as _json

    try:
        parsed = _json.loads(sb.metadata_json or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except _json.JSONDecodeError:
        return {}


@router.post("/{sid}/generate")
async def generate_storyboard(
    sid: int,
    body: StoryboardGenerateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """一键生成全片：先预估（confirm=false）→ 显式确认后入队编排。"""
    import json as _json

    from backend.app.services.task_queue import QueueUnavailableError, enqueue_storyboard_kick

    sb = _owned(db, user.id, sid)
    model = (body.model or "").strip()
    if not model:
        raise HTTPException(
            status_code=422,
            detail={"code": "MODEL_REQUIRED", "message": "请选择生成模型（视频渠道）"},
        )
    segs = (
        db.query(StoryboardSegment)
        .filter(StoryboardSegment.storyboard_id == sid)
        .order_by(StoryboardSegment.segment_index)
        .all()
    )
    if not segs:
        raise HTTPException(
            status_code=422,
            detail={"code": "SEGMENTS_EMPTY", "message": "分镜表为空，请先拆分或添加分镜"},
        )
    if sb.status == "generating":
        raise HTTPException(
            status_code=422,
            detail={"code": "ALREADY_GENERATING", "message": "全片正在生成中，请勿重复启动"},
        )

    from backend.app.services.model_pricing import resolve_cost

    per_segment = round(
        resolve_cost(db, "video", model, {"duration": sb.segment_duration}), 4
    )
    estimate = {
        "model": model,
        "segments": len(segs),
        "per_segment": per_segment,
        "total": round(per_segment * len(segs), 4),
        "currency": "CNY",
    }
    if not body.confirm:
        # 耗时预估：同模型最近 10 个完成任务的 duration_ms 中位数 × 段数（串行）
        import statistics as _stats

        from backend.app.models import Task as _Task

        hist = (
            db.query(_Task.duration_ms)
            .filter(
                _Task.type == "video",
                _Task.status == "completed",
                _Task.model == model,
                _Task.duration_ms.isnot(None),
            )
            .order_by(_Task.created_at.desc())
            .limit(10)
            .all()
        )
        durations = [h[0] for h in hist if h[0]]
        per_ms = _stats.median(durations) if durations else 90_000
        estimate["est_minutes"] = max(1, round(per_ms * len(segs) / 60_000))
        return {"requires_confirm": True, "estimate": estimate}

    if not 1 <= body.concurrency <= 4:
        raise HTTPException(
            status_code=422,
            detail={"code": "CONCURRENCY_INVALID", "message": "并发仅支持 1-4"},
        )
    # P0（生产 sb5 案例）：全 completed 无段可入队——翻 generating 后没有任何
    # 终态钩子会来翻转状态，分镜将永久卡「生成中」；必须显式拒绝。
    if all(seg.status == "completed" for seg in segs):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "NOTHING_TO_REGENERATE",
                "message": "全部分段已完成，无失败段可重跑；调整内容请用单段重出逐段迭代",
            },
        )
    prev_status = sb.status
    # 失败段复位重入队列（completed/pending 不动）；手动重入重置自动重试预算
    for seg in segs:
        if seg.status == "failed":
            seg.status = "pending"
            seg.retry_count = 0
            seg.error_message = None
    sb.status = "generating"
    sb.started_generation_at = datetime.utcnow()
    sb.completed_at = None
    meta = _sb_metadata(sb)
    meta["generation_model"] = model
    meta["max_concurrent"] = body.concurrency
    sb.metadata_json = _json.dumps(meta, ensure_ascii=False)
    # R2 数据一致性：复位后立即重算聚合（重编辑后的分镜 completed_segments
    # 曾保留旧值——生产 sb4 出现过 6/3 自相矛盾）
    from backend.app.services.storyboard_orchestrator import _reaggregate

    _reaggregate(db, sb)
    db.commit()
    try:
        await enqueue_storyboard_kick(sid)
    except QueueUnavailableError as e:
        # 回滚复位，不留假 generating
        sb.status = prev_status
        sb.started_generation_at = None
        db.commit()
        raise HTTPException(
            status_code=503,
            detail={"code": "QUEUE_UNAVAILABLE", "message": f"任务队列不可用: {e}"},
        ) from e
    return {"storyboard": {**sb.to_dict(), "segments": _segments_dict(db, sid)}}


@router.post("/{sid}/segments/{index}/regenerate")
async def regenerate_segment(
    sid: int,
    index: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """completed 段单段重出：改 prompt 后只重跑该段（迭代闭环，走查 P0）。"""
    from backend.app.services.task_queue import QueueUnavailableError, enqueue_storyboard_kick

    sb = _owned(db, user.id, sid)
    if sb.status == "generating":
        raise HTTPException(
            status_code=422,
            detail={"code": "STORYBOARD_GENERATING", "message": "全片生成中，暂不可重出分段"},
        )
    seg = (
        db.query(StoryboardSegment)
        .filter(StoryboardSegment.storyboard_id == sid, StoryboardSegment.segment_index == index)
        .first()
    )
    if seg is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "SEGMENT_NOT_FOUND", "message": "分段不存在"},
        )
    if seg.status != "completed":
        raise HTTPException(
            status_code=422,
            detail={
                "code": "SEGMENT_NOT_COMPLETED",
                "message": "仅已完成分段可重出（失败段请用重试）",
            },
        )
    prev_status = sb.status
    seg.status = "pending"
    seg.task_id = None  # 清除旧任务引用：迟到终态由陈旧守卫拒绝
    seg.retry_count = 0
    seg.error_message = None
    if sb.status in {"completed", "failed"}:
        sb.status = "generating"
        sb.started_generation_at = datetime.utcnow()
        sb.completed_at = None
    sb_meta = _sb_metadata(sb)
    sb_meta["regenerate_total"] = int(sb_meta.get("regenerate_total") or 0) + 1
    sb.metadata_json = json.dumps(sb_meta, ensure_ascii=False)
    db.commit()
    try:
        await enqueue_storyboard_kick(sid)
    except QueueUnavailableError as e:
        seg.status = "completed"
        if sb.status != prev_status:
            sb.status = prev_status
            sb.started_generation_at = None
            sb.completed_at = datetime.utcnow()
        db.commit()
        raise HTTPException(
            status_code=503,
            detail={"code": "QUEUE_UNAVAILABLE", "message": f"任务队列不可用: {e}"},
        ) from e
    return {"segments": _segments_dict(db, sid)}


class KeyframesRequest(BaseModel):
    segments: list[int] | None = None
    count: int = 4  # 1-4，越界走端点内统一错误契约（COUNT_INVALID）
    model: str | None = None
    chain: bool = False  # 接力链模式：顺序 i2i 逐段传递（完成段 N 自动派 N+1）


class CharacterAttachRequest(BaseModel):
    character_ids: list[int]


@router.post("/{sid}/characters/attach")
def attach_characters(
    sid: int,
    body: CharacterAttachRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """把角色库资产带入分镜：并入 character_profiles 快照（同名刷新描述，幂等）。"""
    import json as _attach_json

    from backend.app.models.character import CharacterAsset

    sb = _owned(db, user.id, sid)
    assets = (
        db.query(CharacterAsset)
        .filter(
            CharacterAsset.user_id == user.id,
            CharacterAsset.id.in_(body.character_ids),
        )
        .all()
    )
    if len(assets) != len(set(body.character_ids)):
        raise HTTPException(
            status_code=404,
            detail={"code": "CHARACTER_NOT_FOUND", "message": "角色不存在或不属于当前用户"},
        )

    try:
        profiles = _attach_json.loads(sb.character_profiles or "[]")
        profiles = profiles if isinstance(profiles, list) else []
    except _attach_json.JSONDecodeError:
        profiles = []

    by_name = {p.get("name"): p for p in profiles if isinstance(p, dict)}
    for a in assets:
        by_name[a.name] = {"name": a.name, "description": a.description}
    sb.character_profiles = _attach_json.dumps(list(by_name.values()), ensure_ascii=False)
    db.commit()
    db.refresh(sb)
    return {"storyboard": {**sb.to_dict()}}


@router.post("/{sid}/keyframes")
async def generate_keyframes(
    sid: int,
    body: KeyframesRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """关键帧批量生成：每段一镜多图（image 域，generate 队列云 API 并行）。"""
    from backend.app.routers.canvas import _default_model_for
    from backend.app.services.storyboard_orchestrator import (
        _profiles,
        _with_style,
        resolve_prompt_mentions,
    )
    from backend.app.services.task_queue import QueueUnavailableError, enqueue_generation_task
    from backend.app.services.task_store import create_task

    sb = _owned(db, user.id, sid)
    if sb.status == "generating":
        raise HTTPException(
            status_code=422,
            detail={"code": "STORYBOARD_GENERATING", "message": "全片生成中，暂不可生成关键帧"},
        )
    if not 1 <= body.count <= 4:
        raise HTTPException(
            status_code=422,
            detail={"code": "COUNT_INVALID", "message": "关键帧数量仅支持 1-4 张"},
        )
    model = (body.model or "").strip() or _default_model_for(db, "image")

    query = db.query(StoryboardSegment).filter(StoryboardSegment.storyboard_id == sid)
    if body.chain:
        # 接力链：只派首个无关键帧任务的段，后续由终态钩子逐段推进
        first_pending = (
            query.filter(StoryboardSegment.keyframe_task_id.is_(None))
            .order_by(StoryboardSegment.segment_index)
            .first()
        )
        segs = [first_pending] if first_pending else []
    else:
        if body.segments:
            query = query.filter(StoryboardSegment.segment_index.in_(body.segments))
        segs = query.order_by(StoryboardSegment.segment_index).all()
    if body.segments and len(segs) != len(set(body.segments)):
        raise HTTPException(
            status_code=404,
            detail={"code": "SEGMENT_NOT_FOUND", "message": "指定分段不存在"},
        )
    if not segs:
        raise HTTPException(
            status_code=422,
            detail={"code": "NO_SEGMENTS", "message": "没有待生成关键帧的分段"},
        )

    if body.chain:
        from backend.app.services.storyboard_orchestrator import _meta

        meta = _meta(sb)
        meta["keyframe_chain"] = True
        sb.metadata_json = json.dumps(meta, ensure_ascii=False)
        db.commit()

    tasks_out = []
    for seg in segs:
        refs = (
            [sb.character_ref_image_url]
            if (sb.character_ref_image_url or "").strip() and seg.segment_index == 1
            else []
        )
        task = create_task(
            db,
            user_id=user.id,
            type_="image",
            prompt=_with_style(
                resolve_prompt_mentions(seg.prompt, _profiles(sb))
                + ", 16:9 cinematic widescreen composition",
                sb,
            ),
            model=model,
            params=merge_model_param_defaults(
                db,
                "image",
                model,
                {
                    "storyboard_id": sid,
                    "segment_id": seg.id,
                    "keyframe": True,
                    "keyframe_chain": body.chain,
                    "image_count": body.count,
                    "reference_urls": refs,
                },
            ),
            queue_name="generate",
        )
        seg.keyframe_task_id = task.id
        db.commit()
        tasks_out.append({"segment_index": seg.segment_index, "task_id": task.id})
        try:
            await enqueue_generation_task(task.id, "generate")
        except QueueUnavailableError as e:
            raise HTTPException(
                status_code=503,
                detail={"code": "QUEUE_UNAVAILABLE", "message": f"任务队列不可用: {e}"},
            ) from e
    return {"tasks": tasks_out, "count": len(tasks_out)}


@router.post("/{sid}/stop")
async def stop_storyboard(
    sid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """停止生成：pending 段终局化，在跑段发起取消；终态聚合交由终态钩子完成。"""
    from backend.app.services.task_queue import publish_storyboard_event
    from backend.app.services.task_store import mark_cancel_requested

    sb = _owned(db, user.id, sid)
    if sb.status != "generating":
        raise HTTPException(
            status_code=422,
            detail={"code": "NOT_GENERATING", "message": "当前不在生成中"},
        )
    segs = (
        db.query(StoryboardSegment)
        .filter(StoryboardSegment.storyboard_id == sid)
        .order_by(StoryboardSegment.segment_index)
        .all()
    )
    for seg in segs:
        if seg.status in {"queued", "generating"} and seg.task_id:
            mark_cancel_requested(db, seg.task_id)
        elif seg.status == "pending":
            seg.status = "failed"
            seg.error_message = "已手动停止"
    # 审计批次二（P2）：事件/响应携带的段计数必须重新聚合（原版为翻转前旧值）
    from backend.app.services.storyboard_orchestrator import _reaggregate

    _reaggregate(db, sb)
    sb.status = "failed"
    db.commit()
    publish_storyboard_event(
        sid,
        "storyboard_update",
        {
            "status": sb.status,
            "completed_segments": sb.completed_segments,
            "failed_segments": sb.failed_segments,
        },
    )
    return {"storyboard": {**sb.to_dict(), "segments": _segments_dict(db, sid)}}


@router.post("/{sid}/to-canvas", status_code=201)
def to_canvas_endpoint(
    sid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """成片+分段回流画布：新建项目并直写 video 节点（client_id/outputUrl 契约）。"""
    import json as _json

    from backend.app.models.canvas import CanvasNode, CanvasProject

    sb = _owned(db, user.id, sid)
    segs = (
        db.query(StoryboardSegment)
        .filter(
            StoryboardSegment.storyboard_id == sid,
            StoryboardSegment.status == "completed",
            StoryboardSegment.output_video_url.isnot(None),
        )
        .order_by(StoryboardSegment.segment_index)
        .all()
    )
    if not segs and not sb.final_video_url:
        raise HTTPException(
            status_code=422,
            detail={"code": "NOTHING_TO_EXPORT", "message": "没有可回流的成片或分段"},
        )

    project = CanvasProject(user_id=user.id, title=f"{sb.title} · 成片")
    db.add(project)
    db.flush()

    count = 0
    if sb.final_video_url:
        db.add(
            CanvasNode(
                project_id=project.id,
                type="video",
                title=f"{sb.title or '成片'} · 成片",
                position_x=0,
                position_y=-280,
                width=280,
                height=180,
                metadata_json=_json.dumps(
                    {
                        "client_id": f"sb{sid}-final",
                        "outputUrl": sb.final_video_url,
                        "storyboardFinal": True,
                    },
                    ensure_ascii=False,
                ),
            )
        )
        count += 1
    for i, seg in enumerate(segs):
        db.add(
            CanvasNode(
                project_id=project.id,
                type="video",
                title=f"第 {seg.segment_index} 段",
                position_x=i * 300,
                position_y=0,
                width=280,
                height=180,
                metadata_json=_json.dumps(
                    {
                        "client_id": f"sb{sid}-seg{seg.segment_index}",
                        "outputUrl": seg.output_video_url,
                        "prompt": seg.prompt or "",
                    },
                    ensure_ascii=False,
                ),
            )
        )
        count += 1
    db.commit()
    return {"project_id": project.id, "node_count": count}


@router.post("/{sid}/segments/{index}/retry")
async def retry_segment(
    sid: int,
    index: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """失败段单独重试：重置后重新入队编排（不重跑全片）。"""
    from backend.app.services.task_queue import QueueUnavailableError, enqueue_storyboard_kick

    sb = _owned(db, user.id, sid)
    seg = (
        db.query(StoryboardSegment)
        .filter(StoryboardSegment.storyboard_id == sid, StoryboardSegment.segment_index == index)
        .first()
    )
    if seg is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "SEGMENT_NOT_FOUND", "message": "分段不存在"},
        )
    if seg.status != "failed":
        raise HTTPException(
            status_code=422,
            detail={"code": "SEGMENT_NOT_FAILED", "message": "仅失败分段可重试"},
        )
    prev_status = sb.status
    seg.status = "pending"
    seg.retry_count = 0
    seg.error_message = None
    if sb.status in {"completed", "failed"}:
        sb.status = "generating"
        sb.started_generation_at = datetime.utcnow()
        sb.completed_at = None
    db.commit()
    try:
        await enqueue_storyboard_kick(sid)
    except QueueUnavailableError as e:
        # 回滚：段与分镜状态都复原，不留假 generating（Review Focus #5）
        seg.status = "failed"
        if sb.status != prev_status:
            sb.status = prev_status
            sb.started_generation_at = None
            sb.completed_at = None
        db.commit()
        raise HTTPException(
            status_code=503,
            detail={"code": "QUEUE_UNAVAILABLE", "message": f"任务队列不可用: {e}"},
        ) from e
    return {"segments": _segments_dict(db, sid)}


class StoryboardComposeRequest(BaseModel):
    bgm_url: str | None = Field(default=None, max_length=2000)


@router.post("/{sid}/compose")
async def compose_endpoint(
    sid: int,
    body: StoryboardComposeRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """手动（重）合成成片：仅拼 completed 段；compose 任务入 generate 队列异步执行。"""

    from backend.app.services.storyboard_orchestrator import _meta
    from backend.app.services.task_queue import QueueUnavailableError, enqueue_storyboard_compose

    sb = _owned(db, user.id, sid)
    if sb.status == "generating":
        raise HTTPException(
            status_code=422,
            detail={"code": "NOT_GENERATING", "message": "全片生成中，结束后再合成"},
        )
    done = (
        db.query(StoryboardSegment)
        .filter(
            StoryboardSegment.storyboard_id == sid,
            StoryboardSegment.status == "completed",
        )
        .count()
    )
    if done < 1:
        raise HTTPException(
            status_code=422,
            detail={"code": "NO_SEGMENTS", "message": "没有已完成的分段可合成"},
        )
    if _meta(sb).get("compose_status") == "running":
        raise HTTPException(
            status_code=422,
            detail={"code": "COMPOSE_RUNNING", "message": "成片正在合成中"},
        )

    # BGM 决策：body 显式 > metadata_json.bgm_url（面板上传后即生效）；仅限站内 /outputs/
    from backend.app.services.storyboard_orchestrator import _meta

    meta = _meta(sb)
    if (body.bgm_url or "").strip():
        body.bgm_url = _require_outputs_path(body.bgm_url or "", "BGM 音轨")
    bgm_url = (body.bgm_url or "").strip() or meta.get("bgm_url") or None
    # running 由 job 侧服务层置位——API 预置会与 guard 自挡（COMPOSE_RUNNING）
    try:
        await enqueue_storyboard_compose(sid, bgm_url)
    except QueueUnavailableError as e:
        raise HTTPException(
            status_code=503,
            detail={"code": "QUEUE_UNAVAILABLE", "message": f"任务队列不可用: {e}"},
        ) from e
    return {"compose_status": "running", "segments": done}


@router.get("/{sid}/stats")
def storyboard_stats(
    sid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """创作质量信号：拆分改动率 / 重出次数 / 段状态分布。"""
    sb = _owned(db, user.id, sid)
    segs = (
        db.query(StoryboardSegment)
        .filter(StoryboardSegment.storyboard_id == sid)
        .all()
    )
    meta = _sb_metadata(sb)
    edit_stats = meta.get("last_edit_stats") or {}
    total_edits = edit_stats.get("total") or 0
    edited = edit_stats.get("edited") or 0
    return {
        "segments_total": len(segs),
        "completed": sum(1 for s in segs if s.status == "completed"),
        "failed": sum(1 for s in segs if s.status == "failed"),
        "keyframed": sum(1 for s in segs if s.keyframe_url),
        "regenerations": int(meta.get("regenerate_total") or 0),
        "edited": edited,
        "edit_ratio": round(edited / total_edits, 2) if total_edits else None,
    }


def _storyboard_stream(db: Session, sid: int):
    """SSE：先发全量快照（晚订阅兜底），转发分镜/合成事件直至终态。"""
    from backend.app.services.storyboard_orchestrator import _meta
    from backend.app.services.task_queue import subscribe_storyboard_events

    current = db.get(Storyboard, sid)
    if current is None:
        yield 'data: {"type": "error", "data": {"message": "storyboard not found"}}\n\n'
        return
    snapshot_payload = {"storyboard": {**current.to_dict(), "segments": _segments_dict(db, sid)}}
    snapshot = json.dumps({"type": "snapshot", "data": snapshot_payload}, ensure_ascii=False)
    yield f"data: {snapshot}\n\n"
    terminal_types = {"done", "compose_done", "compose_failed"}
    if current.status in {"completed", "failed"}:
        compose_status = _meta(current).get("compose_status")
        if compose_status != "running":
            yield 'data: ' + json.dumps(
                {"type": "done", "data": {"status": current.status}}, ensure_ascii=False
            ) + '\n\n'
            return
        # compose 进行中：继续订阅直至合成终帧（晚订阅兜底）
    pubsub = subscribe_storyboard_events(sid)
    try:
        while True:
            message = pubsub.get_message(timeout=5.0)
            if message is None:
                yield ": keepalive\n\n"
                continue
            if message.get("type") == "message":
                payload = message.get("data") or "{}"
                yield f"data: {payload}\n\n"
                parsed = json.loads(payload)
                if parsed.get("type") in terminal_types:
                    return
    finally:
        pubsub.close()


class KeyframeSelectRequest(BaseModel):
    url: str = Field(default="", max_length=500)


@router.post("/{sid}/segments/{index}/keyframe/select")
def select_keyframe(
    sid: int,
    index: int,
    body: KeyframeSelectRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """定帧：选中关键帧候选（或任意 /outputs/ 图）作为该段视频首帧；空串清除。"""
    from backend.app.services.task_queue import publish_storyboard_event

    _owned(db, user.id, sid)  # 归属校验
    seg = (
        db.query(StoryboardSegment)
        .filter(StoryboardSegment.storyboard_id == sid, StoryboardSegment.segment_index == index)
        .first()
    )
    if seg is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "SEGMENT_NOT_FOUND", "message": "分段不存在"},
        )
    url = (body.url or "").strip()
    if url and not url.startswith("/outputs/"):
        raise HTTPException(
            status_code=422,
            detail={"code": "KEYFRAME_URL_INVALID", "message": "定帧地址必须为 /outputs/ 资产"},
        )
    seg.keyframe_url = url or None
    db.commit()
    publish_storyboard_event(
        sid,
        "segment_update",
        {
            "keyframe": True,
            "segment_id": seg.id,
            "index": seg.segment_index,
            "keyframe_url": seg.keyframe_url,
        },
    )
    return {"segments": _segments_dict(db, sid)}


@router.get("/{sid}/stream")
def stream_storyboard_events(
    sid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> StreamingResponse:
    """SSE：分镜生成进度流（snapshot 先行 + 事件续传 + keepalive）。"""
    _owned(db, user.id, sid)  # 鉴权与归属校验
    return StreamingResponse(
        _storyboard_stream(db, sid),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


@router.post("/{sid}/split")
def split_storyboard(
    sid: int,
    body: SplitRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """LLM 拆分：剧本简报 → 分镜表（覆盖现有分镜，需 confirm）。"""
    sb = _owned(db, user.id, sid)
    if not body.confirm:
        raise HTTPException(
            status_code=422,
            detail={"code": "CONFIRM_REQUIRED", "message": "拆分将覆盖现有分镜，需 confirm=true"},
        )


    from backend.app.core.errors import ProviderNotConfiguredError
    from backend.app.models.capability import CapabilityConfig
    from backend.app.providers.openai_compat import OpenAICompatProvider

    # 默认 LLM 模型与画布 Agent 同源（系统默认 → 渠道首模型），不硬编码——
    # 硬编码 gpt-4o-mini 在真实渠道（SiliconFlow/MiniMax）全部 400（创作者走查发现）
    from backend.app.routers.canvas import _default_model_for
    from backend.app.services.provider_service import resolve_provider_for_task

    default_model = _default_model_for(db, "llm")
    # 必须带上 default_models 里的 channel_id——只给模型名会被首个 llm 渠道劫持（真实渠道 400）
    default_channel_id: int | None = None
    cfg_row = db.get(CapabilityConfig, "default_models")
    if cfg_row:
        try:
            import json as _cfg_json

            cfg = _cfg_json.loads(str(cfg_row.value_json or "{}")).get("llm")
            if isinstance(cfg, dict) and isinstance(cfg.get("channel_id"), int):
                default_channel_id = cfg["channel_id"]
        except ValueError:
            pass

    try:
        provider, _ch = resolve_provider_for_task(
            db, user.id, "llm", default_model, channel_id=default_channel_id
        )
    except ProviderNotConfiguredError as e:
        raise HTTPException(status_code=400, detail={"code": e.code, "message": e.message}) from e
    if not isinstance(provider, OpenAICompatProvider):
        raise HTTPException(
            status_code=400,
                        detail={
                "code": "STORYBOARD_SPLIT_UNSUPPORTED",
                "message": "当前环境无 LLM 渠道（FakeProvider 不支持拆分）",
            },
        )

    system, user_prompt = storyboard_service.split_prompts(
        sb.short_description, sb.story_summary or "", sb.segments_count, sb.segment_duration
    )

    class _Task:
        id = 0
        type = "llm"

        def __init__(self, system: str, user: str):
            self.prompt = f"{system}\n\n{user}"

        def params_dict(self):
            return {}

    result = asyncio.run(provider.run(_Task(system, user_prompt), on_event=None))
    if result.get("status") != "completed":
        raise HTTPException(
            status_code=502,
                        detail={
                "code": result.get("error_code", "SPLIT_FAILED"),
                "message": result.get("error", "拆分失败"),
            },
        )

    segs, characters = storyboard_service.parse_segments(result["output_url"], sb.segments_count)
    sb.character_profiles = json.dumps(characters, ensure_ascii=False)
    db.query(StoryboardSegment).filter(StoryboardSegment.storyboard_id == sid).delete()
    for item in segs:
        db.add(
            StoryboardSegment(
                storyboard_id=sid,
                segment_index=item["segment_index"],
                prompt=item["prompt"],
                camera_motion=item.get("camera_motion", ""),
                mood=item.get("mood", ""),
                audio_cue=item.get("audio_cue", ""),
            )
        )
    db.commit()
    return {"segments": _segments_dict(db, sid)}
