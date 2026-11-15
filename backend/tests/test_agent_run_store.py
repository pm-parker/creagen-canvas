"""agent_run_store 单测：CAS 状态机基座 / pending / steps / 活跃 run 判定。"""

from __future__ import annotations

import json

import pytest
from sqlalchemy.orm import Session

from backend.app.models import AgentRun, AgentStep, User
from backend.app.services import agent_run_store as store


@pytest.fixture
def user(db_session: Session) -> User:
    u = User(username="alice", email="alice@t.local", password_hash="x")
    db_session.add(u)
    db_session.commit()
    return u


def _mk_run(db_session, user, status="queued"):
    return store.create_run(
        db_session,
        user_id=user.id,
        project_id=1,
        initial_message="画三张赛博朋克猫",
        mentions=[],
    )


def test_create_run_defaults(db_session, user):
    run = _mk_run(db_session, user)
    assert run.status == "queued"
    assert run.max_steps == 12
    assert run.step_count == 0
    # 首条 user 消息已注入
    msgs = json.loads(run.messages_json)
    assert msgs and msgs[0]["role"] == "user"
    assert "赛博朋克猫" in msgs[0]["content"]
    assert run.pending_json == "[]"


def test_cas_begin_iteration_single_winner(db_session, user):
    run = _mk_run(db_session, user)
    assert store.cas_begin_iteration(db_session, run.id) is True
    # 第二次抢（同状态已变 planning）必须失败——双 kick 幂等基石
    assert store.cas_begin_iteration(db_session, run.id) is False
    assert db_session.get(AgentRun, run.id).status == "planning"


def test_cas_accepts_waiting(db_session, user):
    run = _mk_run(db_session, user)
    store.set_status(db_session, run.id, "waiting")
    assert store.cas_begin_iteration(db_session, run.id) is True


def test_cas_rejects_terminal(db_session, user):
    run = _mk_run(db_session, user)
    store.set_status(db_session, run.id, "stopped")
    assert store.cas_begin_iteration(db_session, run.id) is False


def test_pending_roundtrip_and_filter_terminal(db_session, user):
    run = _mk_run(db_session, user)
    store.set_pending(db_session, run.id, [{"type": "task", "id": 1}, {"type": "task", "id": 2}])
    pending = store.get_pending(db_session, run.id)
    assert [p["id"] for p in pending] == [1, 2]
    # 类型化过滤：只留 task 引用
    assert store.get_pending(db_session, run.id, kind="task")


def test_append_step_and_messages(db_session, user):
    run = _mk_run(db_session, user)
    store.append_step(
        db_session,
        run.id,
        idx=0,
        thought="先出图",
        action={"actions": [{"tool": "generate_image", "args": {}}]},
        result=[{"ok": True}],
    )
    steps = db_session.query(AgentStep).filter(AgentStep.run_id == run.id).all()
    assert len(steps) == 1 and steps[0].thought == "先出图"
    # messages 追加 assistant 消息
    before = len(json.loads(store.get_run(db_session, run.id).messages_json))
    store.append_message(db_session, run.id, {"role": "assistant", "content": "json"})
    after = json.loads(store.get_run(db_session, run.id).messages_json)
    assert len(after) == before + 1


def test_get_messages_json(db_session, user):
    run = _mk_run(db_session, user)
    msgs = store.get_messages(db_session, run.id)
    assert isinstance(msgs, list) and msgs[0]["role"] == "user"


def test_active_run_scoped_to_user(db_session, user):
    from backend.app.models import User as U

    bob = U(username="bob", email="bob@t.local", password_hash="x")
    db_session.add(bob)
    db_session.commit()

    a = _mk_run(db_session, user)
    assert store.get_active_run(db_session, user.id).id == a.id
    assert store.get_active_run(db_session, bob.id) is None
    # 终态后不再活跃
    store.set_status(db_session, a.id, "completed")
    assert store.get_active_run(db_session, user.id) is None


def test_finish_sets_error_and_tokens(db_session, user):
    run = _mk_run(db_session, user)
    store.finish_run(db_session, run.id, "failed", error="PROTOCOL_ERROR")
    fresh = db_session.get(AgentRun, run.id)
    assert fresh.status == "failed" and fresh.error == "PROTOCOL_ERROR"
