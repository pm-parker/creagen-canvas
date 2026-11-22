"""回归：入口认领后、视频闸门前排队不计入 30 分钟 generating 收尸。

事故链（review 发现）：claim_queued_task 在认领时即写 started_at，而视频任务
拿到 job 后可能要在渠道闸门前排队最多 GATE_QUEUE_TIMEOUT_S（3500s ≈ 58 分钟）。
收尸 sweep 只看 status=generating 且 started_at<now-30min——闸门排队中的任务
被误判为孤儿，标 failed；随后真正生成完成，终态写保护又把 completed 结果丢弃。
"""
import asyncio
from datetime import datetime, timedelta


def test_gate_wait_not_reaped(client, worker_db, db_session, monkeypatch):
    """闸门前排队 31 分钟：收尸不得把等待中的任务标 failed，任务最终 completed。"""
    from backend.app.models import Task
    from backend.app.services import video_gate
    from backend.worker import run_generation_task, run_maintenance_job

    t = Task(user_id=1, type="video", prompt="p", model="fake-video", status="queued")
    db_session.add(t)
    db_session.commit()
    tid = int(t.id)
    seen = {}

    async def fake_acquire(redis, ch, limit=2, **kw):
        # 模拟在闸门前排队 31 分钟：把时间往回拨，然后跑一次定时收尸
        s = worker_db()
        row = s.get(Task, tid)
        seen["status_in_gate"] = row.status
        if row.started_at is not None:
            row.started_at = datetime.utcnow() - timedelta(minutes=31)
        row.created_at = datetime.utcnow() - timedelta(minutes=31)
        s.commit()
        s.close()
        await run_maintenance_job({}, "cleanup_stale_tasks", None)
        s = worker_db()
        row = s.get(Task, tid)
        seen["after_reaper"] = (row.status, row.error_msg)
        s.close()
        return True

    async def fake_release(redis, ch):
        return None

    monkeypatch.setattr(video_gate, "acquire_video_gate", fake_acquire)
    monkeypatch.setattr(video_gate, "release_video_gate", fake_release)
    res = asyncio.run(run_generation_task({"redis": object()}, tid))
    db_session.expire_all()
    final = db_session.get(Task, tid)
    assert seen["after_reaper"][0] != "failed", (
        f"闸门排队中的任务被收尸：{seen['after_reaper']}"
    )
    assert final.status == "completed"
    assert res["status"] == "completed"


def test_task_cancelled_at_gate_not_rerun(client, worker_db, db_session, monkeypatch):
    """闸门后复检：排队期间任务已被取消/写终态——拿到闸门后不得再启动生成。

    终态不覆盖：lifecycle 不被进入，provider 不被调用，任务保持 cancelled。
    """
    import asyncio

    from backend.app.models import Task
    from backend.app.services import video_gate
    from backend.worker import run_generation_task

    t = Task(user_id=1, type="video", prompt="p", model="fake-video", status="queued")
    db_session.add(t)
    db_session.commit()
    tid = int(t.id)

    async def fake_acquire(redis, ch, limit=2, **kw):
        # 闸门排队期间：另一进程把任务写终态（用户取消）
        s = worker_db()
        row = s.get(Task, tid)
        row.status = "cancelled"
        row.cancel_requested_at = datetime.utcnow()
        s.commit()
        s.close()
        return True

    async def fake_release(redis, ch):
        return None

    monkeypatch.setattr(video_gate, "acquire_video_gate", fake_acquire)
    monkeypatch.setattr(video_gate, "release_video_gate", fake_release)

    res = asyncio.run(run_generation_task({"redis": object()}, tid))
    db_session.expire_all()
    final = db_session.get(Task, tid)
    assert res["status"] == "skipped"
    assert final.status == "cancelled"  # 终态未被覆盖，provider 未被调用


def test_claimed_gate_wait_not_reaped_but_stale_generating_is(
    client, worker_db, db_session
):
    """收尸 sweep 的闸门豁免只给 started_at IS NULL（闸门排队中）：
    真正开跑后超过 30 分钟的 generating 仍必须被回收（worker 崩溃孤儿）。"""
    import asyncio
    from datetime import timedelta

    from backend.app.models import Task
    from backend.worker import run_maintenance_job

    fresh = Task(user_id=1, type="video", prompt="p", model="fake-video", status="queued")
    waiting = Task(user_id=1, type="video", prompt="p", model="fake-video", status="queued")
    running = Task(user_id=1, type="video", prompt="p", model="fake-video", status="queued")
    db_session.add_all([fresh, waiting, running])
    db_session.commit()
    now = datetime.utcnow()
    # fresh：刚入库 queued（排队收尸不碰）
    # waiting：已认领、闸门排队中（generating + started_at IS NULL，created_at
    # 仍在 6h 阈值内——两档收尸都不碰；worker 崩溃遗留场景见下一测试）
    db_session.query(Task).filter(Task.id == waiting.id).update(
        {"status": "generating", "phase": "waiting_gate", "created_at": now - timedelta(minutes=45)}
    )
    # running：已开跑 31 分钟（generating + 老 started_at）——必须被收尸
    db_session.query(Task).filter(Task.id == running.id).update(
        {
            "status": "generating",
            "started_at": now - timedelta(minutes=31),
            "created_at": now - timedelta(hours=7),
        }
    )
    db_session.commit()

    asyncio.run(run_maintenance_job({}, "cleanup_stale_tasks", None))
    db_session.expire_all()
    assert db_session.get(Task, fresh.id).status == "queued"
    assert db_session.get(Task, waiting.id).status == "generating"  # 闸门排队豁免
    final = db_session.get(Task, running.id)
    assert final.status == "failed"


def test_gate_wait_worker_crash_reaped_by_queued_sweep(client, worker_db, db_session):
    """闸门豁免不等于杀不死：worker 在闸门排队中崩溃（generating +
    started_at IS NULL）且超过 queued 阈值——由 queued 收尸回收，不永久挂起。"""
    import asyncio
    from datetime import timedelta

    from backend.app.models import Task
    from backend.worker import run_maintenance_job

    stuck = Task(user_id=1, type="video", prompt="p", model="fake-video", status="queued")
    db_session.add(stuck)
    db_session.commit()
    now = datetime.utcnow()
    db_session.query(Task).filter(Task.id == stuck.id).update(
        {
            "status": "generating",
            "phase": "waiting_gate",
            "created_at": now - timedelta(hours=7),
        }
    )
    db_session.commit()

    asyncio.run(run_maintenance_job({}, "cleanup_stale_tasks", None))
    db_session.expire_all()
    final = db_session.get(Task, stuck.id)
    assert final.status == "failed"
    assert "排队超时" in (final.error_msg or "")


def _patch_gate(monkeypatch, acquire, release):
    from backend.app.services import video_gate

    monkeypatch.setattr(video_gate, "acquire_video_gate", acquire)
    monkeypatch.setattr(video_gate, "release_video_gate", release)


def test_cancel_during_gate_wait_releases_acquired_gate_once(
    client, worker_db, db_session, monkeypatch
):
    """闸门等待期间被取消：已成功拿到的槽位释放恰好一次，且不再生成。"""
    from backend.app.models import Task
    from backend.worker import run_generation_task

    t = Task(user_id=1, type="video", prompt="p", model="fake-video", status="queued")
    db_session.add(t)
    db_session.commit()
    tid = int(t.id)
    releases = {"n": 0}

    async def fake_acquire(redis, ch, limit=2, **kw):
        s = worker_db()
        row = s.get(Task, tid)
        row.status = "cancelled"
        row.cancel_requested_at = datetime.utcnow()
        s.commit()
        s.close()
        return True

    async def fake_release(redis, ch):
        releases["n"] += 1

    _patch_gate(monkeypatch, fake_acquire, fake_release)
    res = asyncio.run(run_generation_task({"redis": object()}, tid))
    db_session.expire_all()
    final = db_session.get(Task, tid)
    assert res["status"] == "skipped"
    assert final.status == "cancelled"
    assert releases["n"] == 1


def test_cleanup_during_gate_wait_releases_acquired_gate_once(
    client, worker_db, db_session, monkeypatch
):
    """闸门等待期间被收尸写成 failed：已成功拿到的槽位释放恰好一次，终态保持 failed。"""
    from datetime import timedelta

    from backend.app.models import Task
    from backend.worker import run_generation_task, run_maintenance_job

    t = Task(user_id=1, type="video", prompt="p", model="fake-video", status="queued")
    db_session.add(t)
    db_session.commit()
    tid = int(t.id)
    releases = {"n": 0}

    async def fake_acquire(redis, ch, limit=2, **kw):
        s = worker_db()
        row = s.get(Task, tid)
        row.created_at = datetime.utcnow() - timedelta(hours=7)
        s.commit()
        s.close()
        await run_maintenance_job({}, "cleanup_stale_tasks", None)
        return True

    async def fake_release(redis, ch):
        releases["n"] += 1

    _patch_gate(monkeypatch, fake_acquire, fake_release)
    res = asyncio.run(run_generation_task({"redis": object()}, tid))
    db_session.expire_all()
    final = db_session.get(Task, tid)
    assert res["status"] == "skipped"
    assert final.status == "failed"
    assert "排队超时" in (final.error_msg or "")
    assert releases["n"] == 1


def test_successful_video_generation_releases_acquired_gate_once(
    client, worker_db, db_session, monkeypatch
):
    """正常拿到闸门并完成生成：槽位释放恰好一次。"""
    from backend.app.models import Task
    from backend.worker import run_generation_task

    t = Task(user_id=1, type="video", prompt="p", model="fake-video", status="queued")
    db_session.add(t)
    db_session.commit()
    tid = int(t.id)
    releases = {"n": 0}

    async def fake_acquire(redis, ch, limit=2, **kw):
        return True

    async def fake_release(redis, ch):
        releases["n"] += 1

    _patch_gate(monkeypatch, fake_acquire, fake_release)
    res = asyncio.run(run_generation_task({"redis": object()}, tid))
    db_session.expire_all()
    final = db_session.get(Task, tid)
    assert res["status"] == "completed"
    assert final.status == "completed"
    assert releases["n"] == 1


def test_gate_queue_timeout_never_releases_unacquired_gate(
    client, worker_db, db_session, monkeypatch
):
    """acquire 返回 False（排队超时，槽位未持有）：释放次数为 0。"""
    from backend.app.models import Task
    from backend.worker import run_generation_task

    t = Task(user_id=1, type="video", prompt="p", model="fake-video", status="queued")
    db_session.add(t)
    db_session.commit()
    tid = int(t.id)
    releases = {"n": 0}
    acquires = {"n": 0}

    async def fake_acquire(redis, ch, limit=2, **kw):
        acquires["n"] += 1
        return False

    async def fake_release(redis, ch):
        releases["n"] += 1

    _patch_gate(monkeypatch, fake_acquire, fake_release)
    res = asyncio.run(run_generation_task({"redis": object()}, tid))
    db_session.expire_all()
    final = db_session.get(Task, tid)
    assert acquires["n"] == 1
    assert res["status"] == "failed"
    assert "渠道并发闸门" in (res.get("error") or "")
    assert final.status == "failed"
    assert releases["n"] == 0
