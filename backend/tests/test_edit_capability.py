"""worker edit 能力校验分支的事件发布契约（W4 P0-2 配套）。"""

import asyncio


def test_edit_capability_failure_publishes_failed_event(client, worker_db, db_session, monkeypatch):
    """能力校验失败直接落库时必须同时发布 failed 事件——否则 SSE 订阅者永远收不到终态，
    前端节点卡\"生成中\"（W4 P0-2 实况复现）。"""
    from backend.app.core.crypto import encrypt_secret
    from backend.app.models import ProviderChannel, User
    from backend.worker import run_generation_task

    user = User(username="editcap2", email="editcap2@x.io", password_hash="x")
    db_session.add(user)
    db_session.commit()
    # 无 edit 能力的渠道
    ch = ProviderChannel(
        user_id=user.id,
        domain="image",
        name="plain",
        base_url="https://api.example.com/v1",
        key_encrypted=encrypt_secret("sk-x"),
        enabled=1,
        capabilities="[]",
    )
    db_session.add(ch)
    db_session.commit()

    from backend.app.models import Task

    task = Task(
        user_id=user.id,
        type="image",
        prompt="p",
        model="m",
        params=f'{{"mode": "edit", "channel_id": {ch.id}}}',
    )
    db_session.add(task)
    db_session.commit()

    events: list[tuple[int, str]] = []
    import backend.worker as worker_mod

    monkeypatch.setattr(
        worker_mod,
        "publish_task_event",
        lambda tid, etype, data: events.append((tid, etype)),
    )

    task_id = int(task.id)
    result = asyncio.run(run_generation_task({}, task_id))
    assert result["status"] == "failed"
    assert "图像编辑能力" in result["error"]
    # 契约：落库失败的同时发布 failed 事件（SSE 终态可达）
    assert (task_id, "failed") in events, f"未发布 failed 事件，收到: {events}"
