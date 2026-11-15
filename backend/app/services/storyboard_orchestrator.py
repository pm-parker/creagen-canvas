"""分镜批量生成编排器（0.8.0-M2）：kick 派发 / 终态推进 / 尾帧接力 / 自动重试 / 聚合。

事件驱动链式编排（无长驻任务）：
  generate 端点复位状态 → 入队 kick → kick 按容量派下一段视频任务（video 队列）
  → 段任务终态时 run_generation_task 尾部钩子 advance_on_task_terminal
  → 更新段/提取尾帧/聚合分镜 → 再入队 kick 驱动下一段。
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from backend.app.models import Task
from backend.app.models.storyboard import Storyboard, StoryboardSegment
from backend.app.services.failure_classifier import (
    DETERMINISTIC,
    TRANSIENT,
    classify_failure,
    retry_hint,
)
from backend.app.services.task_queue import (
    enqueue_generation_task,
    enqueue_storyboard_compose,
    enqueue_storyboard_kick,
    publish_storyboard_event,
)
from backend.app.services.task_store import create_task

logger = logging.getLogger("canvas.storyboard_orch")

MAX_AUTO_RETRY = 2  # 失败自动重试预算（retry_count 达 2 次后第 3 败终局）
# 默认同一分镜并发 1（H3 GPU 单卡串行）；云引擎经 metadata.max_concurrent 提升至 1-4
DEFAULT_CONCURRENT = 1
_ACTIVE_SEGMENT_STATUSES = ("queued", "generating")
_TERMINAL_SEGMENT_STATUSES = ("completed", "failed")


def _profiles(sb: Storyboard) -> list[dict]:
    try:
        parsed = json.loads(sb.character_profiles or "[]")
        return parsed if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        return []


def resolve_prompt_mentions(text: str, profiles: list[dict]) -> str:
    """@角色名 引用展开：替换为「名字 (外观描述)」——无描述仅去 @；未提及不变。"""
    import re as _re

    out = text
    for c in profiles or []:
        if not isinstance(c, dict):
            continue
        name = str(c.get("name") or "").strip()
        if not name:
            continue
        desc = str(c.get("description") or "").strip()
        replacement = f"{name} ({desc})" if desc else name
        out = _re.sub(f"@{ _re.escape(name) }", replacement, out)
    return out


def _with_style(prompt: str, sb: Storyboard) -> str:
    """全局风格锚：统一追加到视频/关键帧派发 prompt 尾部（未设置则原样）。"""
    style = str(_meta(sb).get("style_prompt") or "").strip()
    base = (prompt or "").strip()
    if not style:
        return base
    return f"{base}, {style}"


def _meta(sb: Storyboard) -> dict[str, Any]:
    try:
        parsed = json.loads(sb.metadata_json or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _segments(db: Session, sid: int) -> list[StoryboardSegment]:
    return (
        db.query(StoryboardSegment)
        .filter(StoryboardSegment.storyboard_id == sid)
        .order_by(StoryboardSegment.segment_index)
        .all()
    )


def _segment_event(seg: StoryboardSegment, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "segment_id": seg.id,
        "index": seg.segment_index,
        "status": seg.status,
        "task_id": seg.task_id,
        "output_video_url": seg.output_video_url,
        "error_message": seg.error_message,
        "retry_count": seg.retry_count,
    }
    payload.update(extra)
    return payload


async def kick_storyboard(db: Session, storyboard_id: int) -> dict[str, int]:
    """幂等推进：generating 且容量空闲时派发下一个 pending 段。可安全重复调用。"""
    sb = db.get(Storyboard, storyboard_id)
    if sb is None or sb.status != "generating":
        return {"dispatched": 0}
    model = str(_meta(sb).get("generation_model") or "")
    if not model.strip():
        logger.warning("storyboard %s kick: no generation_model, skip", sb.id)
        return {"dispatched": 0}

    segs = _segments(db, sb.id)
    try:
        max_cc = int(_meta(sb).get("max_concurrent") or DEFAULT_CONCURRENT)
    except (TypeError, ValueError):
        max_cc = DEFAULT_CONCURRENT
    max_cc = max(1, min(4, max_cc))
    active = sum(1 for s in segs if s.status in _ACTIVE_SEGMENT_STATUSES)
    if active >= max_cc:
        return {"dispatched": 0}
    pendings = [s for s in segs if s.status == "pending"][: max_cc - active]
    if not pendings:
        # 对账自愈（生产 sb5 案例补丁）：全段终态却仍 generating——
        # 「重跑全片」端点无段可入队只翻状态，或终态钩子丢失 → kick 兜底翻终态
        await _finalize_storyboard(db, sb)
        return {"dispatched": 0}

    dispatched = 0
    for pending in pendings:
        # 首帧优先级（M5）：已定关键帧 > 角色定妆照(seg1) > 上段尾帧 > 无
        reference_urls: list[str] = []
        if (pending.keyframe_url or "").strip():
            reference_urls = [pending.keyframe_url]
        elif pending.segment_index == 1 and (sb.character_ref_image_url or "").strip():
            reference_urls = [sb.character_ref_image_url]
        elif pending.segment_index > 1:
            prev = next(
                (s for s in segs if s.segment_index == pending.segment_index - 1), None
            )
            if prev is not None and prev.status == "completed" and prev.last_frame_url:
                # 文件预检：尾帧已被磁盘清理时退化为 t2v，避免 i2v 必败重试烧钱
                if _tail_url_exists(prev.last_frame_url):
                    reference_urls = [prev.last_frame_url]
                else:
                    logger.warning(
                        "storyboard %s tail frame missing on disk: %s (t2v fallback)",
                        sb.id, prev.last_frame_url,
                    )

        task = create_task(
            db,
            user_id=sb.user_id,
            type_="video",
            prompt=_with_style(
                resolve_prompt_mentions(pending.prompt, _profiles(sb)), sb
            ),
            model=model,
            negative_prompt=pending.negative_prompt or "",
            params={
                "duration": (pending.target_duration_ms // 1000)
                if pending.target_duration_ms
                else sb.segment_duration,
                "resolution": sb.resolution,
                "reference_urls": reference_urls,
                "storyboard_id": sb.id,
                "segment_id": pending.id,
            },
            queue_name="video",
        )
        pending.task_id = task.id
        pending.status = "queued"
        db.commit()
        publish_storyboard_event(sb.id, "segment_update", _segment_event(pending))
        logger.info(
            "storyboard %s dispatched segment %s (task %s, refs=%s)",
            sb.id, pending.segment_index, task.id, reference_urls,
        )
        await enqueue_generation_task(task.id, "video")
        dispatched += 1
    return {"dispatched": dispatched}


def _compose_video(
    segment_urls: list[str], outputs_root: Path, *, resolution: str, bgm_url: str | None
) -> Path:
    """compose_video 转发包装（测试 monkeypatch 点）。"""
    from backend.app.services.media_service import compose_video

    return compose_video(segment_urls, outputs_root, resolution=resolution, bgm_url=bgm_url)


async def compose_storyboard(
    db: Session, storyboard_id: int, *, bgm_url: str | None = None
) -> dict[str, Any]:
    """合成成片：completed 段按序拼接 → final_video_url + metadata.compose_status。永不抛出。"""
    try:
        sb = db.get(Storyboard, storyboard_id)
        if sb is None:
            return {"ok": False, "error": "NO_STORYBOARD"}
        if sb.status == "generating":
            return {"ok": False, "error": "NOT_GENERATING"}
        segs = _segments(db, sb.id)
        done_urls = [
            s.output_video_url for s in segs if s.status == "completed" and s.output_video_url
        ]
        if not done_urls:
            return {"ok": False, "error": "NO_SEGMENTS"}
        meta = _meta(sb)
        if meta.get("compose_status") == "running":
            return {"ok": False, "error": "COMPOSE_RUNNING"}

        meta["compose_status"] = "running"
        meta.pop("compose_error", None)
        sb.metadata_json = json.dumps(meta, ensure_ascii=False)
        db.commit()
        publish_storyboard_event(
            sb.id,
            "compose_update",
            {"compose_status": "running", "segments": len(done_urls)},
        )
        logger.info("storyboard %s composing %s segments", sb.id, len(done_urls))

        out_path = await asyncio.to_thread(
            _compose_video, done_urls, Path("outputs"),
            resolution=sb.resolution or "480p", bgm_url=bgm_url,
        )
        sb.final_video_url = f"/outputs/{out_path.relative_to(Path('outputs')).as_posix()}"
        meta = _meta(sb)
        meta["compose_status"] = "done"
        meta.pop("compose_error", None)
        sb.metadata_json = json.dumps(meta, ensure_ascii=False)
        db.commit()
        publish_storyboard_event(
            sb.id,
            "compose_done",
            {"final_video_url": sb.final_video_url, "compose_status": "done"},
        )
        logger.info("storyboard %s composed → %s", sb.id, sb.final_video_url)
        return {"ok": True, "final_video_url": sb.final_video_url}
    except Exception as exc:  # noqa: BLE001 — 合成失败落状态不抛
        try:
            sb = db.get(Storyboard, storyboard_id)
            if sb is not None:
                meta = _meta(sb)
                meta["compose_status"] = "failed"
                meta["compose_error"] = str(exc)[:500]
                sb.metadata_json = json.dumps(meta, ensure_ascii=False)
                db.commit()
                publish_storyboard_event(
                    sb.id,
                    "compose_failed",
                    {"compose_status": "failed", "error": str(exc)[:200]},
                )
        except Exception:  # noqa: BLE001
            logger.exception("compose failure bookkeeping failed for %s", storyboard_id)
        logger.exception("compose_storyboard failed for %s", storyboard_id)
        return {"ok": False, "error": str(exc)[:200]}


def clear_stale_keyframe_chain(sb: Storyboard) -> bool:
    """链卡死自愈（worker cron 调用）：keyframe_chain 超时未推进 → 清 flag。返回是否清理。"""
    meta = _meta(sb)
    if meta.get("keyframe_chain") is not True:
        return False
    meta.pop("keyframe_chain", None)
    sb.metadata_json = json.dumps(meta, ensure_ascii=False)
    return True


def _tail_url_exists(url: str) -> bool:
    """尾帧文件预检（monkeypatch 点）：磁盘已清理 → False → 段退化为 t2v。"""
    return (Path("outputs") / url.removeprefix("/outputs/")).is_file()


def _extract_last_frame_url(output_url: str) -> str | None:
    """段视频末帧提取（best-effort：失败返回 None，下段退化为 t2v，管线不断）。"""
    try:
        from backend.app.services.media_service import (
            ensure_local,
            extract_last_frame,
        )

        root = Path("outputs")
        video = ensure_local(output_url, root)
        out = extract_last_frame(video, root)
        return f"/outputs/{out.relative_to(root).as_posix()}"
    except Exception:  # noqa: BLE001 — 尾帧尽力而为
        logger.warning("last frame extraction failed for %s", output_url, exc_info=True)
        return None


def _reaggregate(db: Session, sb: Storyboard) -> None:
    segs = _segments(db, sb.id)
    sb.completed_segments = sum(1 for s in segs if s.status == "completed")
    sb.failed_segments = sum(1 for s in segs if s.status == "failed")
    # None 健壮性：段指向的任务行可能已不存在（历史数据/手工清理），跳过而非崩溃
    total = 0.0
    for s in segs:
        if s.task_id and s.status == "completed":
            t = db.get(Task, s.task_id)
            if t is not None:
                total += t.cost_amount or 0.0
    sb.total_cost = total


async def _finalize_storyboard(db: Session, sb: Storyboard) -> bool:
    """全段终态 → 聚合 + 终态翻转 + done 事件 +（全成功）自动合成。

    仅 generating 生效，幂等；advance 终态钩子与 kick 对账自愈共用。
    """
    segs = _segments(db, sb.id)
    if not segs or not all(s.status in _TERMINAL_SEGMENT_STATUSES for s in segs):
        return False
    if sb.status != "generating":
        return False
    _reaggregate(db, sb)
    sb.status = "completed" if sb.failed_segments == 0 else "failed"
    sb.completed_at = datetime.utcnow()
    db.commit()
    publish_storyboard_event(
        sb.id,
        "done",
        {
            "status": sb.status,
            "completed_segments": sb.completed_segments,
            "failed_segments": sb.failed_segments,
        },
    )
    logger.info("storyboard %s finished: %s", sb.id, sb.status)
    if sb.status == "completed":
        # 一句话到成片：全片成功即自动合成（失败仅告警，不影响终态）
        try:
            await enqueue_storyboard_compose(sb.id)
        except Exception:  # noqa: BLE001
            logger.warning("auto compose enqueue failed for %s", sb.id, exc_info=True)
    return True


async def advance_on_task_terminal(db: Session, task: Task, result: dict[str, Any]) -> None:
    """段任务终态钩子：更新段/聚合分镜/发布事件/再 kick。永不抛出。"""
    try:
        params = task.params_dict()
        sid = params.get("storyboard_id")
        seg_id = params.get("segment_id")
        if not isinstance(sid, int) or not isinstance(seg_id, int):
            return
        seg = db.get(StoryboardSegment, seg_id)
        if seg is None or seg.storyboard_id != sid:
            return
        sb = db.get(Storyboard, sid)
        if sb is None:
            return

        # M5：关键帧任务分支——双轨 stale guard（比对 keyframe_task_id），零污染视频状态/计数
        if params.get("keyframe"):
            if seg.keyframe_task_id != task.id:
                logger.info(
                    "stale keyframe task %s ignored (segment now on %s)",
                    task.id, seg.keyframe_task_id,
                )
                return
            error_text = str(result.get("error") or task.error_msg or "关键帧生成失败")
            if result.get("status") == "completed":
                candidates = [str(task.output_url or "")] + [
                    str(u) for u in (params.get("extra_outputs") or []) if u
                ]
                publish_storyboard_event(
                    sb.id,
                    "segment_update",
                    {
                        "keyframe": True,
                        "segment_id": seg.id,
                        "index": seg.segment_index,
                        "keyframe_task_id": task.id,
                        "candidates": candidates,
                    },
                )
                # 0.9.4 接力链：带 chain 标记的完成 → 自动派下一段（i2i 参考=首选候选）
                if params.get("keyframe_chain"):
                    next_seg = (
                        db.query(StoryboardSegment)
                        .filter(
                            StoryboardSegment.storyboard_id == sb.id,
                            StoryboardSegment.segment_index == seg.segment_index + 1,
                            StoryboardSegment.keyframe_task_id.is_(None),
                        )
                        .first()
                    )
                    if next_seg is not None:
                        from backend.app.services.task_queue import enqueue_generation_task

                        new_task = create_task(
                            db,
                            user_id=task.user_id,
                            type_="image",
                            prompt=_with_style(
                                resolve_prompt_mentions(
                                    next_seg.prompt, _profiles(sb)
                                ),
                                sb,
                            )
                            + ", 16:9 cinematic widescreen composition",
                            model=task.model,
                            params={
                                "storyboard_id": sb.id,
                                "segment_id": next_seg.id,
                                "keyframe": True,
                                "keyframe_chain": True,
                                "image_count": int(params.get("image_count") or 4),
                                "reference_urls": [candidates[0]],
                            },
                            queue_name="generate",
                        )
                        next_seg.keyframe_task_id = new_task.id
                        db.commit()
                        publish_storyboard_event(
                            sb.id,
                            "segment_update",
                            _segment_event(next_seg, keyframe_dispatched=new_task.id),
                        )
                        logger.info(
                            "storyboard %s keyframe chain: segment %s dispatched (task %s)",
                            sb.id, next_seg.segment_index, new_task.id,
                        )
                        await enqueue_generation_task(new_task.id, "generate")
                    else:
                        # 末段完成：清链
                        meta = _meta(sb)
                        meta.pop("keyframe_chain", None)
                        sb.metadata_json = json.dumps(meta, ensure_ascii=False)
                        db.commit()
                        logger.info("storyboard %s keyframe chain finished", sb.id)
                return
            # 关键帧失败：瞬时错误自动重派一次（keyframe_retry 记账），确定性/二次失败终局
            failure_class = classify_failure(error_text)
            retry_no = int(params.get("keyframe_retry") or 0)
            if failure_class == TRANSIENT and retry_no < 1:
                from backend.app.services.task_queue import enqueue_generation_task

                new_task = create_task(
                    db,
                    user_id=task.user_id,
                    type_="image",
                    prompt=seg.prompt,
                    model=task.model,
                    params={
                        **{k: v for k, v in params.items() if k != "extra_outputs"},
                        "keyframe_retry": retry_no + 1,
                    },
                    queue_name="generate",
                )
                seg.keyframe_task_id = new_task.id
                db.commit()
                logger.info(
                    "storyboard %s segment %s keyframe failed (%s), auto redispatch task %s",
                    sb.id, seg.segment_index, failure_class, new_task.id,
                )
                await enqueue_generation_task(new_task.id, "generate")
                return
            hint = retry_hint(error_text) if failure_class == DETERMINISTIC else None
            publish_storyboard_event(
                sb.id,
                "segment_update",
                {
                    "keyframe": True,
                    "keyframe_failed": True,
                    "segment_id": seg.id,
                    "index": seg.segment_index,
                    "error": (
                        f"{error_text[:180]}｜{hint}" if hint else error_text[:200]
                    ),
                },
            )
            return

        # 陈旧任务守卫：手动重试已换新任务后，旧任务迟到终态不得污染
        if seg.task_id != task.id:
            logger.info(
                "stale storyboard task %s ignored (segment now on task %s)",
                task.id, seg.task_id,
            )
            return
        sb = db.get(Storyboard, sid)
        if sb is None:
            return

        if result.get("status") == "completed":
            seg.status = "completed"
            seg.output_video_url = str(task.output_url or "")
            seg.error_message = None
            seg.last_frame_url = _extract_last_frame_url(seg.output_video_url)
            if seg.duration_ms is None and task.duration_ms:
                seg.duration_ms = task.duration_ms
        else:
            error_text = str(result.get("error") or task.error_msg or "生成失败")
            # 0.9.1 失败分类：确定性失败（配额/文件缺失/配置错）不烧重试预算
            failure_class = classify_failure(error_text)
            if (
                sb.status == "generating"
                and seg.retry_count < MAX_AUTO_RETRY
                and failure_class == TRANSIENT
            ):
                seg.retry_count += 1
                seg.status = "pending"  # 等下次 kick 重派新任务
                seg.error_message = error_text[:500]
                logger.info(
                    "storyboard %s segment %s failed (%s), auto retry %s/%s",
                    sb.id, seg.segment_index, failure_class, seg.retry_count, MAX_AUTO_RETRY,
                )
            else:
                seg.status = "failed"
                hint = retry_hint(error_text) if failure_class == DETERMINISTIC else None
                seg.error_message = f"{error_text[:400]}｜{hint}" if hint else error_text[:500]

        db.commit()
        publish_storyboard_event(sb.id, "segment_update", _segment_event(seg))

        _reaggregate(db, sb)
        finalized = await _finalize_storyboard(db, sb)
        if not finalized and sb.status == "generating":
            db.commit()
            publish_storyboard_event(
                sb.id,
                "storyboard_update",
                {
                    "status": sb.status,
                    "completed_segments": sb.completed_segments,
                    "failed_segments": sb.failed_segments,
                },
            )
            await enqueue_storyboard_kick(sb.id)
    except Exception:  # noqa: BLE001 — 编排推进永不影响任务主流程
        db.rollback()
        logger.exception("advance_on_task_terminal failed for task %s", task.id)
