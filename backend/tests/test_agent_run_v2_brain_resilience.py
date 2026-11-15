"""Agent Run v2 · 脑渠道瞬时错误韧性：超时包装 + 退避重试（2026-10-10 run#11 生产实证）。"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from backend.app.models import CanvasProject, User
from backend.app.services import agent_brain, agent_orchestrator
from backend.app.services import agent_run_store as store


@pytest.fixture
def user(db_session):  # noqa: ANN001
    u = User(username="btuser", email="bt@t.local", password_hash="x")
    db_session.add(u)
    db_session.commit()
    return u


@pytest.fixture
def v2_run(db_session, user):  # noqa: ANN001
    proj = CanvasProject(title="bt", user_id=user.id)
    db_session.add(proj)
    db_session.commit()
    return store.create_run(
        db_session, user_id=int(user.id), project_id=int(proj.id), initial_message="m", engine="v2"
    )


def test_call_brain_wraps_read_timeout(db_session, user, monkeypatch):
    """httpx.ReadTimeout 必须被包装为 BrainChannelError(BRAIN_TIMEOUT)——不得裸逃逸。"""

    from backend.app.providers.openai_compat import OpenAICompatProvider

    class _StubProvider(OpenAICompatProvider):
        base_url = "http://brain.local/v1"
        key_plain = "k"
        model = "stub-brain"

    stub = object.__new__(_StubProvider)  # 不跑 __init__：本用例只需属性与 isinstance
    monkeypatch.setattr(
        agent_brain, "_resolve_brain_provider", lambda db, uid: (stub, None)
    )

    class _BoomClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):  # noqa: ANN002
            return False

        async def post(self, *a, **k):  # noqa: ANN002, ANN003
            raise httpx.ReadTimeout("read timed out")

    monkeypatch.setattr(agent_brain.httpx, "AsyncClient", lambda **k: _BoomClient())

    async def _call():
        await agent_brain.call_brain(
            db_session, user_id=int(user.id), messages=[], tool_manifest=[]
        )

    with pytest.raises(agent_brain.BrainChannelError) as ei:
        asyncio.run(_call())
    assert ei.value.code == "BRAIN_TIMEOUT"


def test_kick_v2_transient_retry_then_success(db_session, user, v2_run, monkeypatch):
    calls = {"n": 0}

    async def flaky(db, *, user_id, messages, tool_manifest=None):  # noqa: ANN001
        calls["n"] += 1
        if calls["n"] == 1:
            raise agent_brain.BrainChannelError("BRAIN_TIMEOUT", "超时")
        return {"thought": "恢复", "actions": []}

    enqueued: list[tuple[int, int]] = []

    async def fake_enqueue(run_id, delay=0):
        enqueued.append((run_id, delay))

    monkeypatch.setattr(agent_brain, "call_brain", flaky)
    monkeypatch.setattr(agent_orchestrator, "enqueue_agent_step", fake_enqueue)
    result = asyncio.run(agent_orchestrator.kick_run(db_session, v2_run.id))

    assert result.get("brain_retry") == 1, "瞬时错误 → 退避重试而非处决"
    assert enqueued and enqueued[0][1] == 60, "60s 退避入队"
    fresh = store.get_run(db_session, v2_run.id)
    assert fresh is not None and fresh.status == "waiting"


def test_kick_v2_transient_exhausted_fails(db_session, user, v2_run, monkeypatch):
    for _ in range(3):
        store.bump_ext(db_session, v2_run.id, "brain_transient")

    async def always_timeout(db, *, user_id, messages, tool_manifest=None):  # noqa: ANN001
        raise agent_brain.BrainChannelError("BRAIN_TIMEOUT", "超时")

    async def fake_enqueue(run_id, delay=0):
        pass

    monkeypatch.setattr(agent_brain, "call_brain", always_timeout)
    monkeypatch.setattr(agent_orchestrator, "enqueue_agent_step", fake_enqueue)
    asyncio.run(agent_orchestrator.kick_run(db_session, v2_run.id))
    fresh = store.get_run(db_session, v2_run.id)
    assert fresh is not None and fresh.status == "failed" and fresh.error == "BRAIN_TIMEOUT"


def test_cron_carries_job_name():
    """生产实证（2026-10-10 run#11/12）：裸挂 run_maintenance_job → job_name=None 空转。"""
    import inspect

    from backend.worker import WorkerSettings, _maintenance_cron

    (job,) = WorkerSettings.cron_jobs
    assert job.coroutine is _maintenance_cron
    assert "cleanup_stale_tasks" in inspect.getsource(_maintenance_cron)
