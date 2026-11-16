"""任务生命周期守卫回归测试（三连 bug）：

1. 取消可见性：worker 长会话（expire_on_commit=False）必须看见 API 进程提交的取消。
2. 启动前异常（预算熔断等）不得让任务永远卡 queued。
3. queued 收尸：长期没人接手的 queued 任务必须被标记 failed。
"""

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
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


# ---- 1) 取消可见性（跨会话）------------------------------------------------


def test_cancel_requested_visible_across_sessions(tmp_path):
    """worker 会话必须看见 API 进程提交的 cancel_requested_at。

    此前 is_cancel_requested → get_task_or_none 命中 identity map 缓存的 Task，
    API 进程在另一会话提交的取消永远不可见（取消形同虚设）。
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from backend.app.models import Base, Task
    from backend.app.services.task_store import is_cancel_requested, mark_cancel_requested

    # 真实多连接（文件库）→ 模拟 worker 进程与 API 进程各自的连接
    engine = create_engine(f"sqlite:///{tmp_path / 'cancel.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    worker = factory()
    api = factory()

    task = Task(user_id=1, type="image", prompt="p", model="fake-image", status="queued")
    worker.add(task)
    worker.commit()
    task_id = int(task.id)

    # worker 侧加载并提交（对象进入 identity map 缓存）
    cached = worker.get(Task, task_id)
    assert cached is not None
    assert is_cancel_requested(worker, task_id) is False

    # API 进程（另一会话/连接）标记取消
    assert mark_cancel_requested(api, task_id) is True

    # worker 会话缓存仍是旧值（这正是 bug 的根源），但判定必须读 DB 最新值
    assert cached.cancel_requested_at is None
    assert is_cancel_requested(worker, task_id) is True

    worker.close()
    api.close()


def test_cancel_requested_reads_db_not_session_cache(db_session):
    """同会话内已提交取消 → is_cancel_requested 为 True（缓存亦应一致）。"""
    from backend.app.models import Task
    from backend.app.services.task_store import is_cancel_requested, mark_cancel_requested

    task = Task(user_id=1, type="image", prompt="p", model="fake-image", status="queued")
    db_session.add(task)
    db_session.commit()
    task_id = int(task.id)

    assert is_cancel_requested(db_session, task_id) is False
    assert mark_cancel_requested(db_session, task_id) is True
    assert is_cancel_requested(db_session, task_id) is True

    # 未知任务不算取消
    assert is_cancel_requested(db_session, 987654) is False


def test_worker_provider_cancel_check_sees_api_cancel(client, worker_db, db_session, monkeypatch):
    """worker 注入的 provider.cancel_check 必须看见 API 进程提交的取消。

    端到端：生成过程中被取消 → 任务终态 failed（取消），而非 completed。
    """
    from backend.app.models import Task
    from backend.app.services import provider_service
    from backend.app.services.task_store import mark_cancel_requested
    from backend.worker import run_generation_task

    headers = _auth_headers(client)
    r = client.post(
        "/api/tasks",
        json={"type": "image", "prompt": "hello", "model": "fake-image"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    task_id = r.json()["task"]["id"]

    class StubProvider:
        cancel_check = None

        async def run(self, task, on_event=None):  # noqa: ANN001
            return {"status": "completed", "output_url": "/outputs/images/x.png"}

    stub = StubProvider()
    monkeypatch.setattr(
        provider_service, "resolve_provider_for_task", lambda *a, **k: (stub, None)
    )

    async def gen(_t):  # noqa: ANN001
        # 生成过程中「API 进程」提交取消（另一会话）
        assert stub.cancel_check() is False
        assert mark_cancel_requested(db_session, task_id) is True
        return {"status": "completed", "output_url": "/outputs/images/x.png"}

    result = asyncio.run(run_generation_task({}, task_id, _gen_override=gen))
    assert result["status"] == "failed", result
    db_session.expire_all()
    task = db_session.get(Task, task_id)
    assert task.status == "failed"
    assert "取消" in (task.error_msg or ""), task.error_msg


# ---- 2) 生命周期启动前的异常不得卡 queued ----------------------------------


def test_llm_budget_exceeded_marks_task_failed(client, worker_db, db_session):
    """LLM 预算熔断发生在 lifecycle 之前 → 任务必须落 failed（不得留在 queued）。

    此前异常直接抛出：任务永远 queued，SSE 无终态事件，前端卡「生成中」。
    """
    from backend.app.models import LLMBudget, Task
    from backend.worker import run_generation_task

    headers = _auth_headers(client)
    r = client.post(
        "/api/tasks",
        json={"type": "llm", "prompt": "hello", "model": "fake-llm"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    task_id = r.json()["task"]["id"]

    # 熔断：当日额度用尽
    row = db_session.query(LLMBudget).first()
    assert row is not None
    row.tokens_limit = 100
    row.tokens_used = 100
    db_session.commit()

    result = asyncio.run(run_generation_task({}, task_id))
    assert result["status"] == "failed", result

    db_session.expire_all()
    task = db_session.get(Task, task_id)
    assert task.status == "failed", "预算熔断后任务不得停留在 queued"
    assert task.error_msg, "失败必须带可读原因"
    assert "预算" in (task.error_msg or "")


def test_provider_resolution_failure_marks_task_failed(client, worker_db, db_session):
    """渠道解析失败（无 fake provider）同样不得让任务卡 queued。"""
    from backend.app.models import Task
    from backend.worker import run_generation_task

    headers = _auth_headers(client)
    r = client.post(
        "/api/tasks",
        json={"type": "image", "prompt": "hello", "model": "no-such-model"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    task_id = r.json()["task"]["id"]

    # 关掉 fake provider 回落 → 渠道解析必失败
    import backend.app.core.config as cfg

    settings = cfg.get_settings()
    object.__setattr__(settings, "enable_fake_provider", False)

    try:
        result = asyncio.run(run_generation_task({}, task_id))
    finally:
        object.__setattr__(settings, "enable_fake_provider", True)

    assert result["status"] == "failed", result
    db_session.expire_all()
    task = db_session.get(Task, task_id)
    assert task.status == "failed", "启动前失败的任务不得停留在 queued"
    assert task.error_msg


# ---- 3) queued 收尸 --------------------------------------------------------


def test_stale_queued_task_reaped_but_fresh_kept(client, worker_db, db_session):
    """queued 超时（created_at 旧）被标记 failed；新鲜的 queued 不受影响。"""
    from datetime import datetime, timedelta

    from backend.app.core.config import get_settings
    from backend.app.models import Task
    from backend.worker import run_maintenance_job

    timeout_min = get_settings().queued_task_timeout_min
    old = Task(user_id=1, type="image", prompt="old", model="fake-image", status="queued")
    old.created_at = datetime.utcnow() - timedelta(minutes=timeout_min + 10)
    fresh = Task(user_id=1, type="image", prompt="fresh", model="fake-image", status="queued")
    db_session.add_all([old, fresh])
    db_session.commit()
    old_id, fresh_id = int(old.id), int(fresh.id)

    asyncio.run(run_maintenance_job({}, "cleanup_stale_tasks", None))

    db_session.expire_all()
    old_task = db_session.get(Task, old_id)
    fresh_task = db_session.get(Task, fresh_id)
    assert old_task.status == "failed", "超时 queued 任务必须被收尸"
    assert "排队" in (old_task.error_msg or ""), old_task.error_msg
    assert fresh_task.status == "queued", "新鲜 queued 任务不得被误杀"


def test_stale_queued_reaper_timeout_is_configurable(client, worker_db, db_session):
    """QUEUED_TASK_TIMEOUT_MIN 可配：调小后仅略超新阈值的 queued 任务即被收尸。

    默认 360 分钟（6 小时）时 10 分钟前的 queued 不该被收；把配置调成 5 分钟后
    同一任务被收尸，证明 reaper 的 cutoff 确实跟随配置（不是硬编码常量）。
    """
    from datetime import datetime, timedelta

    from backend.app.core.config import get_settings
    from backend.app.models import Task
    from backend.worker import run_maintenance_job

    settings = get_settings()
    default_timeout = settings.queued_task_timeout_min
    assert default_timeout == 360, "默认阈值必须保持 360（6 小时）"

    # 覆盖配置：阈值收到 5 分钟
    object.__setattr__(settings, "queued_task_timeout_min", 5)

    mid = Task(user_id=1, type="image", prompt="mid", model="fake-image", status="queued")
    mid.created_at = datetime.utcnow() - timedelta(minutes=10)  # < 默认 90：默认不收尸
    db_session.add(mid)
    db_session.commit()
    mid_id = int(mid.id)

    try:
        asyncio.run(run_maintenance_job({}, "cleanup_stale_tasks", None))
    finally:
        object.__setattr__(settings, "queued_task_timeout_min", default_timeout)

    db_session.expire_all()
    t = db_session.get(Task, mid_id)
    assert t.status == "failed", "调小阈值后，10 分钟前的 queued 任务应被收尸"
    assert "排队" in (t.error_msg or ""), t.error_msg

    # 反向：恢复默认阈值（360）后，同样 10 分钟前的任务不再被收尸
    other = Task(user_id=1, type="image", prompt="other", model="fake-image", status="queued")
    other.created_at = datetime.utcnow() - timedelta(minutes=10)
    db_session.add(other)
    db_session.commit()
    other_id = int(other.id)

    asyncio.run(run_maintenance_job({}, "cleanup_stale_tasks", None))

    db_session.expire_all()
    t2 = db_session.get(Task, other_id)
    assert t2.status == "queued", "默认阈值下 10 分钟前的 queued 不该被收尸"


def test_queued_timeout_default_is_360_from_timeouts_constant():
    """默认值链条：core/timeouts.CLEANUP_STALE_QUEUED_MIN = 360（6 小时），
    且它是 Settings.queued_task_timeout_min 的默认来源。"""
    from backend.app.core.config import get_settings
    from backend.app.core.timeouts import CLEANUP_STALE_QUEUED_MIN

    assert CLEANUP_STALE_QUEUED_MIN == 360
    assert get_settings().queued_task_timeout_min == CLEANUP_STALE_QUEUED_MIN


def test_queued_timeout_env_override_queued_task_timeout_min(monkeypatch):
    """环境变量 QUEUED_TASK_TIMEOUT_MIN 生效：默认 360，设 42 → 42。"""
    from backend.app.core.config import get_settings

    assert get_settings().queued_task_timeout_min == 360
    monkeypatch.setenv("QUEUED_TASK_TIMEOUT_MIN", "42")
    get_settings.cache_clear()
    try:
        assert get_settings().queued_task_timeout_min == 42
    finally:
        monkeypatch.delenv("QUEUED_TASK_TIMEOUT_MIN")
        get_settings.cache_clear()


# ---- 4) review 竞态修复：认领守卫 + 终态写保护 ------------------------------


def test_claim_queued_task_atomic_single_winner(worker_db):
    """queued→generating 认领必须原子：第二个 worker（arq 重投）看到 generating
    直接放弃，绝不重复执行；终态/不存在同样不认领。"""
    from backend.app.models import Task
    from backend.app.services.task_store import claim_queued_task

    factory = worker_db
    s1, s2 = factory(), factory()
    t = Task(user_id=1, type="image", prompt="p", model="fake-image", status="queued")
    s1.add(t)
    s1.commit()
    tid = int(t.id)

    # 第一个 worker 认领成功（只占坑不计时：started_at 留给生成真正开始，
    # 否则闸门排队会被 30 分钟收尸误杀）
    assert claim_queued_task(s1, tid) == "claimed"
    s1.refresh(t)
    assert t.status == "generating"
    assert t.phase == "waiting_gate"
    assert t.started_at is None
    assert t.worker_heartbeat_at is not None

    # 第二个 worker（重投）拿到同一 job：看到 generating → 放弃
    assert claim_queued_task(s2, tid) == "generating"

    # 终态任务不被认领（收尸 reaper 已标 failed 的积压 job 直接放弃）
    s2.query(Task).filter(Task.id == tid).update(
        {"status": "failed"}, synchronize_session=False
    )
    s2.commit()
    assert claim_queued_task(s1, tid) == "failed"

    # 不存在的任务
    assert claim_queued_task(s2, 987654) is None
    s1.close()
    s2.close()


def test_reaped_task_job_returns_early_without_running(client, worker_db, db_session):
    """review 竞态：任务被 90 分钟 queued 收尸 reaper 标 failed 后，积压 job 才被
    取到——worker 必须直接放弃：不跑 provider、不得把 failed 改写成 completed。"""
    from backend.app.models import Task
    from backend.worker import run_generation_task

    headers = _auth_headers(client)
    r = client.post(
        "/api/tasks",
        json={"type": "image", "prompt": "hello", "model": "fake-image"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    task_id = r.json()["task"]["id"]

    # 模拟 reaper 抢先（另一会话条件 UPDATE queued→failed）
    n = (
        db_session.query(Task)
        .filter(Task.id == task_id, Task.status == "queued")
        .update(
            {"status": "failed", "phase": "failed", "error_msg": "排队超时：任务长时间未被执行"},
            synchronize_session=False,
        )
    )
    db_session.commit()
    assert n == 1

    calls = {"n": 0}

    async def gen(_t):  # noqa: ANN001
        calls["n"] += 1
        return {"status": "completed", "output_url": "/outputs/images/late.png"}

    result = asyncio.run(run_generation_task({}, task_id, _gen_override=gen))
    assert result.get("status") == "skipped", result
    assert calls["n"] == 0, "已被收尸的任务不得再执行 provider"

    db_session.expire_all()
    t = db_session.get(Task, task_id)
    assert t.status == "failed", "failed 终态不得被积压 job 复活"
    assert "排队超时" in (t.error_msg or "")
    assert t.output_url is None


def test_completed_never_overwrites_failed(client, worker_db, db_session):
    """review 竞态：生成进行中任务被并发写终态 failed（收尸/取消）——provider
    成功返回 completed 也不得覆盖已有终态（条件 CAS WHERE status='generating'）。"""
    from backend.app.models import Task
    from backend.worker import run_generation_task

    headers = _auth_headers(client)
    r = client.post(
        "/api/tasks",
        json={"type": "image", "prompt": "hello", "model": "fake-image"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    task_id = r.json()["task"]["id"]

    async def gen(_t):  # noqa: ANN001
        # 生成期间「另一进程」把任务标 failed（模拟收尸 reaper / 取消竞态）
        n = (
            db_session.query(Task)
            .filter(Task.id == task_id, Task.status == "generating")
            .update(
                {
                    "status": "failed",
                    "phase": "failed",
                    "error_msg": "生成超时：任务可能因服务重启中断，请重试",
                },
                synchronize_session=False,
            )
        )
        db_session.commit()
        assert n == 1
        return {"status": "completed", "output_url": "/outputs/images/ok.png"}

    result = asyncio.run(run_generation_task({}, task_id, _gen_override=gen))
    # 结果被丢弃（superseded），落库终态保持 failed
    assert result.get("superseded") is True or result.get("status") != "completed", result

    db_session.expire_all()
    t = db_session.get(Task, task_id)
    assert t.status == "failed", "completed 不得覆盖 failed 终态"
    assert "生成超时" in (t.error_msg or "")
    assert t.output_url is None, "被否决的产物不得写入"


def test_lifecycle_direct_terminal_task_not_revived(db_session):
    """直调 lifecycle（未经 worker 认领）也不得复活终态任务。"""
    from backend.app.models import Task
    from backend.app.services.task_lifecycle import run_task_lifecycle

    t = Task(user_id=1, type="image", prompt="p", model="fake-image", status="failed",
             error_msg="排队超时：任务长时间未被执行")
    db_session.add(t)
    db_session.commit()
    tid = int(t.id)

    async def gen(_t):  # noqa: ANN001
        return {"status": "completed", "output_url": "/x.png"}

    result = asyncio.run(run_task_lifecycle(db_session, tid, gen))
    assert result.get("superseded") is True or result.get("status") != "completed"
    db_session.expire_all()
    t2 = db_session.get(Task, tid)
    assert t2.status == "failed"
    assert "排队超时" in (t2.error_msg or "")
    assert t2.output_url is None
