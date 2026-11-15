"""Agent Run v2 · M5 脑上下文无损压缩（技术设计 §7）。"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.app.models import CanvasProject, User
from backend.app.models.run_event import RunEvent
from backend.app.services import agent_brain, agent_orchestrator
from backend.app.services import agent_run_store as store


@pytest.fixture
def user(db_session):  # noqa: ANN001
    u = User(username="m5user", email="m5@t.local", password_hash="x")
    db_session.add(u)
    db_session.commit()
    return u


def _v2_run(db_session, user, msgs):  # noqa: ANN001
    proj = CanvasProject(title="m5", user_id=user.id)
    db_session.add(proj)
    db_session.commit()
    run = store.create_run(
        db_session, user_id=int(user.id), project_id=int(proj.id), initial_message="m", engine="v2"
    )
    db_session.query(type(run)).filter(type(run).id == int(run.id)).update(
        {"messages_json": json.dumps(msgs, ensure_ascii=False)}
    )
    db_session.commit()
    db_session.expire_all()
    return store.get_run(db_session, int(run.id)) or run


def _seed_long_msgs(n_chars: int, count: int) -> list[dict]:
    return [{"role": "user", "content": "x" * n_chars} for _ in range(count)]


def test_brain_receives_compacted_projection(db_session, user, monkeypatch, fake_arq_pool):
    msgs = _seed_long_msgs(12_000, 7)  # 84k chars > 60k 触发
    run = _v2_run(db_session, user, msgs)
    seen: list[list[dict]] = []

    async def fake_call(db, *, user_id, messages, tool_manifest=None):  # noqa: ANN001
        seen.append(messages)
        return {"done": True, "summary": "ok"}

    async def fake_sum(db, *, user_id, text):  # noqa: ANN001
        assert "user: xxx" in text, "摘要输入须含被压缩区间原文"
        return "这是压缩摘要"

    monkeypatch.setattr(agent_brain, "call_brain", fake_call)
    monkeypatch.setattr(agent_brain, "summarize_for_compaction", fake_sum)
    asyncio.run(agent_orchestrator.kick_run(db_session, run.id))

    assert seen, "脑必须被调用"
    first = seen[0][0]
    assert "压缩摘要" in first["content"], "投影头部是摘要"
    assert len(seen[0]) < len(msgs), "投影只含摘要+保留窗口"

    fresh = store.get_run(db_session, run.id)
    assert fresh is not None
    assert len(fresh.messages()) >= len(msgs), "原文无损（只增不删——done 轮合法追加）"
    rec = fresh.compaction()
    assert rec and rec["summary"] == "这是压缩摘要" and rec["first_kept"] > 0
    types = [e.type for e in db_session.query(RunEvent).filter(RunEvent.run_id == int(run.id))]
    assert "compaction" in types


def test_below_threshold_no_compaction(db_session, user, monkeypatch, fake_arq_pool):
    msgs = _seed_long_msgs(100, 5)
    run = _v2_run(db_session, user, msgs)

    async def fake_call(db, *, user_id, messages, tool_manifest=None):  # noqa: ANN001
        return {"done": True, "summary": "ok"}

    async def no_sum(db, *, user_id, text):  # noqa: ANN001
        raise AssertionError("未超阈值不得触发摘要")

    monkeypatch.setattr(agent_brain, "call_brain", fake_call)
    monkeypatch.setattr(agent_brain, "summarize_for_compaction", no_sum)
    asyncio.run(agent_orchestrator.kick_run(db_session, run.id))
    fresh = store.get_run(db_session, run.id)
    assert fresh is not None and fresh.compaction() is None


def test_summarize_failure_degrades(db_session, user, monkeypatch, fake_arq_pool):
    msgs = _seed_long_msgs(12_000, 7)
    run = _v2_run(db_session, user, msgs)
    seen: list[list[dict]] = []

    async def fake_call(db, *, user_id, messages, tool_manifest=None):  # noqa: ANN001
        seen.append(messages)
        return {"done": True, "summary": "ok"}

    async def bad_sum(db, *, user_id, text):  # noqa: ANN001
        return None  # 摘要失败

    monkeypatch.setattr(agent_brain, "call_brain", fake_call)
    monkeypatch.setattr(agent_brain, "summarize_for_compaction", bad_sum)
    asyncio.run(agent_orchestrator.kick_run(db_session, run.id))
    assert len(seen[0]) == len(msgs), "降级：用未压缩全文继续"
    fresh = store.get_run(db_session, run.id)
    assert fresh is not None and fresh.compaction() is None


def test_context_overflow_terminal_no_retry(db_session, user, monkeypatch, fake_arq_pool):  # noqa: ANN001
    msgs = _seed_long_msgs(100, 3)
    run = _v2_run(db_session, user, msgs)
    retries: list[int] = []

    async def fake_call(db, *, user_id, messages, tool_manifest=None):  # noqa: ANN001
        raise agent_brain.BrainChannelError("BRAIN_CONTEXT_OVERFLOW", "超限")

    async def fake_enqueue(run_id, delay=0):  # noqa: ANN001
        retries.append(delay)

    monkeypatch.setattr(agent_brain, "call_brain", fake_call)
    monkeypatch.setattr(agent_orchestrator, "enqueue_agent_step", fake_enqueue)
    result = asyncio.run(agent_orchestrator.kick_run(db_session, run.id))
    assert result.get("status") == "failed"
    fresh = store.get_run(db_session, run.id)
    assert fresh is not None
    assert fresh.status == "failed" and fresh.error == "BRAIN_CONTEXT_OVERFLOW"
    assert not retries, "确定性错误不退避重试"


# ---------- 转场（用户设计 2026-10-10）：xfade 滤镜链 ----------


def test_compose_transition_filter_chain(db_session, monkeypatch, tmp_path):  # noqa: ANN001, ARG001
    """xfade 链：offset 按段时长累计（重叠 0.5s）；none 走 concat。"""
    import backend.app.services.media_service as ms

    captured: dict = {}

    def fake_run_ffmpeg(args):  # noqa: ANN001
        captured["args"] = args
        out = args[-1]
        from pathlib import Path

        Path(out).write_bytes(b"x")
        return True

    monkeypatch.setattr(ms, "_run_ffmpeg", fake_run_ffmpeg)
    monkeypatch.setattr(ms, "ensure_local", lambda u, root: tmp_path / u.split("/")[-1])
    monkeypatch.setattr(ms, "_ffprobe_dims", lambda v: (1024, 1024))
    monkeypatch.setattr(ms, "_ffprobe_duration", lambda v: 10.0)
    monkeypatch.setattr(ms, "_out_path", lambda root, p, ext: tmp_path / f"{p}{ext}")

    ms.compose_videos(["/a.mp4", "/b.mp4", "/c.mp4"], transition="fade")
    fc = captured["args"][captured["args"].index("-filter_complex") + 1]
    assert "xfade=transition=fade:duration=0.5:offset=9.500" in fc, "第1接缝 offset=10-0.5"
    assert "offset=19.000" in fc, "第2接缝 offset=(10-0.5)+10-0.5"
    assert "fps=30" in fc, "xfade 路径需同帧率"

    ms.compose_videos(["/a.mp4", "/b.mp4"], transition="none")
    fc2 = captured["args"][captured["args"].index("-filter_complex") + 1]
    assert "concat=n=2" in fc2 and "xfade" not in fc2


def test_compose_transition_whitelist(tmp_path, monkeypatch):  # noqa: ANN001
    import backend.app.services.media_service as ms

    monkeypatch.setattr(ms, "ensure_local", lambda u, root: tmp_path / u)
    with pytest.raises(ms.MediaError) as ei:
        ms.compose_videos(["/a.mp4", "/b.mp4"], transition="explode")
    assert ei.value.code == "MEDIA_COMPOSE_TRANSITION_INVALID"
