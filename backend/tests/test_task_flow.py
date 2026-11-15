"""任务闭环契约：提交→执行→终态 / 队列不可用 / SSE（含 Review Focus #4）。"""

import asyncio

REGISTER_OK = {
    "username": "bob",
    "email": "bob@example.com",
    "password": "password123",
    "invite_code": "letmein",
}


def _auth_headers(client) -> dict[str, str]:
    r = client.post("/api/auth/register", json=REGISTER_OK)
    if r.status_code != 200:
        raise AssertionError(f"register failed: {r.status_code} {r.text[:300]}")
    token = r.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _submit(
    client, headers, type_="image", model="fake-image", prompt="a test prompt", params=None
):
    return client.post(
        "/api/tasks",
        json={"type": type_, "prompt": prompt, "model": model, "params": params},
        headers=headers,
    )


def test_submit_creates_queued_task(client):
    headers = _auth_headers(client)
    r = _submit(client, headers)
    assert r.status_code == 200, r.text
    assert r.json()["task"]["status"] == "queued"
    assert r.json()["task"]["type"] == "image"


def test_invalid_type_rejected(client):
    headers = _auth_headers(client)
    r = _submit(client, headers, type_="podcast")
    assert r.status_code == 422


def test_requires_auth(client):
    r = client.post("/api/tasks", json={"type": "image", "prompt": "x", "model": "m"})
    assert r.status_code == 401


def test_queue_unavailable_503(client, monkeypatch):
    """Review Focus #4：Redis 不可达 → 503 QUEUE_UNAVAILABLE 而非挂起/500。"""
    headers = _auth_headers(client)

    async def _boom(*args, **kwargs):
        raise ConnectionError("redis down")

    monkeypatch.setattr("backend.app.routers.tasks.enqueue_generation_task", _boom)
    r = _submit(client, headers)
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "QUEUE_UNAVAILABLE"


def test_worker_lifecycle_completes_fake_task(client, worker_db):
    """直接调用 worker 函数（绕过 arq 队列）验证生命周期闭环。"""
    headers = _auth_headers(client)
    r = _submit(client, headers)
    task_id = r.json()["task"]["id"]

    from backend.worker import run_generation_task

    result = asyncio.run(run_generation_task({}, task_id))
    assert result["status"] == "completed"

    r = client.get(f"/api/tasks/{task_id}", headers=headers)
    body = r.json()["task"]
    assert body["status"] == "completed"
    assert body["output_url"] == f"/static/fake/{task_id}.png"
    assert body["duration_ms"] is not None


def test_sse_terminal_state_replay(client, db_session):
    """SSE 对已终态任务补发终态帧（订阅晚于完成的竞态兜底）。"""
    headers = _auth_headers(client)
    r = _submit(client, headers)
    task_id = r.json()["task"]["id"]

    from backend.app.services.task_store import get_task_or_none

    task = get_task_or_none(db_session, task_id)
    assert task is not None
    task.mark_completed("/static/fake/x.png")
    db_session.commit()

    with client.stream("GET", f"/api/tasks/stream?task_id={task_id}", headers=headers) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        chunks = list(response.iter_lines())
    assert any("completed" in line for line in chunks), chunks


def test_cancel_task(client):
    headers = _auth_headers(client)
    r = _submit(client, headers)
    task_id = r.json()["task"]["id"]
    r = client.delete(f"/api/tasks/{task_id}", headers=headers)
    assert r.status_code == 200 and r.json()["cancelled"] is True


def test_other_users_task_404(client):
    """用户隔离：carol 提交的任务，bob 不可见。"""
    r = client.post(
        "/api/auth/register",
        json={
            "username": "carol",
            "email": "carol@example.com",
            "password": "password123",
            "invite_code": "letmein",
        },
    )
    headers_carol = {"Authorization": f"Bearer {r.json()['access_token']}"}

    task_id = _submit(client, headers_carol).json()["task"]["id"]

    r = client.post(
        "/api/auth/register",
        json={
            "username": "bob",
            "email": "bob@example.com",
            "password": "password123",
            "invite_code": "letmein",
        },
    )
    assert r.status_code == 200, r.text
    headers_bob = {"Authorization": f"Bearer {r.json()['access_token']}"}

    resp = client.get(f"/api/tasks/{task_id}", headers=headers_bob)
    assert resp.status_code == 404


def test_task_detail_returns_full_prompt_and_params(client):
    """N3+M1：详情端点返回完整 prompt（非 compact）与 params——重试保真的数据源。"""
    headers = _auth_headers(client)
    r = _submit(
        client, headers, prompt="长" * 300, params={"channel_id": 9, "size": "1024x1024"}
    )
    tid = r.json()["task"]["id"]

    detail = client.get(f"/api/tasks/{tid}", headers=headers).json()["task"]
    assert len(detail["prompt"]) == 300  # 不截断
    assert detail["params"]["channel_id"] == 9
    assert detail["params"]["size"] == "1024x1024"

    # 列表仍是 compact（性能不变）
    listed = client.get("/api/tasks", headers=headers).json()["tasks"][0]
    assert len(listed["prompt"]) <= 220
    assert "params" not in listed


# ---- M2: 编排器接线 ----


def test_storyboard_kick_worker_function(client, worker_db, fake_arq_pool):
    """run_storyboard_kick：派发 pending 段；sb 不存在不抛。"""
    import json

    from backend.app.models.storyboard import Storyboard, StoryboardSegment
    from backend.worker import run_storyboard_kick

    factory = worker_db
    sess = factory()
    sb = Storyboard(
        user_id=1,
        title="kick",
        short_description="深夜拉面馆",
        segments_count=2,
        segment_duration=6,
        status="generating",
        metadata_json=json.dumps({"generation_model": "fake-video"}),
    )
    sess.add(sb)
    sess.flush()
    sess.add(StoryboardSegment(storyboard_id=sb.id, segment_index=1, prompt="s1"))
    sess.add(StoryboardSegment(storyboard_id=sb.id, segment_index=2, prompt="s2"))
    sess.commit()
    sid = int(sb.id)
    sess.close()

    out = asyncio.run(run_storyboard_kick({}, sid))
    assert out == {"dispatched": 1}

    # 不存在的 sb：静默不抛
    out = asyncio.run(run_storyboard_kick({}, 424242))
    assert out == {"dispatched": 0}


def test_advance_hook_fires_on_terminal(client, worker_db, fake_arq_pool, monkeypatch):
    """段任务终态时 run_generation_task 尾部钩子触发编排推进。"""
    from unittest.mock import AsyncMock

    from backend.app.models import Task
    from backend.app.services import storyboard_orchestrator

    mock_advance = AsyncMock(return_value=None)
    monkeypatch.setattr(storyboard_orchestrator, "advance_on_task_terminal", mock_advance)

    headers = _auth_headers(client)
    r = _submit(
        client, headers, type_="video", model="fake-video", prompt="seg",
        params={"storyboard_id": 3, "segment_id": 9},
    )
    task_id = r.json()["task"]["id"]

    from backend.worker import run_generation_task

    result = asyncio.run(run_generation_task({}, task_id))
    assert result["status"] == "completed"

    assert mock_advance.await_count == 1
    assert mock_advance.await_args is not None
    db_task = mock_advance.await_args.args[1]
    assert isinstance(db_task, Task) and db_task.id == task_id

    # 钩子抛异常不影响任务结果
    mock_advance2 = AsyncMock(side_effect=RuntimeError("boom"))
    monkeypatch.setattr(storyboard_orchestrator, "advance_on_task_terminal", mock_advance2)
    r2 = _submit(client, headers, type_="video", model="fake-video", prompt="seg2")
    result2 = asyncio.run(run_generation_task({}, r2.json()["task"]["id"]))
    assert result2["status"] == "completed"

    # 非分镜任务不触发钩子参数断言：advance 仍会被调用，由其内部 params 守卫返回
    assert mock_advance2.await_count == 1


def test_storyboard_compose_worker_function(client, worker_db, fake_arq_pool, monkeypatch):
    """run_storyboard_compose：走通 compose 服务；sb 不存在返回 ok=False 不抛。"""

    from backend.app.models.storyboard import Storyboard, StoryboardSegment
    from backend.app.services import storyboard_orchestrator as orch
    from backend.worker import run_storyboard_compose

    monkeypatch.setattr(
        orch, "_compose_video",
        lambda urls, root, **kw: root / "videos" / "compose_w.mp4",
    )
    factory = worker_db
    sess = factory()
    sb = Storyboard(
        user_id=1, title="c", short_description="雪夜拉面馆",
        segments_count=1, segment_duration=6, status="completed",
    )
    sess.add(sb)
    sess.flush()
    sess.add(
        StoryboardSegment(
            storyboard_id=sb.id, segment_index=1, prompt="s1",
            status="completed", output_video_url="/outputs/videos/ok.mp4",
        )
    )
    sess.commit()
    sid = int(sb.id)
    sess.close()

    out = asyncio.run(run_storyboard_compose({}, sid))
    assert out["ok"] is True

    out = asyncio.run(run_storyboard_compose({}, 424242))
    assert out == {"ok": False, "error": "NO_STORYBOARD"}


def test_advance_enqueues_auto_compose(client, worker_db, fake_arq_pool, monkeypatch):
    """全 completed 终局 → 自动入队 run_storyboard_compose（一句话到成片）。"""
    import json

    from backend.app.models.storyboard import Storyboard, StoryboardSegment
    from backend.app.services import storyboard_orchestrator as orch
    from backend.worker import run_generation_task

    monkeypatch.setattr(orch, "_extract_last_frame_url", lambda url: None)
    factory = worker_db
    sess = factory()
    sb = Storyboard(
        user_id=1, title="auto", short_description="雪夜拉面馆",
        segments_count=1, segment_duration=6, status="generating",
        metadata_json=json.dumps({"generation_model": "fake-video"}),
    )
    sess.add(sb)
    sess.flush()
    seg = StoryboardSegment(storyboard_id=sb.id, segment_index=1, prompt="s1", status="pending")
    sess.add(seg)
    sess.commit()
    sid, seg_id = int(sb.id), int(seg.id)
    sess.close()

    from backend.worker import run_storyboard_kick

    asyncio.run(run_storyboard_kick({}, sid))

    # 段任务完成 → advance → 全终态 → sb completed → 自动 compose 入队
    factory2 = worker_db
    sess2 = factory2()
    seg2 = sess2.get(StoryboardSegment, seg_id)
    task_id = seg2.task_id
    sess2.close()
    result = asyncio.run(run_generation_task({}, task_id))
    assert result["status"] == "completed"

    assert any(j[0] == "run_storyboard_compose" for j in fake_arq_pool), fake_arq_pool
    sess3 = factory2()
    sb3 = sess3.get(Storyboard, sid)
    assert sb3.status == "completed"
    sess3.close()


def test_cleanup_stale_compose(client, worker_db):
    """worker 重启卡死的 compose_status=running（超时）被 cron 清理为 failed。"""
    import json
    from datetime import datetime, timedelta

    from backend.app.models.storyboard import Storyboard
    from backend.worker import run_maintenance_job

    factory = worker_db
    sess = factory()
    sb = Storyboard(
        user_id=1, title="stuck", short_description="雪夜拉面馆",
        segments_count=1, segment_duration=6, status="completed",
        metadata_json=json.dumps({"compose_status": "running"}),
        updated_at=datetime.utcnow() - timedelta(minutes=30),
    )
    sess.add(sb)
    sess.commit()
    sid = sb.id
    sess.close()

    asyncio.run(run_maintenance_job({}, "cleanup_stale_tasks", None))

    sess = factory()
    sb = sess.get(Storyboard, sid)
    meta = json.loads(sb.metadata_json)
    sess.close()
    assert meta["compose_status"] == "failed"
    assert "重新合成" in meta["compose_error"]


def test_keyframe_chain_stale_cleanup(client, worker_db):
    """链卡死（超 10 分钟未推进）被 cron 清 flag。"""
    import json
    from datetime import datetime, timedelta

    from backend.app.models.storyboard import Storyboard
    from backend.worker import run_maintenance_job

    factory = worker_db
    sess = factory()
    sb = Storyboard(
        user_id=1, title="stuck-chain", short_description="雪夜拉面馆",
        segments_count=1, segment_duration=6, status="completed",
        metadata_json=json.dumps({"keyframe_chain": True}),
        updated_at=datetime.utcnow() - timedelta(minutes=30),
    )
    sess.add(sb)
    sess.commit()
    sid = sb.id
    sess.close()

    asyncio.run(run_maintenance_job({}, "cleanup_stale_tasks", None))

    sess = factory()
    sb = sess.get(Storyboard, sid)
    meta = json.loads(sb.metadata_json)
    sess.close()
    assert "keyframe_chain" not in meta


def test_list_tasks_total_is_not_page_size(client):
    """审计批次三（P3）：total 曾返回 len(当前页)——分页元数据恒等于 limit。"""
    headers = _auth_headers(client)
    for i in range(3):
        r = client.post(
            "/api/tasks",
            json={"type": "image", "prompt": f"批量 {i}", "model": "fake-image"},
            headers=headers,
        )
        assert r.status_code == 200, r.text
    r = client.get("/api/tasks?limit=2", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["tasks"]) == 2
    assert body["total"] == 3, "total 必须是过滤后的全量计数，不是当前页长度"


def test_transient_failure_auto_retry_once(client, db_session, worker_db):
    """高并发排队超时自动重试（2026-10-07 多角度四连超时修复）：
    瞬时失败（超时类）首次自动重试一次；确定性失败不烧重试。"""
    import asyncio

    from backend.app.models import Task
    from backend.app.services.task_store import create_task
    from backend.worker import run_generation_task

    task = create_task(
        db_session, user_id=1, type_="image", prompt="一只猫", model="fake-image"
    )
    calls = {"n": 0}

    async def flaky_gen(t):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"status": "failed", "error": "渠道响应超时，请稍后重试"}
        return {"status": "completed", "output_url": "/outputs/images/ok.png", "cost_amount": 0}

    result = asyncio.run(
        run_generation_task({"job_id": "t1"}, int(task.id), _gen_override=flaky_gen)
    )
    assert result["status"] == "completed", result
    assert calls["n"] == 2
    t = db_session.get(Task, task.id)
    db_session.expire_all()
    t = db_session.get(Task, task.id)
    assert t.status == "completed"


def test_deterministic_failure_no_retry(client, db_session, worker_db):
    """确定性失败（配额类）不烧自动重试。"""
    import asyncio

    from backend.app.services.task_store import create_task
    from backend.worker import run_generation_task

    task = create_task(
        db_session, user_id=1, type_="image", prompt="一只猫", model="fake-image"
    )
    calls = {"n": 0}

    async def quota_gen(t):
        calls["n"] += 1
        return {"status": "failed", "error": "渠道返回 429：quota exceeded 用量上限"}

    result = asyncio.run(
        run_generation_task({"job_id": "t2"}, int(task.id), _gen_override=quota_gen)
    )
    assert result["status"] == "failed"
    assert calls["n"] == 1


def test_mark_completed_clears_stale_error_and_cancel(db_session):
    """复检教训 B：完成态必须清残留 error_msg/cancel_requested_at——
    重投/取消竞态会留下 completed+error 矛盾态（画布173 事故实证）。"""
    from datetime import datetime

    from backend.app.models import Task

    t = Task(user_id=1, type="image", prompt="p", model="m")
    db_session.add(t)
    db_session.flush()
    t.error_msg = "任务已被取消"
    t.cancel_requested_at = datetime.utcnow()
    db_session.commit()

    t.mark_completed("/outputs/x.png")
    assert t.status == "completed"
    assert t.error_msg is None
    assert t.cancel_requested_at is None


# ---- 2026-10-08 过载事故修复批次（12 连发击穿单 GPU）------------------------


def test_timeout_cancel_distinct_from_user_cancel(client, worker_db, db_session):
    """修 1：arq 超时击杀 ≠ 用户取消。created_at 距终态超 job_timeout 预算的
    CancelledError 应报「超时」，不得误导为「任务已被取消」（没人取消过）。"""
    from datetime import datetime, timedelta

    from backend.app.models import Task

    headers = _auth_headers(client)
    r = _submit(client, headers, type_="video", model="minimax-h3-video")
    task_id = r.json()["task"]["id"]
    t = db_session.get(Task, task_id)
    t.created_at = datetime.utcnow() - timedelta(seconds=1801)
    db_session.commit()

    import asyncio

    from backend.worker import run_generation_task

    async def drive() -> None:
        import contextlib

        with contextlib.suppress(TimeoutError, asyncio.CancelledError):
            # wait_for 的 cancel 注入 = 模拟 arq job_timeout 击杀
            await asyncio.wait_for(
                run_generation_task(
                    {},
                    task_id,
                    _gen_override=_sleep_gen,
                ),
                timeout=0.6,
            )

    async def _sleep_gen(_t):  # noqa: ANN001
        import asyncio as _a

        await _a.sleep(30)

    asyncio.run(drive())
    t = db_session.get(Task, task_id)
    db_session.refresh(t)
    assert t.status == "failed"
    assert "超时" in (t.error_msg or "")
    assert "已被取消" not in (t.error_msg or "")


def test_video_gate_limits_concurrency(worker_db):
    """修 2：视频渠道闸门——同渠道并发 ≤2，第 3 个必须等释放。"""
    from backend.app.services.video_gate import acquire_video_gate, release_video_gate

    class FakeRedis:
        def __init__(self) -> None:
            self.n = 0

        async def incr(self, _k: str) -> int:
            self.n += 1
            return self.n

        async def decr(self, _k: str) -> int:
            self.n = max(0, self.n - 1)
            return self.n

        async def expire(self, _k: str, _ttl: int) -> bool:
            return True

    import asyncio

    r = FakeRedis()
    assert asyncio.run(acquire_video_gate(r, 40, poll_s=0.01)) is True
    assert asyncio.run(acquire_video_gate(r, 40, poll_s=0.01)) is True
    # 第 3 个：无空位 → 等待期间释放一个 → 最终成功
    async def third() -> bool:
        import asyncio as _a

        async def free_later() -> None:
            await _a.sleep(0.05)
            await release_video_gate(r, 40)

        _a.create_task(free_later())
        return await acquire_video_gate(r, 40, timeout_s=2, poll_s=0.01)

    assert asyncio.run(third()) is True
    # 排队超时：拿不到就放弃
    r2 = FakeRedis()
    asyncio.run(acquire_video_gate(r2, 40, poll_s=0.01))
    asyncio.run(acquire_video_gate(r2, 40, poll_s=0.01))
    assert asyncio.run(acquire_video_gate(r2, 40, timeout_s=0.02, poll_s=0.01)) is False


def test_video_timeout_no_retry_amplification(client, worker_db):
    """修 3b：视频超时不再 auto-retry——过载时重试是队列放大器（事故实证）。"""
    calls = {"n": 0}

    async def vid_gen(_t):  # noqa: ANN001
        calls["n"] += 1
        return {"status": "failed", "error": "本地 H3 生成超时（>20 分钟）"}

    headers = _auth_headers(client)
    r = _submit(client, headers, type_="video", model="minimax-h3-video")
    task_id = r.json()["task"]["id"]

    from backend.worker import run_generation_task

    asyncio.run(run_generation_task({}, task_id, _gen_override=vid_gen))
    assert calls["n"] == 1


def test_image_transient_retry_still_works(client, worker_db):
    """图像瞬时失败重试保留（修 3b 只压视频）。"""
    calls = {"n": 0}

    async def img_gen(_t):  # noqa: ANN001
        calls["n"] += 1
        return {"status": "failed", "error": "上游超时 timeout"}

    headers = _auth_headers(client)
    r = _submit(client, headers)
    task_id = r.json()["task"]["id"]

    from backend.worker import run_generation_task

    asyncio.run(run_generation_task({}, task_id, _gen_override=img_gen))
    assert calls["n"] == 2


def test_worker_job_timeout_raised_for_video():
    """修 3a：job_timeout 1800→3600（排队也吃预算，30 分钟必死）。"""
    from backend.worker import WorkerSettings

    assert WorkerSettings.job_timeout == 3600
