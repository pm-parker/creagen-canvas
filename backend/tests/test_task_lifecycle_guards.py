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

    from backend.app.core.timeouts import CLEANUP_STALE_QUEUED_MIN
    from backend.app.models import Task
    from backend.worker import run_maintenance_job

    old = Task(user_id=1, type="image", prompt="old", model="fake-image", status="queued")
    old.created_at = datetime.utcnow() - timedelta(minutes=CLEANUP_STALE_QUEUED_MIN + 10)
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
