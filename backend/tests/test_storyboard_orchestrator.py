"""分镜编排器契约：kick 派发 / 终态推进 / 尾帧接力 / 自动重试 / 聚合（M2 核心）。"""

import asyncio
import json

from backend.app.models.storyboard import Storyboard, StoryboardSegment
from backend.app.services import storyboard_orchestrator as orch
from backend.app.services.task_store import create_task


def _mk_storyboard(db, *, n_segments=3, status="generating", model="fake-video"):
    sb = Storyboard(
        user_id=1,
        title="测试片",
        short_description="一只橘猫的都市冒险",
        segments_count=n_segments,
        segment_duration=6,
        status=status,
        metadata_json=json.dumps({"generation_model": model}),
    )
    db.add(sb)
    db.flush()
    for i in range(1, n_segments + 1):
        db.add(
            StoryboardSegment(
                storyboard_id=sb.id,
                segment_index=i,
                prompt=f"第 {i} 段画面",
            )
        )
    db.commit()
    return sb


def _segments(db, sid):
    return (
        db.query(StoryboardSegment)
        .filter(StoryboardSegment.storyboard_id == sid)
        .order_by(StoryboardSegment.segment_index)
        .all()
    )


def _completed_task(db, seg, *, output_url="/outputs/videos/seg.mp4", cost=0.5):
    task = create_task(
        db, user_id=1, type_="video", prompt=seg.prompt, model="fake-video",
        params={"storyboard_id": seg.storyboard_id, "segment_id": seg.id},
        queue_name="video",
    )
    task.cost_amount = cost
    db.commit()
    return task


def test_kick_dispatches_first_segment(db_session, fake_arq_pool):
    sb = _mk_storyboard(db_session)

    out = asyncio.run(orch.kick_storyboard(db_session, sb.id))

    assert out == {"dispatched": 1}
    segs = _segments(db_session, sb.id)
    assert segs[0].status == "queued" and segs[0].task_id is not None
    # 后续段不被派发（容量=1）
    assert segs[1].status == "pending" and segs[1].task_id is None
    # 视频任务落库：queue=video，params 带 storyboard/segment 关联
    from backend.app.models import Task

    task = db_session.get(Task, segs[0].task_id)
    assert task.type == "video" and task.queue_name == "video"
    params = task.params_dict()
    assert params["storyboard_id"] == sb.id and params["segment_id"] == segs[0].id
    assert params["reference_urls"] == []
    assert params["duration"] == 6
    # 真正的执行 job 入 video 队列
    assert fake_arq_pool[0][0] == "run_generation_task"
    assert fake_arq_pool[0][2]["_queue_name"] == "video"


def test_kick_respects_capacity(db_session, fake_arq_pool):
    sb = _mk_storyboard(db_session)
    asyncio.run(orch.kick_storyboard(db_session, sb.id))
    before = len(fake_arq_pool)

    out = asyncio.run(orch.kick_storyboard(db_session, sb.id))

    assert out == {"dispatched": 0}
    assert len(fake_arq_pool) == before  # 无新增派发


def test_kick_skips_when_not_generating(db_session, fake_arq_pool):
    sb = _mk_storyboard(db_session, status="draft")

    out = asyncio.run(orch.kick_storyboard(db_session, sb.id))

    assert out == {"dispatched": 0}
    assert fake_arq_pool == []


def test_tail_frame_relay(db_session, fake_arq_pool, monkeypatch):
    sb = _mk_storyboard(db_session, n_segments=2)
    monkeypatch.setattr(
        orch, "_extract_last_frame_url", lambda url: "/outputs/media/frame_tail.png"
    )
    monkeypatch.setattr(orch, "_tail_url_exists", lambda url: True)
    seg1, seg2 = _segments(db_session, sb.id)
    task1 = _completed_task(db_session, seg1)
    seg1.status, seg1.task_id = "queued", task1.id
    db_session.commit()

    asyncio.run(orch.advance_on_task_terminal(db_session, task1, {"status": "completed"}))

    db_session.refresh(seg1)
    assert seg1.status == "completed"
    assert seg1.last_frame_url == "/outputs/media/frame_tail.png"
    # 尾帧接力：kick 再入队 → 段2 派发时带 reference_urls[0]=段1尾帧
    assert any(j[0] == "run_storyboard_kick" for j in fake_arq_pool)
    asyncio.run(orch.kick_storyboard(db_session, sb.id))
    from backend.app.models import Task

    seg2 = _segments(db_session, sb.id)[1]
    task2 = db_session.get(Task, seg2.task_id)
    assert task2.params_dict()["reference_urls"] == ["/outputs/media/frame_tail.png"]


def test_advance_updates_segment_and_aggregates(db_session, fake_arq_pool, monkeypatch):
    sb = _mk_storyboard(db_session, n_segments=3)
    monkeypatch.setattr(orch, "_extract_last_frame_url", lambda url: None)
    segs = _segments(db_session, sb.id)
    tasks = []
    for seg in segs:
        t = _completed_task(db_session, seg, cost=0.5)
        seg.status, seg.task_id = "queued", t.id
        tasks.append(t)
    db_session.commit()

    for t in tasks:
        asyncio.run(orch.advance_on_task_terminal(db_session, t, {"status": "completed"}))

    db_session.refresh(sb)
    segs = _segments(db_session, sb.id)
    assert all(s.status == "completed" for s in segs)
    assert sb.completed_segments == 3
    assert sb.failed_segments == 0
    assert sb.total_cost == 1.5
    assert sb.status == "completed"
    assert sb.completed_at is not None
    assert any(j[0] == "run_storyboard_kick" for j in fake_arq_pool)


def test_advance_auto_retry_then_terminal_failure(db_session, fake_arq_pool, monkeypatch):
    monkeypatch.setattr(orch, "_extract_last_frame_url", lambda url: None)
    sb = _mk_storyboard(db_session, n_segments=2)
    seg1, seg2 = _segments(db_session, sb.id)
    task = _completed_task(db_session, seg1)
    seg1.status, seg1.task_id = "queued", task.id
    db_session.commit()

    # 第 1 败（瞬时）：回 pending，等重派
    asyncio.run(
        orch.advance_on_task_terminal(
            db_session, task, {"status": "failed", "error": "本地 H3 服务错误 HTTP 500"}
        )
    )
    db_session.refresh(seg1)
    assert seg1.status == "pending"
    assert seg1.retry_count == 1
    assert any(j[0] == "run_storyboard_kick" for j in fake_arq_pool)

    # 重派（新任务）后再败：仍回 pending
    fake_arq_pool.clear()
    asyncio.run(orch.kick_storyboard(db_session, sb.id))
    from backend.app.models import Task

    db_session.refresh(seg1)
    task2 = db_session.get(Task, seg1.task_id)
    assert task2.id != task.id  # 新任务
    asyncio.run(
        orch.advance_on_task_terminal(
            db_session, task2, {"status": "failed", "error": "本地 H3 服务错误 HTTP 500"}
        )
    )
    db_session.refresh(seg1)
    assert seg1.status == "pending" and seg1.retry_count == 2

    # 第 3 败：终局 failed；下一段照常派发且无 reference
    fake_arq_pool.clear()
    asyncio.run(orch.kick_storyboard(db_session, sb.id))
    db_session.refresh(seg1)
    task3 = db_session.get(Task, seg1.task_id)
    asyncio.run(
        orch.advance_on_task_terminal(db_session, task3, {"status": "failed", "error": "boom"})
    )
    db_session.refresh(seg1)
    assert seg1.status == "failed"
    assert (seg1.error_message or "").startswith("boom")  # 确定性提示已追加
    asyncio.run(orch.kick_storyboard(db_session, sb.id))
    seg2 = _segments(db_session, sb.id)[1]
    assert seg2.status == "queued"


def test_stale_task_ignored(db_session, fake_arq_pool):
    sb = _mk_storyboard(db_session, n_segments=1)
    seg = _segments(db_session, sb.id)[0]
    stale = _completed_task(db_session, seg)
    seg.status, seg.task_id = "queued", 99999  # 手动重试已换新任务
    db_session.commit()

    asyncio.run(
        orch.advance_on_task_terminal(db_session, stale, {"status": "completed"})
    )

    db_session.refresh(seg)
    assert seg.status == "queued"  # 未被迟到终态污染
    assert seg.task_id == 99999


def test_extract_failure_tolerated(db_session, fake_arq_pool, monkeypatch):
    sb = _mk_storyboard(db_session, n_segments=1)
    seg = _segments(db_session, sb.id)[0]
    task = _completed_task(db_session, seg)
    seg.status, seg.task_id = "queued", task.id
    db_session.commit()

    from backend.app.services import media_service

    def _boom(url, root=None):
        raise RuntimeError("ffmpeg missing")

    monkeypatch.setattr(media_service, "ensure_local", _boom)
    asyncio.run(orch.advance_on_task_terminal(db_session, task, {"status": "completed"}))

    db_session.refresh(seg)
    assert seg.status == "completed"  # 提取失败不影响段完成
    assert seg.last_frame_url is None


def test_kick_without_generation_model_no_dispatch(db_session, fake_arq_pool):
    sb = _mk_storyboard(db_session)
    sb.metadata_json = json.dumps({})
    db_session.commit()

    out = asyncio.run(orch.kick_storyboard(db_session, sb.id))

    assert out == {"dispatched": 0}
    assert fake_arq_pool == []


# ---- M3: 成片合成 ----


def _finish_all(db, sb, *, fail_last=False):
    """把全部分段推进到终态（模拟生成完成），分镜级状态同步收敛。"""
    segs = _segments(db, sb.id)
    for i, seg in enumerate(segs):
        task = _completed_task(db, seg, output_url=f"/outputs/videos/seg{i+1}.mp4")
        seg.status = "failed" if (fail_last and i == len(segs) - 1) else "completed"
        seg.task_id = task.id
        if seg.status == "completed":
            seg.output_video_url = f"/outputs/videos/seg{i+1}.mp4"
        if fail_last and i == len(segs) - 1:
            seg.error_message = "boom"
    sb.status = "failed" if fail_last else "completed"
    db.commit()


def test_compose_happy_path(db_session, fake_arq_pool, monkeypatch):
    sb = _mk_storyboard(db_session, n_segments=2)
    _finish_all(db_session, sb)
    composed: dict = {}

    def _fake_compose(urls, root, *, resolution="480p", bgm_url=None, bgm_volume=0.25):
        composed["urls"] = list(urls)
        composed["resolution"] = resolution
        return root / "videos" / "compose_test.mp4"

    monkeypatch.setattr(orch, "_compose_video", _fake_compose)
    out = asyncio.run(orch.compose_storyboard(db_session, sb.id))

    assert out["ok"] is True
    db_session.refresh(sb)
    assert sb.final_video_url == "/outputs/videos/compose_test.mp4"
    assert orch._meta(sb)["compose_status"] == "done"
    # 只传 completed 段且按序
    assert composed["urls"] == ["/outputs/videos/seg1.mp4", "/outputs/videos/seg2.mp4"]
    assert composed["resolution"] == "480p"


def test_compose_skips_failed_segments(db_session, fake_arq_pool, monkeypatch):
    sb = _mk_storyboard(db_session, n_segments=3)
    _finish_all(db_session, sb, fail_last=True)
    composed: dict = {}

    monkeypatch.setattr(
        orch, "_compose_video",
        lambda urls, root, **kw: (composed.update(urls=list(urls)), root / "videos" / "c.mp4")[1],
    )
    out = asyncio.run(orch.compose_storyboard(db_session, sb.id))

    assert out["ok"] is True
    assert composed["urls"] == ["/outputs/videos/seg1.mp4", "/outputs/videos/seg2.mp4"]


def test_compose_guards(db_session, fake_arq_pool, monkeypatch):
    # 不存在
    out = asyncio.run(orch.compose_storyboard(db_session, 424242))
    assert out == {"ok": False, "error": "NO_STORYBOARD"}

    # generating 中
    sb = _mk_storyboard(db_session, n_segments=1)
    out = asyncio.run(orch.compose_storyboard(db_session, sb.id))
    assert out == {"ok": False, "error": "NOT_GENERATING"}

    # 0 completed 段
    sb2 = _mk_storyboard(db_session, n_segments=2, status="failed")
    out = asyncio.run(orch.compose_storyboard(db_session, sb2.id))
    assert out == {"ok": False, "error": "NO_SEGMENTS"}


def test_compose_running_guard(db_session, fake_arq_pool, monkeypatch):
    import json as _json

    sb = _mk_storyboard(db_session, n_segments=1)
    _finish_all(db_session, sb)
    sb.metadata_json = _json.dumps({**orch._meta(sb), "compose_status": "running"})
    db_session.commit()

    out = asyncio.run(orch.compose_storyboard(db_session, sb.id))

    assert out == {"ok": False, "error": "COMPOSE_RUNNING"}


def test_compose_failure_path(db_session, fake_arq_pool, monkeypatch):
    sb = _mk_storyboard(db_session, n_segments=1)
    _finish_all(db_session, sb)

    def _boom(*a, **kw):
        raise RuntimeError("ffmpeg exploded")

    monkeypatch.setattr(orch, "_compose_video", _boom)
    out = asyncio.run(orch.compose_storyboard(db_session, sb.id))

    assert out["ok"] is False
    db_session.refresh(sb)
    assert orch._meta(sb)["compose_status"] == "failed"
    assert "ffmpeg exploded" in orch._meta(sb)["compose_error"]
    assert sb.final_video_url is None


# ---- M4: 角色参考图首段锚点 ----


def test_kick_injects_character_ref_on_first_segment(db_session, fake_arq_pool, monkeypatch):
    """角色图仅注入第 1 段；第 2 段仍走尾帧接力（不注入角色图）。"""
    sb = _mk_storyboard(db_session, n_segments=2)
    sb.character_ref_image_url = "/outputs/uploads/ref.png"
    db_session.commit()
    monkeypatch.setattr(orch, "_extract_last_frame_url", lambda url: "/outputs/media/tail1.png")
    monkeypatch.setattr(orch, "_tail_url_exists", lambda url: True)

    asyncio.run(orch.kick_storyboard(db_session, sb.id))
    seg1 = _segments(db_session, sb.id)[0]
    from backend.app.models import Task

    task1 = db_session.get(Task, seg1.task_id)
    assert task1.params_dict()["reference_urls"] == ["/outputs/uploads/ref.png"]

    seg1.status = "completed"
    seg1.last_frame_url = "/outputs/media/tail1.png"
    db_session.commit()
    task_obj = db_session.get(Task, seg1.task_id)
    asyncio.run(orch.advance_on_task_terminal(db_session, task_obj, {"status": "completed"}))
    asyncio.run(orch.kick_storyboard(db_session, sb.id))

    seg2 = _segments(db_session, sb.id)[1]
    task2 = db_session.get(Task, seg2.task_id)
    assert task2.params_dict()["reference_urls"] == ["/outputs/media/tail1.png"]


def test_kick_no_ref_regression(db_session, fake_arq_pool):
    """无角色图时段1 reference_urls==[]（既有行为回归）。"""
    sb = _mk_storyboard(db_session, n_segments=1)
    asyncio.run(orch.kick_storyboard(db_session, sb.id))
    from backend.app.models import Task

    seg1 = _segments(db_session, sb.id)[0]
    task1 = db_session.get(Task, seg1.task_id)
    assert task1.params_dict()["reference_urls"] == []


def test_kick_per_segment_duration(db_session, fake_arq_pool, monkeypatch):
    """段级 target_duration_ms 优先；无 target 段回落 sb.segment_duration。"""
    monkeypatch.setattr(orch, "_extract_last_frame_url", lambda url: None)
    sb = _mk_storyboard(db_session, n_segments=2)
    segs = _segments(db_session, sb.id)
    segs[0].target_duration_ms = 10000
    db_session.commit()

    asyncio.run(orch.kick_storyboard(db_session, sb.id))
    from backend.app.models import Task

    seg1 = _segments(db_session, sb.id)[0]
    assert db_session.get(Task, seg1.task_id).params_dict()["duration"] == 10

    # 段1 完成 → 段2 派发：未设 target → 回落 6
    task1 = db_session.get(Task, seg1.task_id)
    seg1.status = "completed"
    db_session.commit()
    asyncio.run(orch.advance_on_task_terminal(db_session, task1, {"status": "completed"}))
    asyncio.run(orch.kick_storyboard(db_session, sb.id))
    seg2 = _segments(db_session, sb.id)[1]
    assert db_session.get(Task, seg2.task_id).params_dict()["duration"] == 6


def test_kick_tail_frame_file_missing_falls_back_t2v(db_session, fake_arq_pool, monkeypatch):
    """尾帧 URL 记录在但文件已被清理 → 不注入引用，退化为 t2v（防必败重试烧钱）。"""
    sb = _mk_storyboard(db_session, n_segments=2)
    segs = _segments(db_session, sb.id)
    segs[0].status = "completed"
    segs[0].output_video_url = "/outputs/videos/gone.mp4"
    segs[0].last_frame_url = "/outputs/media/tail_gone.png"  # 文件不存在
    db_session.commit()

    asyncio.run(orch.kick_storyboard(db_session, sb.id))
    from backend.app.models import Task

    seg2 = _segments(db_session, sb.id)[1]
    task2 = db_session.get(Task, seg2.task_id)
    assert task2.params_dict()["reference_urls"] == []


# ---- M5: 关键帧终态钩子 ----


def _mk_keyframe_task(
    db, seg, *, status="completed", output="/outputs/images/kf.png", extras=None, chain=False
):
    task = create_task(
        db, user_id=1, type_="image", prompt=seg.prompt, model="fake-image",
        params={
            "storyboard_id": seg.storyboard_id, "segment_id": seg.id,
            "keyframe": True, "keyframe_chain": chain, "image_count": 4,
            "extra_outputs": extras or [],
        },
        queue_name="generate",
    )
    if status == "completed":
        task.mark_completed(output)
    else:
        task.mark_failed("boom")
    seg.keyframe_task_id = task.id
    db.commit()
    return task


def test_advance_keyframe_publishes_candidates(db_session, fake_arq_pool, monkeypatch):
    """关键帧完成：发候选事件；段视频状态/计数零污染。"""
    published: list[tuple[int, str, dict]] = []
    monkeypatch.setattr(
        orch, "publish_storyboard_event",
        lambda sid, t, data: published.append((sid, t, data)),
    )
    sb = _mk_storyboard(db_session, n_segments=2)
    sb.status = "completed"  # 任意非 generating 状态
    db_session.commit()
    seg = _segments(db_session, sb.id)[0]
    task = _mk_keyframe_task(db_session, seg, extras=["/outputs/images/kf2.png"])

    asyncio.run(orch.advance_on_task_terminal(db_session, task, {"status": "completed"}))

    assert published and published[0][1] == "segment_update"
    assert published[0][2]["keyframe"] is True
    assert published[0][2]["candidates"] == ["/outputs/images/kf.png", "/outputs/images/kf2.png"]

    db_session.refresh(seg)
    assert seg.status == "pending"  # 视频状态零污染
    db_session.refresh(sb)
    assert sb.completed_segments == 0  # 计数零污染
    assert sb.status == "completed"  # 不流转
    assert fake_arq_pool == []  # 关键帧完成不 kick


def test_advance_keyframe_stale_ignored(db_session, fake_arq_pool):
    """keyframe_task_id 不匹配 → 迟到终态忽略。"""
    sb = _mk_storyboard(db_session, n_segments=1)
    seg = _segments(db_session, sb.id)[0]
    task = _mk_keyframe_task(db_session, seg)
    seg.keyframe_task_id = 99999  # 已换新关键帧任务
    db_session.commit()

    asyncio.run(orch.advance_on_task_terminal(db_session, task, {"status": "completed"}))

    db_session.refresh(seg)
    assert seg.keyframe_task_id == 99999


def test_advance_video_task_still_uses_task_id_guard(db_session, fake_arq_pool):
    """回归：视频任务双轨互不干扰——keyframe_task_id 存在时视频终态仍按 task_id 判定。"""
    sb = _mk_storyboard(db_session, n_segments=1)
    sb.status = "generating"
    db_session.commit()
    seg = _segments(db_session, sb.id)[0]
    vtask = _completed_task(db_session, seg)
    seg.status, seg.task_id = "queued", vtask.id
    ktask = _mk_keyframe_task(db_session, seg)
    db_session.commit()

    asyncio.run(orch.advance_on_task_terminal(db_session, vtask, {"status": "completed"}))

    db_session.refresh(seg)
    assert seg.status == "completed"  # 视频终态正常生效
    assert seg.keyframe_task_id == ktask.id  # 关键帧字段不被视频终态触碰


# ---- M5: kick 首帧优先级（定帧 > 角色图 > 尾帧） ----


def test_kick_keyframe_beats_all(db_session, fake_arq_pool, monkeypatch):
    monkeypatch.setattr(orch, "_extract_last_frame_url", lambda url: None)
    monkeypatch.setattr(orch, "_tail_url_exists", lambda url: True)
    sb = _mk_storyboard(db_session, n_segments=2)
    sb.character_ref_image_url = "/outputs/uploads/ref.png"
    db_session.commit()
    segs = _segments(db_session, sb.id)
    segs[0].keyframe_url = "/outputs/images/chosen.png"
    segs[1].keyframe_url = "/outputs/images/chosen2.png"
    db_session.commit()

    # seg1：定帧优先于角色图
    asyncio.run(orch.kick_storyboard(db_session, sb.id))
    from backend.app.models import Task

    seg1 = _segments(db_session, sb.id)[0]
    assert db_session.get(Task, seg1.task_id).params_dict()["reference_urls"] == [
        "/outputs/images/chosen.png"
    ]

    # seg2：定帧优先于尾帧
    seg1.status = "completed"
    seg1.last_frame_url = "/outputs/media/tail1.png"
    db_session.commit()
    task1 = db_session.get(Task, seg1.task_id)
    asyncio.run(orch.advance_on_task_terminal(db_session, task1, {"status": "completed"}))
    asyncio.run(orch.kick_storyboard(db_session, sb.id))
    seg2 = _segments(db_session, sb.id)[1]
    assert db_session.get(Task, seg2.task_id).params_dict()["reference_urls"] == [
        "/outputs/images/chosen2.png"
    ]


# ---- 0.9.1: 失败分类重试 ----


def test_deterministic_failure_no_auto_retry(db_session, fake_arq_pool, monkeypatch):
    """确定性失败（配额耗尽）第一次就终局，不烧重试预算。"""
    sb = _mk_storyboard(db_session, n_segments=1)
    sb.status = "generating"
    db_session.commit()
    seg = _segments(db_session, sb.id)[0]
    task = _completed_task(db_session, seg)
    seg.status, seg.task_id = "queued", task.id
    db_session.commit()

    asyncio.run(
        orch.advance_on_task_terminal(
            db_session, task,
            {"status": "failed", "error": "当前已达到 Token Plan 用量上限。请升级"},
        )
    )

    db_session.refresh(seg)
    assert seg.status == "failed"  # 直接终局
    assert seg.retry_count == 0  # 未烧预算
    assert "配额" in (seg.error_message or "")  # 可行动提示已追加
    assert fake_arq_pool == []  # 不再 kick


def test_transient_failure_still_retries(db_session, fake_arq_pool, monkeypatch):
    """瞬时失败（H3 500）保持既有自动重试。"""
    sb = _mk_storyboard(db_session, n_segments=1)
    sb.status = "generating"
    db_session.commit()
    seg = _segments(db_session, sb.id)[0]
    task = _completed_task(db_session, seg)
    seg.status, seg.task_id = "queued", task.id
    db_session.commit()

    asyncio.run(
        orch.advance_on_task_terminal(
            db_session, task, {"status": "failed", "error": "本地 H3 服务错误 HTTP 500"}
        )
    )

    db_session.refresh(seg)
    assert seg.status == "pending"  # 回 pending 等重派
    assert seg.retry_count == 1


def test_keyframe_transient_failure_auto_retries_once(db_session, fake_arq_pool, monkeypatch):
    """关键帧瞬时失败自动重派一次（keyframe_retry 记账），第二次失败终局。"""
    sb = _mk_storyboard(db_session, n_segments=1)
    sb.status = "completed"
    db_session.commit()
    seg = _segments(db_session, sb.id)[0]
    task = _mk_keyframe_task(db_session, seg)

    # 瞬时失败 → 自动重派新关键帧任务（带 keyframe_retry=1）
    asyncio.run(
        orch.advance_on_task_terminal(
            db_session, task, {"status": "failed", "error": "OpenAI 兼容渠道 Error: 429"}
        )
    )
    db_session.refresh(seg)
    assert seg.keyframe_task_id != task.id  # 新任务
    from backend.app.models import Task

    new_task = db_session.get(Task, seg.keyframe_task_id)
    assert new_task.params_dict().get("keyframe_retry") == 1

    # 第二次瞬时失败 → 终局（不再重派）
    new_task.mark_failed("OpenAI 兼容渠道 Error: 429")
    db_session.commit()
    asyncio.run(
        orch.advance_on_task_terminal(db_session, new_task, {"status": "failed", "error": "429"})
    )
    db_session.refresh(seg)
    assert seg.keyframe_task_id == new_task.id  # 未再重派


def test_keyframe_deterministic_failure_terminal(db_session, fake_arq_pool):
    """关键帧确定性失败（文件缺失）直接终局不重派。"""
    sb = _mk_storyboard(db_session, n_segments=1)
    sb.status = "completed"
    db_session.commit()
    seg = _segments(db_session, sb.id)[0]
    task = _mk_keyframe_task(db_session, seg)

    asyncio.run(
        orch.advance_on_task_terminal(
            db_session, task,
            {"status": "failed", "error": "首帧图获取失败: FileNotFoundError"},
        )
    )

    db_session.refresh(seg)
    assert seg.keyframe_task_id == task.id  # 未重派


# ---- 0.9.3: @角色名引用解析 ----


def test_resolve_prompt_mentions_expands():
    from backend.app.services.storyboard_orchestrator import resolve_prompt_mentions

    profiles = [{"name": "阿珍", "description": "40yo woman in apron"}]
    text = "@阿珍 ladles noodles, steam rising, close-up"
    out = resolve_prompt_mentions(text, profiles)
    assert out == "阿珍 (40yo woman in apron) ladles noodles, steam rising, close-up"


def test_resolve_prompt_mentions_no_mention_unchanged():
    from backend.app.services.storyboard_orchestrator import resolve_prompt_mentions

    profiles = [{"name": "阿珍", "description": "40yo"}]
    text = "rain falls on the alley, slow push-in"
    assert resolve_prompt_mentions(text, profiles) == text


def test_resolve_prompt_mentions_no_description_strips_at():
    from backend.app.services.storyboard_orchestrator import resolve_prompt_mentions

    profiles = [{"name": "阿珍"}]
    out = resolve_prompt_mentions("@阿珍 smiles", profiles)
    assert out == "阿珍 smiles"


def test_kick_enriches_prompt_with_mention(db_session, fake_arq_pool):
    sb = _mk_storyboard(db_session, n_segments=1)
    import json as _json

    sb.character_profiles = _json.dumps(
        [{"name": "阿珍", "description": "40yo woman in apron"}]
    )
    db_session.commit()
    seg = _segments(db_session, sb.id)[0]
    seg.prompt = "@阿珍 ladles noodles at midnight, steam, warm light"
    db_session.commit()

    asyncio.run(orch.kick_storyboard(db_session, sb.id))
    from backend.app.models import Task

    task = db_session.get(Task, seg.task_id)
    assert "阿珍 (40yo woman in apron)" in task.prompt
    assert "@阿珍" not in task.prompt


def test_keyframes_enrich_prompt_with_mention(client, db_session, fake_arq_pool):
    from backend.app.models import Task

    h = None
    from backend.tests.test_characters import _auth

    h = _auth(client, "char_kf_enrich")
    sid = client.post(
        "/api/storyboards", json={"short_description": "面馆故事"}, headers=h
    ).json()["storyboard"]["id"]
    c1 = client.post(
        "/api/characters",
        json={"name": "阿珍", "description": "40yo woman in apron"},
        headers=h,
    ).json()["character"]
    client.post(
        f"/api/storyboards/{sid}/characters/attach",
        json={"character_ids": [c1["id"]]},
        headers=h,
    )
    # 分镜段 prompt 引用角色
    client.put(
        f"/api/storyboards/{sid}/segments",
        json={"segments": [{"prompt": "@阿珍 closes the shop, rain outside"}]},
        headers=h,
    )

    r = client.post(f"/api/storyboards/{sid}/keyframes", json={}, headers=h)
    assert r.status_code == 200, r.text
    task_id = r.json()["tasks"][0]["task_id"]
    task = db_session.get(Task, task_id)
    assert "阿珍 (40yo woman in apron)" in task.prompt


# ---- 0.9.4-T3: 关键帧接力链 ----


def test_keyframe_chain_dispatches_first_only(client, db_session, fake_arq_pool):
    from backend.tests.test_characters import _auth

    headers = _auth(client, "sb_m6_chain_a")
    sid = client.post(
        "/api/storyboards",
        json={"short_description": "chain test", "segments_count": 3},
        headers=headers,
    ).json()["storyboard"]["id"]
    client.put(
        f"/api/storyboards/{sid}/segments",
        json={"segments": [{"prompt": f"s{i}"} for i in range(1, 4)]},
        headers=headers,
    )
    r = client.post(
        f"/api/storyboards/{sid}/keyframes", json={"chain": True}, headers=headers
    )
    assert r.status_code == 200, r.text
    # 只派段 1
    from backend.app.models.storyboard import StoryboardSegment

    segs = (
        db_session.query(StoryboardSegment)
        .filter(StoryboardSegment.storyboard_id == sid)
        .order_by(StoryboardSegment.segment_index)
        .all()
    )
    assert segs[0].keyframe_task_id is not None
    assert segs[1].keyframe_task_id is None and segs[2].keyframe_task_id is None
    sb = client.get(f"/api/storyboards/{sid}", headers=headers).json()["storyboard"]
    assert sb["metadata"]["keyframe_chain"] is True


def test_keyframe_chain_relay_on_complete(db_session, fake_arq_pool, monkeypatch):
    """段 1 关键帧完成 → 自动派段 2（reference=段 1 首选候选）。"""
    sb = _mk_storyboard(db_session, n_segments=2)
    sb.status = "completed"
    sb.metadata_json = json.dumps({**orch._meta(sb), "keyframe_chain": True})
    db_session.commit()
    segs = _segments(db_session, sb.id)
    task1 = _mk_keyframe_task(db_session, segs[0], output="/outputs/images/c1.png",
                              extras=["/outputs/images/c1b.png"], chain=True)
    monkeypatch.setattr(orch, "_tail_url_exists", lambda url: True)

    asyncio.run(orch.advance_on_task_terminal(db_session, task1, {"status": "completed"}))

    segs = _segments(db_session, sb.id)
    assert segs[1].keyframe_task_id is not None  # 段 2 被自动派发
    from backend.app.models import Task

    task2 = db_session.get(Task, segs[1].keyframe_task_id)
    assert task2.params_dict()["reference_urls"] == ["/outputs/images/c1.png"]  # i2i 接力
    assert task2.params_dict().get("keyframe_chain") is True


def test_keyframe_chain_manual_task_no_relay(db_session, fake_arq_pool):
    """手动（无链标记）关键帧完成 → 不推进链。"""
    sb = _mk_storyboard(db_session, n_segments=2)
    sb.status = "completed"
    db_session.commit()
    segs = _segments(db_session, sb.id)
    task1 = _mk_keyframe_task(db_session, segs[0])  # 无 chain 标记

    asyncio.run(orch.advance_on_task_terminal(db_session, task1, {"status": "completed"}))

    segs = _segments(db_session, sb.id)
    assert segs[1].keyframe_task_id is None  # 不自动推进


def test_keyframe_chain_completes_clears_flag(db_session, fake_arq_pool):
    """末段关键帧完成 → 清 keyframe_chain。"""
    sb = _mk_storyboard(db_session, n_segments=2)
    sb.status = "completed"
    sb.metadata_json = json.dumps({**orch._meta(sb), "keyframe_chain": True})
    db_session.commit()
    segs = _segments(db_session, sb.id)
    for i, seg in enumerate(segs):
        seg.status = "completed"
        seg.output_video_url = f"/outputs/videos/p{i+1}.mp4"
    task2 = _mk_keyframe_task(db_session, segs[1], output="/outputs/images/c2.png",
                              extras=[], chain=True)
    # 段 1 已有候选（模拟链中已完成）
    _mk_keyframe_task(db_session, segs[0], output="/outputs/images/c1.png")
    db_session.commit()

    asyncio.run(orch.advance_on_task_terminal(db_session, task2, {"status": "completed"}))

    db_session.refresh(sb)
    assert orch._meta(sb).get("keyframe_chain") is None  # 链完成清 flag


# ---- 0.10: 按引擎并发 ----


def test_kick_respects_metadata_concurrency(db_session, fake_arq_pool):
    """metadata.max_concurrent=2 → 同 kick 双段并行派发（并行段无尾帧=预期）。"""
    sb = _mk_storyboard(db_session, n_segments=2)
    sb.metadata_json = json.dumps(
        {**orch._meta(sb), "generation_model": "fake-video", "max_concurrent": 2}
    )
    db_session.commit()

    out = asyncio.run(orch.kick_storyboard(db_session, sb.id))
    assert out == {"dispatched": 2}
    segs = _segments(db_session, sb.id)
    assert segs[0].status == "queued" and segs[1].status == "queued"
    from backend.app.models import Task

    # 并行段无前段尾帧（前段未完成）——reference 为空是预期
    assert db_session.get(Task, segs[0].task_id).params_dict()["reference_urls"] == []
    assert db_session.get(Task, segs[1].task_id).params_dict()["reference_urls"] == []


def test_kick_default_concurrency_regression(db_session, fake_arq_pool):
    """无 metadata.max_concurrent → 默认 1（M2 行为回归）。"""
    sb = _mk_storyboard(db_session, n_segments=2)
    out = asyncio.run(orch.kick_storyboard(db_session, sb.id))
    assert out == {"dispatched": 1}


def test_kick_reconciles_stuck_generating_all_terminal(db_session, fake_arq_pool):
    """P0 自愈（生产 sb5 案例）：generating 且全段 completed——「重跑全片」端点无段可入队
    只翻了状态、没有任何终态钩子会来翻转 → kick 必须兜底翻终态+自动合成。"""
    sb = _mk_storyboard(db_session, n_segments=2)
    for s in _segments(db_session, sb.id):
        t = _completed_task(db_session, s)
        s.status = "completed"
        s.task_id = t.id
        s.output_video_url = "/outputs/videos/seg.mp4"
    db_session.commit()

    out = asyncio.run(orch.kick_storyboard(db_session, sb.id))

    assert out == {"dispatched": 0}
    db_session.expire_all()
    healed = db_session.get(Storyboard, sb.id)
    assert healed.status == "completed"
    assert healed.completed_at is not None
    assert any(j[0] == "run_storyboard_compose" for j in fake_arq_pool)


def test_reaggregate_survives_missing_task(db_session, fake_arq_pool):
    """P3 健壮性：段指向不存在的 task_id（数据修复/手工清理后）不得让终态钩子整体崩掉。"""
    sb = _mk_storyboard(db_session, n_segments=2)
    for s in _segments(db_session, sb.id):
        t = _completed_task(db_session, s)
        s.status = "completed"
        s.task_id = t.id
        s.output_video_url = "/outputs/videos/seg.mp4"
    # 段 2 的任务引用换成不存在的 id（模拟任务行丢失）
    segs = _segments(db_session, sb.id)
    segs[1].task_id = 999999
    db_session.commit()

    out = asyncio.run(orch.kick_storyboard(db_session, sb.id))

    assert out == {"dispatched": 0}
    db_session.expire_all()
    healed = db_session.get(Storyboard, sb.id)
    assert healed.status == "completed"  # 不因 None.cost_amount 崩溃回滚
    assert healed.total_cost is not None
