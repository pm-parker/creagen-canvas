"""分镜 M1 契约：CRUD / 分镜替换 / LLM 拆分（respx mock）。"""

import httpx
import pytest

BASE = "https://llm.example"


def _register(client, username):
    r = client.post(
        "/api/auth/register",
        json={
            "username": username,
            "email": f"{username}@x.io",
            "password": "password123",
            "invite_code": "letmein",
        },
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_storyboard_crud_and_segments(client, db_engine):
    headers = _register(client, "sb_user")
    r = client.post(
        "/api/storyboards",
        json={"title": "测试片", "short_description": "一只橘猫的都市冒险", "segments_count": 3},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    sid = r.json()["storyboard"]["id"]

    r = client.put(
        f"/api/storyboards/{sid}/segments",
        json={"segments": [{"prompt": "opening"}, {"prompt": "middle"}, {"prompt": "ending"}]},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    segs = r.json()["segments"]
    assert [s["segment_index"] for s in segs] == [1, 2, 3]

    r = client.get(f"/api/storyboards/{sid}", headers=headers)
    assert len(r.json()["storyboard"]["segments"]) == 3

    r = client.get("/api/storyboards", headers=headers)
    assert any(sb["id"] == sid for sb in r.json()["storyboards"])

    assert client.delete(f"/api/storyboards/{sid}", headers=headers).status_code == 204
    assert client.get(f"/api/storyboards/{sid}", headers=headers).status_code == 404
    from sqlalchemy.orm import sessionmaker as _sm

    from backend.app.models.storyboard import StoryboardSegment

    sess = _sm(bind=db_engine)()
    assert sess.query(StoryboardSegment).count() == 0
    sess.close()


def test_storyboard_user_isolation(client):
    h1 = _register(client, "sb_iso_a")
    h2 = _register(client, "sb_iso_b")
    sid = client.post(
        "/api/storyboards", json={"short_description": "A 的剧本"}, headers=h1
    ).json()["storyboard"]["id"]
    assert client.get(f"/api/storyboards/{sid}", headers=h2).status_code == 404
    assert client.delete(f"/api/storyboards/{sid}", headers=h2).status_code == 404


def test_split_replaces_segments(respx_mock, client, monkeypatch):
    monkeypatch.setenv("ENABLE_FAKE_PROVIDER", "false")
    from backend.app.core.config import get_settings

    get_settings.cache_clear()
    try:
        headers = _register(client, "sb_split")
        client.post(
            "/api/providers/channels",
            json={"domain": "llm", "name": "llm", "base_url": BASE, "key": "sk-test"},
            headers=headers,
        )
        sid = client.post(
            "/api/storyboards",
            json={"short_description": "深夜拉面馆的温暖故事", "segments_count": 2},
            headers=headers,
        ).json()["storyboard"]["id"]

        split_payload = (
            '[{"prompt": "night ramen shop exterior, warm light",'
            ' "camera_motion": "推近", "mood": "温暖"}, '
            '{"prompt": "chef preparing ramen, steam", "camera_motion": "特写", "mood": "专注"}]'
        )
        respx_mock.post(f"{BASE}/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={"choices": [{"message": {"content": split_payload}}]},
            )
        )
        r = client.post(f"/api/storyboards/{sid}/split", json={"confirm": True}, headers=headers)
        assert r.status_code == 200, r.text
        segs = r.json()["segments"]
        assert [s["prompt"] for s in segs] == [
            "night ramen shop exterior, warm light",
            "chef preparing ramen, steam",
        ]

        r = client.post(f"/api/storyboards/{sid}/split", json={"confirm": False}, headers=headers)
        assert r.status_code == 422
    finally:
        get_settings.cache_clear()


# ---- M2: 队列/事件契约 ----


def test_storyboard_kick_enqueued_on_generate_queue(fake_arq_pool):
    import asyncio

    from backend.app.services.task_queue import enqueue_storyboard_kick

    asyncio.run(enqueue_storyboard_kick(7))

    assert len(fake_arq_pool) == 1
    fn, args, kwargs = fake_arq_pool[0]
    assert fn == "run_storyboard_kick"
    assert args == (7,)
    assert kwargs.get("_queue_name") == "generate"


def test_storyboard_event_channel_names(fake_arq_pool, monkeypatch):
    from backend.app.services import task_queue

    assert (
        task_queue.STORYBOARD_EVENT_CHANNEL.format(sid=7) == "canvas:storyboard_events:7"
    )

    # publish：无 Redis 环境下静默（不抛出，仅告警）
    task_queue.publish_storyboard_event(7, "segment_update", {"status": "queued"})

    # subscribe：专用长连接订阅正确频道
    captured: dict = {}

    class FakePubSub:
        def __init__(self):
            self.subscribed = None

        def subscribe(self, channel):
            self.subscribed = channel

    class FakeRedis:
        def __init__(self, **kwargs):
            captured["kwargs"] = kwargs

        @classmethod
        def from_url(cls, url: str, **kwargs):
            captured["url"] = url
            return cls(**kwargs)

        def pubsub(self, **kwargs):
            captured["pubsub_kwargs"] = kwargs
            return FakePubSub()

    monkeypatch.setattr(task_queue, "Redis", FakeRedis)
    pubsub = task_queue.subscribe_storyboard_events(7)
    assert pubsub.subscribed == "canvas:storyboard_events:7"
    # 专用长连接：socket_timeout 必须为 None（空闲不断流）
    assert captured["kwargs"].get("socket_timeout") is None


# ---- M2: 生成 API ----


def _sb_with_segments(client, headers, *, n=3, desc="一只橘猫的都市冒险"):
    sid = client.post(
        "/api/storyboards", json={"short_description": desc, "segments_count": n}, headers=headers
    ).json()["storyboard"]["id"]
    client.put(
        f"/api/storyboards/{sid}/segments",
        json={"segments": [{"prompt": f"seg {i}"} for i in range(1, n + 1)]},
        headers=headers,
    )
    return sid


def test_generate_requires_model(client):
    headers = _register(client, "sb_gen_a")
    sid = _sb_with_segments(client, headers)
    r = client.post(f"/api/storyboards/{sid}/generate", json={"model": "  "}, headers=headers)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "MODEL_REQUIRED"


def test_generate_estimate_without_confirm(client):
    headers = _register(client, "sb_gen_b")
    sid = _sb_with_segments(client, headers, n=3)
    r = client.post(
        f"/api/storyboards/{sid}/generate",
        json={"model": "fake-video", "confirm": False},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["requires_confirm"] is True
    est = body["estimate"]
    assert est["segments"] == 3
    assert est["per_segment"] == pytest.approx(1.35)  # fallback 0.225/s × 6s
    assert est["total"] == pytest.approx(4.05)
    # 零状态变更
    sb = client.get(f"/api/storyboards/{sid}", headers=headers).json()["storyboard"]
    assert sb["status"] == "draft"


def test_generate_confirm_starts(client, fake_arq_pool):
    headers = _register(client, "sb_gen_c")
    sid = _sb_with_segments(client, headers, n=2)
    r = client.post(
        f"/api/storyboards/{sid}/generate",
        json={"model": "fake-video", "confirm": True},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    sb = r.json()["storyboard"]
    assert sb["status"] == "generating"
    assert sb["metadata"]["generation_model"] == "fake-video"
    assert sb["segments"][0]["status"] == "pending"
    assert any(j[0] == "run_storyboard_kick" for j in fake_arq_pool)


def test_generate_rejects_when_generating(client):
    headers = _register(client, "sb_gen_d")
    sid = _sb_with_segments(client, headers, n=2)
    ok = client.post(
        f"/api/storyboards/{sid}/generate",
        json={"model": "fake-video", "confirm": True},
        headers=headers,
    )
    assert ok.status_code == 200
    again = client.post(
        f"/api/storyboards/{sid}/generate",
        json={"model": "fake-video", "confirm": True},
        headers=headers,
    )
    assert again.status_code == 422
    assert again.json()["detail"]["code"] == "ALREADY_GENERATING"


def test_generate_empty_segments_rejected(client):
    headers = _register(client, "sb_gen_e")
    sid = client.post(
        "/api/storyboards", json={"short_description": "空分镜测试"}, headers=headers
    ).json()["storyboard"]["id"]
    r = client.post(
        f"/api/storyboards/{sid}/generate",
        json={"model": "fake-video", "confirm": True},
        headers=headers,
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "SEGMENTS_EMPTY"


def test_generate_queue_unavailable_rolls_back(client, monkeypatch):
    headers = _register(client, "sb_gen_f")
    sid = _sb_with_segments(client, headers, n=2)


    from backend.app.services import task_queue

    async def _raise(sid_):
        raise task_queue.QueueUnavailableError("redis down")

    monkeypatch.setattr(task_queue, "enqueue_storyboard_kick", _raise)
    r = client.post(
        f"/api/storyboards/{sid}/generate",
        json={"model": "fake-video", "confirm": True},
        headers=headers,
    )
    assert r.status_code == 503
    sb = client.get(f"/api/storyboards/{sid}", headers=headers).json()["storyboard"]
    assert sb["status"] == "draft"  # 回滚，不留假 generating


def test_generate_user_isolation(client):
    h1 = _register(client, "sb_gen_g1")
    h2 = _register(client, "sb_gen_g2")
    sid = _sb_with_segments(client, h1, n=1)
    r = client.post(
        f"/api/storyboards/{sid}/generate",
        json={"model": "fake-video", "confirm": True},
        headers=h2,
    )
    assert r.status_code == 404


# ---- M2: 单段重试 / 停止 / SSE ----


def _mark_segment_via_db(db_session, sid, index, *, status, task_id=None, retry_count=0):
    from backend.app.models.storyboard import StoryboardSegment

    seg = (
        db_session.query(StoryboardSegment)
        .filter(StoryboardSegment.storyboard_id == sid, StoryboardSegment.segment_index == index)
        .first()
    )
    seg.status = status
    seg.task_id = task_id
    seg.retry_count = retry_count
    db_session.commit()
    return seg


def test_retry_failed_segment(client, db_session, fake_arq_pool):
    headers = _register(client, "sb_retry_a")
    sid = _sb_with_segments(client, headers, n=2)
    _mark_segment_via_db(db_session, sid, 1, status="failed", retry_count=2)
    # 分镜整体处于 failed 终态
    from backend.app.models.storyboard import Storyboard

    sb = db_session.get(Storyboard, sid)
    sb.status = "failed"
    db_session.commit()

    r = client.post(f"/api/storyboards/{sid}/segments/1/retry", headers=headers)
    assert r.status_code == 200, r.text
    segs = r.json()["segments"]
    assert segs[0]["status"] == "pending"
    # 分镜回到 generating 且 kick 入队
    sb = client.get(f"/api/storyboards/{sid}", headers=headers).json()["storyboard"]
    assert sb["status"] == "generating"
    assert any(j[0] == "run_storyboard_kick" for j in fake_arq_pool)


def test_retry_rejects_non_failed(client, db_session):
    headers = _register(client, "sb_retry_b")
    sid = _sb_with_segments(client, headers, n=1)
    _mark_segment_via_db(db_session, sid, 1, status="completed")
    r = client.post(f"/api/storyboards/{sid}/segments/1/retry", headers=headers)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "SEGMENT_NOT_FAILED"


def test_retry_missing_segment_404(client):
    headers = _register(client, "sb_retry_c")
    sid = _sb_with_segments(client, headers, n=1)
    r = client.post(f"/api/storyboards/{sid}/segments/99/retry", headers=headers)
    assert r.status_code == 404


def test_stop_marks_pending_failed_and_cancels_active(client, db_session):
    from backend.app.models import Task
    from backend.app.services.task_store import create_task

    headers = _register(client, "sb_stop_a")
    sid = _sb_with_segments(client, headers, n=3)
    assert client.post(
        f"/api/storyboards/{sid}/generate",
        json={"model": "fake-video", "confirm": True},
        headers=headers,
    ).status_code == 200  # 置 generating
    active = create_task(
        db_session, user_id=1, type_="video", prompt="seg 1", model="fake-video",
        params={"storyboard_id": sid}, queue_name="video",
    )
    _mark_segment_via_db(db_session, sid, 1, status="queued", task_id=active.id)
    _mark_segment_via_db(db_session, sid, 2, status="pending")

    r = client.post(f"/api/storyboards/{sid}/stop", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()["storyboard"]
    assert body["status"] == "failed"
    statuses = {s["segment_index"]: s["status"] for s in body["segments"]}
    assert statuses[1] == "queued"  # 活跃段状态不变，靠取消兜底
    assert statuses[2] == "failed"  # pending 段终局化
    task = db_session.get(Task, active.id)
    db_session.expire_all()  # API 会话的提交需过期后重读（expire_on_commit=False）
    task = db_session.get(Task, active.id)
    assert task.cancel_requested_at is not None

    # 非 generating 再停 → 422
    again = client.post(f"/api/storyboards/{sid}/stop", headers=headers)
    assert again.status_code == 422


def test_stop_no_revive(db_session, fake_arq_pool):
    """停止后迟到的失败终态：段直接终局，不自动重试、不再 kick。"""
    import asyncio
    import json as _json

    from backend.app.models.storyboard import Storyboard, StoryboardSegment
    from backend.app.services import storyboard_orchestrator as orch
    from backend.app.services.task_store import create_task

    sb = Storyboard(
        user_id=1,
        title="stopped",
        short_description="深夜拉面馆",
        segments_count=2,
        segment_duration=6,
        status="failed",  # 已被手动停止
        metadata_json=_json.dumps({"generation_model": "fake-video"}),
    )
    db_session.add(sb)
    db_session.flush()
    db_session.add(StoryboardSegment(storyboard_id=sb.id, segment_index=1, prompt="s1"))
    db_session.add(StoryboardSegment(storyboard_id=sb.id, segment_index=2, prompt="s2"))
    db_session.commit()
    seg1 = (
        db_session.query(StoryboardSegment)
        .filter(StoryboardSegment.storyboard_id == sb.id, StoryboardSegment.segment_index == 1)
        .first()
    )
    task = create_task(
        db_session, user_id=1, type_="video", prompt=seg1.prompt, model="fake-video",
        params={"storyboard_id": sb.id, "segment_id": seg1.id}, queue_name="video",
    )
    seg1.status, seg1.task_id = "queued", task.id
    db_session.commit()

    asyncio.run(
        orch.advance_on_task_terminal(db_session, task, {"status": "failed", "error": "boom"})
    )

    db_session.refresh(seg1)
    assert seg1.status == "failed"  # 不回 pending
    assert seg1.retry_count == 0  # 无自动重试
    assert fake_arq_pool == []  # 不再 kick


def test_stream_snapshot_first_for_terminal(client):
    headers = _register(client, "sb_stream_a")
    sid = _sb_with_segments(client, headers, n=1)
    # 造终态：直接跑一次（无段级状态机，仅把 sb 置 completed）
    client.post(
        f"/api/storyboards/{sid}/generate",
        json={"model": "fake-video", "confirm": True},
        headers=headers,
    )
    r = client.post(f"/api/storyboards/{sid}/stop", headers=headers)
    assert r.status_code == 200

    with client.stream("GET", f"/api/storyboards/{sid}/stream", headers=headers) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        chunks = list(response.iter_lines())
    assert any("snapshot" in line for line in chunks), chunks
    assert any('"failed"' in line for line in chunks), chunks


def test_stream_404_other_user(client):
    h1 = _register(client, "sb_stream_b1")
    h2 = _register(client, "sb_stream_b2")
    sid = _sb_with_segments(client, h1, n=1)
    r = client.get(f"/api/storyboards/{sid}/stream", headers=h2)
    assert r.status_code == 404


def test_retry_queue_unavailable_rolls_back_status(client, db_session, monkeypatch):
    """Review Focus #5 变体：retry 入队失败必须回滚 sb 的 generating，不留假 generating。"""
    from backend.app.models.storyboard import Storyboard
    from backend.app.services import task_queue

    headers = _register(client, "sb_retry_q")
    sid = _sb_with_segments(client, headers, n=1)
    _mark_segment_via_db(db_session, sid, 1, status="failed")
    sb = db_session.get(Storyboard, sid)
    sb.status = "failed"
    db_session.commit()

    async def _raise(sid_):
        raise task_queue.QueueUnavailableError("redis down")

    monkeypatch.setattr(task_queue, "enqueue_storyboard_kick", _raise)
    r = client.post(f"/api/storyboards/{sid}/segments/1/retry", headers=headers)
    assert r.status_code == 503

    sb = db_session.get(Storyboard, sid)
    db_session.expire_all()
    sb = db_session.get(Storyboard, sid)
    assert sb.status == "failed"  # 不留假 generating


# ---- M3: compose API ----


def test_compose_endpoint_starts(client, db_session, fake_arq_pool, monkeypatch):
    from backend.app.models.storyboard import Storyboard, StoryboardSegment
    from backend.app.services import storyboard_orchestrator as orch

    headers = _register(client, "sb_comp_a")
    sid = _sb_with_segments(client, headers, n=2)
    segs = (
        db_session.query(StoryboardSegment)
        .filter(StoryboardSegment.storyboard_id == sid)
        .order_by(StoryboardSegment.segment_index)
        .all()
    )
    for i, seg in enumerate(segs):
        seg.status = "completed"
        seg.output_video_url = f"/outputs/videos/c{i+1}.mp4"
    sb = db_session.get(Storyboard, sid)
    sb.status = "completed"
    db_session.commit()

    r = client.post(
        f"/api/storyboards/{sid}/compose", json={"bgm_url": None}, headers=headers
    )
    assert r.status_code == 200, r.text
    assert r.json()["compose_status"] == "running"
    assert any(j[0] == "run_storyboard_compose" for j in fake_arq_pool)
    # running 由 job 侧服务层置位（API 预置会自挡 guard——教训：compose 竞态）
    sb = db_session.get(Storyboard, sid)
    db_session.expire_all()
    sb = db_session.get(Storyboard, sid)
    assert orch._meta(sb).get("compose_status") != "running"


def test_compose_endpoint_generating_422(client, db_session):
    from backend.app.models.storyboard import Storyboard

    headers = _register(client, "sb_comp_b")
    sid = _sb_with_segments(client, headers, n=1)
    sb = db_session.get(Storyboard, sid)
    sb.status = "generating"
    db_session.commit()

    r = client.post(f"/api/storyboards/{sid}/compose", json={}, headers=headers)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "NOT_GENERATING"


def test_compose_endpoint_empty_422(client, db_session):
    headers = _register(client, "sb_comp_c")
    sid = _sb_with_segments(client, headers, n=1)
    from backend.app.models.storyboard import Storyboard

    sb = db_session.get(Storyboard, sid)
    sb.status = "completed"  # 无 completed 段
    db_session.commit()

    r = client.post(f"/api/storyboards/{sid}/compose", json={}, headers=headers)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "NO_SEGMENTS"


def test_compose_endpoint_404_other_user(client):
    h1 = _register(client, "sb_comp_d1")
    h2 = _register(client, "sb_comp_d2")
    sid = _sb_with_segments(client, h1, n=1)
    r = client.post(f"/api/storyboards/{sid}/compose", json={}, headers=h2)
    assert r.status_code == 404


# ---- M3: to-canvas 回流 ----


def _sb_with_completed(client, db_session, headers, *, n=3, fail_last=False):
    sid = _sb_with_segments(client, headers, n=n)
    from backend.app.models.storyboard import Storyboard, StoryboardSegment

    segs = (
        db_session.query(StoryboardSegment)
        .filter(StoryboardSegment.storyboard_id == sid)
        .order_by(StoryboardSegment.segment_index)
        .all()
    )
    for i, seg in enumerate(segs):
        is_failed = fail_last and i == n - 1
        seg.status = "failed" if is_failed else "completed"
        if not is_failed:
            seg.output_video_url = f"/outputs/videos/c{i+1}.mp4"
    sb = db_session.get(Storyboard, sid)
    sb.status = "failed" if fail_last else "completed"
    db_session.commit()
    return sid


def test_to_canvas_creates_project_and_nodes(client, db_session):
    headers = _register(client, "sb_canvas_a")
    sid = _sb_with_completed(client, db_session, headers, n=3, fail_last=True)

    r = client.post(f"/api/storyboards/{sid}/to-canvas", headers=headers)
    assert r.status_code == 201, r.text
    body = r.json()
    # 2 completed 段（failed 跳过，无 final_video_url）
    assert body["node_count"] == 2
    pid = body["project_id"]

    from backend.app.models.canvas import CanvasProject

    proj = db_session.get(CanvasProject, pid)
    assert proj is not None and proj.user_id is not None
    assert "成片" in proj.title
    nodes = sorted(proj.nodes, key=lambda n: n.position_x)
    assert len(nodes) == 2
    import json as _json

    for i, node in enumerate(nodes, start=1):
        meta = _json.loads(node.metadata_json)
        assert meta["client_id"] == f"sb{sid}-seg{i}"
        assert meta["outputUrl"] == f"/outputs/videos/c{i}.mp4"
        assert node.type == "video"
    # x 递增（横排）
    assert nodes[0].position_x < nodes[1].position_x


def test_to_canvas_includes_final_node(client, db_session):
    import json as _json

    from backend.app.models.canvas import CanvasProject
    from backend.app.models.storyboard import Storyboard

    headers = _register(client, "sb_canvas_b")
    sid = _sb_with_completed(client, db_session, headers, n=2)
    sb = db_session.get(Storyboard, sid)
    sb.final_video_url = "/outputs/videos/compose_final.mp4"
    db_session.commit()

    r = client.post(f"/api/storyboards/{sid}/to-canvas", headers=headers)
    assert r.status_code == 201
    proj = db_session.get(CanvasProject, r.json()["project_id"])
    assert len(proj.nodes) == 3  # 成片节点 + 2 段
    final_nodes = [n for n in proj.nodes if "final" in (n.metadata_json or "")]
    assert len(final_nodes) == 1
    meta = _json.loads(final_nodes[0].metadata_json)
    assert meta["outputUrl"] == "/outputs/videos/compose_final.mp4"
    # 成片节点位置在段节点上方
    assert final_nodes[0].position_y < min(n.position_y for n in proj.nodes if n not in final_nodes)


def test_to_canvas_nothing_to_export(client, db_session):
    headers = _register(client, "sb_canvas_c")
    sid = _sb_with_segments(client, headers, n=1)
    from backend.app.models.storyboard import Storyboard

    sb = db_session.get(Storyboard, sid)
    sb.status = "failed"  # 全 pending 无 completed
    db_session.commit()

    r = client.post(f"/api/storyboards/{sid}/to-canvas", headers=headers)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "NOTHING_TO_EXPORT"


def test_to_canvas_404_other_user(client):
    h1 = _register(client, "sb_canvas_d1")
    h2 = _register(client, "sb_canvas_d2")
    sid = client.post(
        "/api/storyboards", json={"short_description": "别人的分镜"}, headers=h1
    ).json()["storyboard"]["id"]
    assert client.post(f"/api/storyboards/{sid}/to-canvas", headers=h2).status_code == 404


# ---- M3: SSE 终止条件扩展 ----


def test_stream_terminates_on_compose_done(client, db_session, monkeypatch):
    """compose_done 帧后流终止（不再无限续流）。"""
    import json as _json

    from backend.app.services import task_queue

    headers = _register(client, "sb_stream_c")
    sid = _sb_with_segments(client, headers, n=1)
    from backend.app.models.storyboard import Storyboard

    sb = db_session.get(Storyboard, sid)
    sb.status = "completed"
    sb.metadata_json = _json.dumps({"compose_status": "running"})
    db_session.commit()

    frames = [
        _json.dumps({"type": "snapshot", "data": {}}),
        _json.dumps({"type": "compose_update", "data": {"compose_status": "running"}}),
        _json.dumps({"type": "compose_done", "data": {"final_video_url": "/x.mp4"}}),
        # 终止后不应下发
        _json.dumps({"type": "compose_update", "data": {"compose_status": "running"}}),
    ]

    class FakePubSub:
        def subscribe(self, channel):
            pass

        def get_message(self, timeout=5.0):
            return {"type": "message", "data": frames.pop(0)} if frames else None

        def close(self):
            pass

    monkeypatch.setattr(task_queue, "subscribe_storyboard_events", lambda sid_: FakePubSub())

    with client.stream("GET", f"/api/storyboards/{sid}/stream", headers=headers) as response:
        assert response.status_code == 200
        chunks = list(response.iter_lines())
    joined = "\n".join(chunks)
    assert "compose_done" in joined
    # 终止帧之后无第四帧内容
    assert joined.count("compose_update") == 1


def test_split_routes_to_default_models_channel(respx_mock, client, monkeypatch, db_session):
    """split 按 default_models 的 {channel_id, model} 路由——不硬编码模型、不被首渠道劫持。"""
    monkeypatch.setenv("ENABLE_FAKE_PROVIDER", "false")
    from backend.app.core.config import get_settings

    get_settings.cache_clear()
    try:
        from backend.app.models import ProviderChannel, ProviderModel
        from backend.app.models.capability import CapabilityConfig

        headers = _register(client, "sb_split_route")
        # 渠道 A：llm 首个（sort 靠前），只有模型 wrong-model
        a = ProviderChannel(
            domain="llm", name="A", base_url="https://a.example", key_encrypted="", sort_order=1
        )
        db_session.add(a)
        db_session.flush()
        db_session.add(ProviderModel(channel_id=a.id, model_id="wrong-model", enabled=1))
        # 渠道 B：持有默认模型 good-model
        b = ProviderChannel(domain="llm", name="B", base_url=BASE, key_encrypted="", sort_order=2)
        db_session.add(b)
        db_session.flush()
        db_session.add(ProviderModel(channel_id=b.id, model_id="good-model", enabled=1))
        # 系统默认：llm → B/good-model
        db_session.merge(
            CapabilityConfig(
                key="default_models",
                value_json=f'{{"llm": {{"channel_id": {b.id}, "model": "good-model"}}}}',
            )
        )
        db_session.commit()

        sid = client.post(
            "/api/storyboards",
            json={"short_description": "深夜拉面馆的温暖故事", "segments_count": 2},
            headers=headers,
        ).json()["storyboard"]["id"]

        respx_mock.post(f"{BASE}/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={"choices": [{"message": {"content": '[{"prompt": "a"}, {"prompt": "b"}]'}}]},
            )
        )
        r = client.post(f"/api/storyboards/{sid}/split", json={"confirm": True}, headers=headers)
        assert r.status_code == 200, r.text
        assert len(r.json()["segments"]) == 2
    finally:
        get_settings.cache_clear()


# ---- M4: 保存保产物（内容签名匹配） ----


def _sb_generated(client, db_session, headers, *, n=3):
    """造一个 n 段全 completed（带产物）的分镜，返回 (sid, prompts)。"""
    sid = _sb_with_segments(client, headers, n=n)
    from backend.app.models.storyboard import Storyboard, StoryboardSegment

    segs = (
        db_session.query(StoryboardSegment)
        .filter(StoryboardSegment.storyboard_id == sid)
        .order_by(StoryboardSegment.segment_index)
        .all()
    )
    prompts = []
    for i, seg in enumerate(segs, start=1):
        seg.status = "completed"
        seg.output_video_url = f"/outputs/videos/p{i}.mp4"
        seg.last_frame_url = f"/outputs/media/tail{i}.png"
        prompts.append(seg.prompt)
    sb = db_session.get(Storyboard, sid)
    sb.status = "completed"
    db_session.commit()
    return sid, prompts


def test_save_preserves_untouched_segments(client, db_session):
    headers = _register(client, "sb_m4_a")
    sid, prompts = _sb_generated(client, db_session, headers)

    # 只改第 2 段 prompt（1/3 原样）
    new_prompts = [prompts[0], "changed second segment", prompts[2]]
    r = client.put(
        f"/api/storyboards/{sid}/segments",
        json={"segments": [{"prompt": p} for p in new_prompts]},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    segs = r.json()["segments"]
    assert segs[0]["status"] == "completed"
    assert segs[0]["output_video_url"] == "/outputs/videos/p1.mp4"
    assert segs[2]["status"] == "completed"
    assert segs[2]["output_video_url"] == "/outputs/videos/p3.mp4"
    assert segs[1]["status"] == "pending" and segs[1]["output_video_url"] is None


def test_save_reorder_preserves_artifacts(client, db_session):
    headers = _register(client, "sb_m4_b")
    sid, prompts = _sb_generated(client, db_session, headers)

    # 整体倒序：产物应跟随内容
    r = client.put(
        f"/api/storyboards/{sid}/segments",
        json={"segments": [{"prompt": p} for p in reversed(prompts)]},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    segs = r.json()["segments"]
    assert segs[0]["status"] == "completed"
    assert segs[0]["output_video_url"] == "/outputs/videos/p3.mp4"
    assert segs[1]["output_video_url"] == "/outputs/videos/p2.mp4"
    assert segs[2]["output_video_url"] == "/outputs/videos/p1.mp4"


def test_save_rejects_generating(client, db_session):
    headers = _register(client, "sb_m4_c")
    sid = _sb_with_segments(client, headers, n=1)
    from backend.app.models.storyboard import Storyboard

    sb = db_session.get(Storyboard, sid)
    sb.status = "generating"
    db_session.commit()

    r = client.put(
        f"/api/storyboards/{sid}/segments",
        json={"segments": [{"prompt": "x"}]},
        headers=headers,
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "STORYBOARD_GENERATING"


# ---- M4: completed 段单段重生成 ----


def test_regenerate_completed_segment(client, db_session, fake_arq_pool):
    headers = _register(client, "sb_m4_regen_a")
    sid, _ = _sb_generated(client, db_session, headers)

    r = client.post(f"/api/storyboards/{sid}/segments/2/regenerate", headers=headers)
    assert r.status_code == 200, r.text
    segs = r.json()["segments"]
    assert segs[1]["status"] == "pending"
    # 旧产物保留可见（直到新片到达）
    from backend.app.models.storyboard import StoryboardSegment

    seg = (
        db_session.query(StoryboardSegment)
        .filter(StoryboardSegment.storyboard_id == sid, StoryboardSegment.segment_index == 2)
        .first()
    )
    assert seg.output_video_url == "/outputs/videos/p2.mp4"
    assert seg.task_id is None and seg.retry_count == 0
    # sb 回 generating + kick 入队
    sb = client.get(f"/api/storyboards/{sid}", headers=headers).json()["storyboard"]
    assert sb["status"] == "generating"
    assert any(j[0] == "run_storyboard_kick" for j in fake_arq_pool)


def test_regenerate_rejects_failed(client, db_session):
    headers = _register(client, "sb_m4_regen_b")
    sid = _sb_with_segments(client, headers, n=1)
    _mark_segment_via_db(db_session, sid, 1, status="failed")

    r = client.post(f"/api/storyboards/{sid}/segments/1/regenerate", headers=headers)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "SEGMENT_NOT_COMPLETED"


def test_regenerate_rejects_when_generating(client, db_session):
    headers = _register(client, "sb_m4_regen_c")
    sid, _ = _sb_generated(client, db_session, headers)
    from backend.app.models.storyboard import Storyboard

    sb = db_session.get(Storyboard, sid)
    sb.status = "generating"
    db_session.commit()

    r = client.post(f"/api/storyboards/{sid}/segments/1/regenerate", headers=headers)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "STORYBOARD_GENERATING"


def test_regenerate_stale_task_ignored(client, db_session, fake_arq_pool):
    """regenerate 清 task_id 后，旧任务迟到终态不污染新状态。"""
    import asyncio

    from backend.app.models.storyboard import StoryboardSegment
    from backend.app.services import storyboard_orchestrator as orch
    from backend.app.services.task_store import create_task

    headers = _register(client, "sb_m4_regen_d")
    sid, _ = _sb_generated(client, db_session, headers)
    seg = (
        db_session.query(StoryboardSegment)
        .filter(StoryboardSegment.storyboard_id == sid, StoryboardSegment.segment_index == 1)
        .first()
    )
    old_task = create_task(
        db_session, user_id=1, type_="video", prompt=seg.prompt, model="fake-video",
        params={"storyboard_id": sid, "segment_id": seg.id}, queue_name="video",
    )
    seg.task_id = old_task.id
    db_session.commit()

    r = client.post(f"/api/storyboards/{sid}/segments/1/regenerate", headers=headers)
    assert r.status_code == 200

    # 旧任务迟到终态：seg.task_id 已为 None → 陈旧守卫拒绝
    asyncio.run(
        orch.advance_on_task_terminal(db_session, old_task, {"status": "completed"})
    )
    db_session.refresh(seg)
    assert seg.status == "pending"  # 未被旧终态污染为 completed


# ---- M4: 拆分产出角色档案（契约 v2） ----


def test_split_prompt_bans_dialogue_and_requires_characters():
    from backend.app.services.storyboard_service import split_prompts

    system, _ = split_prompts("简报", "", 6, 6)
    assert "不得包含对白" in system or "不要包含对白" in system
    assert "characters" in system


def test_split_parses_characters_payload(respx_mock, client, monkeypatch, db_session):
    """LLM 返回 {"characters", "segments"} 新形态 → 段落落库 + character_profiles 持久化。"""
    monkeypatch.setenv("ENABLE_FAKE_PROVIDER", "false")
    from backend.app.core.config import get_settings
    from backend.app.models import ProviderChannel, ProviderModel
    from backend.app.models.capability import CapabilityConfig
    from backend.app.models.storyboard import Storyboard

    get_settings.cache_clear()
    try:
        headers = _register(client, "sb_m4_split_a")
        b = ProviderChannel(domain="llm", name="B", base_url=BASE, key_encrypted="", sort_order=1)
        db_session.add(b)
        db_session.flush()
        db_session.add(ProviderModel(channel_id=b.id, model_id="good-model", enabled=1))
        db_session.merge(
            CapabilityConfig(
                key="default_models",
                value_json=f'{{"llm": {{"channel_id": {b.id}, "model": "good-model"}}}}',
            )
        )
        db_session.commit()

        sid = client.post(
            "/api/storyboards",
            json={"short_description": "雨夜面馆母女故事", "segments_count": 2},
            headers=headers,
        ).json()["storyboard"]["id"]

        payload = (
            '{"characters": [{"name": "阿珍", "description": "40yo woman, '
            'flour-dusted apron, hair in a bun"}],'
            ' "segments": [{"prompt": "p1"}, {"prompt": "p2"}]}'
        )
        respx_mock.post(f"{BASE}/chat/completions").mock(
            return_value=httpx.Response(
                200, json={"choices": [{"message": {"content": payload}}]}
            )
        )
        r = client.post(f"/api/storyboards/{sid}/split", json={"confirm": True}, headers=headers)
        assert r.status_code == 200, r.text

        sb = db_session.get(Storyboard, sid)
        db_session.expire_all()
        sb = db_session.get(Storyboard, sid)
        import json as _json

        chars = _json.loads(sb.character_profiles or "[]")
        assert chars and chars[0]["name"] == "阿珍"
    finally:
        get_settings.cache_clear()


def test_split_backcompat_bare_array(respx_mock, client, monkeypatch, db_session):
    """旧渠道返回裸数组仍成功，characters 落 []。"""
    monkeypatch.setenv("ENABLE_FAKE_PROVIDER", "false")
    from backend.app.core.config import get_settings
    from backend.app.models import ProviderChannel, ProviderModel
    from backend.app.models.storyboard import Storyboard

    get_settings.cache_clear()
    try:
        headers = _register(client, "sb_m4_split_b")
        b = ProviderChannel(domain="llm", name="B2", base_url=BASE, key_encrypted="", sort_order=1)
        db_session.add(b)
        db_session.flush()
        db_session.add(ProviderModel(channel_id=b.id, model_id="good-model", enabled=1))
        db_session.commit()

        sid = client.post(
            "/api/storyboards",
            json={"short_description": "雨夜面馆母女故事", "segments_count": 2},
            headers=headers,
        ).json()["storyboard"]["id"]

        respx_mock.post(f"{BASE}/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={"choices": [{"message": {"content": '[{"prompt": "a"}, {"prompt": "b"}]'}}]},
            )
        )
        r = client.post(f"/api/storyboards/{sid}/split", json={"confirm": True}, headers=headers)
        assert r.status_code == 200, r.text
        sb = db_session.get(Storyboard, sid)
        db_session.expire_all()
        sb = db_session.get(Storyboard, sid)
        import json as _json

        assert _json.loads(sb.character_profiles or "[]") == []
    finally:
        get_settings.cache_clear()


# ---- M4: 角色参考图 ----


def test_patch_character_ref(client, db_session):
    headers = _register(client, "sb_m4_ref_a")
    sid = _sb_with_segments(client, headers, n=1)

    r = client.patch(
        f"/api/storyboards/{sid}",
        json={"character_ref_image_url": "/outputs/uploads/ref.png"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["storyboard"]["character_ref_image_url"] == "/outputs/uploads/ref.png"

    # 空串清空
    r = client.patch(
        f"/api/storyboards/{sid}", json={"character_ref_image_url": ""}, headers=headers
    )
    assert r.json()["storyboard"]["character_ref_image_url"] == ""


# ---- M4: 逐段时长 ----


def test_save_roundtrips_target_duration(client, db_session):
    """PUT 带 target_duration_s → 落 ms；只改时长不改四字段时产物保留（T1 语义回归）。"""
    headers = _register(client, "sb_m4_dur_a")
    sid, _ = _sb_generated(client, db_session, headers)

    from backend.app.models.storyboard import StoryboardSegment

    segs = (
        db_session.query(StoryboardSegment)
        .filter(StoryboardSegment.storyboard_id == sid)
        .order_by(StoryboardSegment.segment_index)
        .all()
    )
    payload = [{"prompt": s.prompt, "target_duration_s": 10} for s in segs]
    r = client.put(
        f"/api/storyboards/{sid}/segments", json={"segments": payload}, headers=headers
    )
    assert r.status_code == 200, r.text
    out = r.json()["segments"]
    assert all(s["target_duration_ms"] == 10000 for s in out)
    # 只改时长：产物保留
    assert all(s["status"] == "completed" for s in out)

    # 非法值 7 → 422
    bad = [{"prompt": prompts} for prompts in [s["prompt"] for s in out]]
    bad[0]["target_duration_s"] = 7
    r = client.put(
        f"/api/storyboards/{sid}/segments", json={"segments": bad}, headers=headers
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "TARGET_DURATION_INVALID"


# ---- M5: 关键帧批量生成 ----


def test_keyframes_creates_image_tasks(client, db_session, fake_arq_pool):
    headers = _register(client, "sb_kf_a")
    sid = _sb_with_segments(client, headers, n=3)

    r = client.post(f"/api/storyboards/{sid}/keyframes", json={}, headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 3
    assert [t["segment_index"] for t in body["tasks"]] == [1, 2, 3]

    from backend.app.models import Task
    from backend.app.models.storyboard import StoryboardSegment

    segs = (
        db_session.query(StoryboardSegment)
        .filter(StoryboardSegment.storyboard_id == sid)
        .order_by(StoryboardSegment.segment_index)
        .all()
    )
    for seg in segs:
        assert seg.keyframe_task_id is not None
        task = db_session.get(Task, seg.keyframe_task_id)
        assert task.type == "image"
        assert task.queue_name == "generate"  # 云 API 并行，不占 GPU 队列
        params = task.params_dict()
        assert params["keyframe"] is True
        assert params["segment_id"] == seg.id
        assert params["storyboard_id"] == sid
        assert params["image_count"] == 4
    # fake pool 捕获 generate 队列 job
    assert all(j[2]["_queue_name"] == "generate" for j in fake_arq_pool)


def test_keyframes_selected_indices(client, db_session):
    headers = _register(client, "sb_m5_kf_b")
    sid = _sb_with_segments(client, headers, n=3)

    r = client.post(
        f"/api/storyboards/{sid}/keyframes", json={"segments": [2], "count": 2}, headers=headers
    )
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 1
    from backend.app.models.storyboard import StoryboardSegment

    segs = (
        db_session.query(StoryboardSegment)
        .filter(StoryboardSegment.storyboard_id == sid)
        .order_by(StoryboardSegment.segment_index)
        .all()
    )
    assert segs[1].keyframe_task_id is not None
    assert segs[0].keyframe_task_id is None


def test_keyframes_rejects_when_generating(client, db_session):
    headers = _register(client, "sb_m5_kf_c")
    sid = _sb_with_segments(client, headers, n=1)
    from backend.app.models.storyboard import Storyboard

    sb = db_session.get(Storyboard, sid)
    sb.status = "generating"
    db_session.commit()

    r = client.post(f"/api/storyboards/{sid}/keyframes", json={}, headers=headers)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "STORYBOARD_GENERATING"


def test_keyframes_count_invalid(client):
    headers = _register(client, "sb_m5_kf_d")
    sid = _sb_with_segments(client, headers, n=1)
    r = client.post(
        f"/api/storyboards/{sid}/keyframes", json={"count": 8}, headers=headers
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "COUNT_INVALID"


def test_keyframes_404_other_user(client):
    h1 = _register(client, "sb_m5_kf_e1")
    h2 = _register(client, "sb_m5_kf_e2")
    sid = _sb_with_segments(client, h1, n=1)
    r = client.post(f"/api/storyboards/{sid}/keyframes", json={}, headers=h2)
    assert r.status_code == 404


# ---- M5: 定帧选择 ----


def test_keyframe_select_sets_and_clears(client, db_session):
    headers = _register(client, "sb_m5_sel_a")
    sid = _sb_with_segments(client, headers, n=1)

    r = client.post(
        f"/api/storyboards/{sid}/segments/1/keyframe/select",
        json={"url": "/outputs/images/kf.png"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["segments"][0]["keyframe_url"] == "/outputs/images/kf.png"

    r = client.post(
        f"/api/storyboards/{sid}/segments/1/keyframe/select", json={"url": ""}, headers=headers
    )
    assert r.json()["segments"][0]["keyframe_url"] is None


def test_keyframe_select_invalid_url(client):
    headers = _register(client, "sb_m5_sel_b")
    sid = _sb_with_segments(client, headers, n=1)
    r = client.post(
        f"/api/storyboards/{sid}/segments/1/keyframe/select",
        json={"url": "https://evil.example/x.png"},
        headers=headers,
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "KEYFRAME_URL_INVALID"


# ---- 0.9.2: BGM 持久化 + 耗时预估 ----


def test_patch_bgm_stores_metadata(client, db_session):
    headers = _register(client, "sb_092_bgm_a")
    sid = _sb_with_segments(client, headers, n=1)

    r = client.patch(
        f"/api/storyboards/{sid}",
        json={"bgm_url": "/outputs/uploads/bgm.mp3"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["storyboard"]["metadata"]["bgm_url"] == "/outputs/uploads/bgm.mp3"

    r = client.patch(f"/api/storyboards/{sid}", json={"bgm_url": ""}, headers=headers)
    assert r.json()["storyboard"]["metadata"].get("bgm_url") is None


def test_compose_uses_body_bgm_over_metadata(client, db_session, fake_arq_pool, monkeypatch):
    from backend.app.models.storyboard import Storyboard, StoryboardSegment

    headers = _register(client, "sb_092_bgm_b")
    sid = _sb_with_segments(client, headers, n=1)
    seg = (
        db_session.query(StoryboardSegment)
        .filter(StoryboardSegment.storyboard_id == sid)
        .first()
    )
    seg.status = "completed"
    seg.output_video_url = "/outputs/videos/c1.mp4"
    sb = db_session.get(Storyboard, sid)
    sb.status = "completed"
    sb.metadata_json = '{"bgm_url": "/outputs/uploads/meta_bgm.mp3", "compose_status": "idle"}'
    db_session.commit()

    captured: dict = {}

    async def fake_enqueue_compose(bgm_sid, bgm_url=None):
        captured["bgm_url"] = bgm_url

    monkeypatch.setattr(
        "backend.app.services.task_queue.enqueue_storyboard_compose", fake_enqueue_compose
    )
    # body 显式优先
    r = client.post(
        f"/api/storyboards/{sid}/compose",
        json={"bgm_url": "/outputs/uploads/body_bgm.mp3"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert captured["bgm_url"] == "/outputs/uploads/body_bgm.mp3"
    # 无 body → metadata 兜底
    monkeypatch.setattr(
        "backend.app.services.task_queue.enqueue_storyboard_compose", fake_enqueue_compose
    )
    # compose_status 已 running（上一发入队）——重置后再试
    sb = db_session.get(Storyboard, sid)
    sb.metadata_json = '{"bgm_url": "/outputs/uploads/meta_bgm.mp3", "compose_status": "idle"}'
    db_session.commit()
    r = client.post(f"/api/storyboards/{sid}/compose", json={}, headers=headers)
    assert r.status_code == 200, r.text
    assert captured["bgm_url"] == "/outputs/uploads/meta_bgm.mp3"


def test_estimate_includes_minutes(client, db_session):
    headers = _register(client, "sb_092_est_a")
    sid = _sb_with_segments(client, headers, n=3)
    # 预置同模型历史：2 个 completed 任务，每个 60s
    from datetime import datetime, timedelta

    from backend.app.models import Task

    for i in range(2):
        t = Task(
            user_id=1, type="video", model="fake-video", prompt="p", status="completed",
            duration_ms=60000, output_url=f"/outputs/videos/e{i}.mp4",
            created_at=datetime.utcnow() - timedelta(hours=i + 1),
        )
        db_session.add(t)
    db_session.commit()

    r = client.post(
        f"/api/storyboards/{sid}/generate",
        json={"model": "fake-video", "confirm": False},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    est = r.json()["estimate"]
    assert est["est_minutes"] >= 3  # 3 段 × 60s 中位 = 180s


def test_estimate_minutes_fallback_no_history(client):
    headers = _register(client, "sb_092_est_b")
    sid = _sb_with_segments(client, headers, n=2)
    r = client.post(
        f"/api/storyboards/{sid}/generate",
        json={"model": "brand-new-model-x", "confirm": False},
        headers=headers,
    )
    est = r.json()["estimate"]
    assert est["est_minutes"] == 3  # 2 段 × 90s 回落 = 180s


def test_uploads_accepts_audio(client, monkeypatch):
    """0.9.2 BGM 上传：audio 类型进白名单（10MB cap）。"""
    import base64

    # 限流窗口是进程级内存，跨测试累积会误伤——本测试只验白名单
    from backend.app.routers import uploads as uploads_router

    monkeypatch.setattr(uploads_router, "enforce", lambda *a, **kw: None)

    headers = _register(client, "sb_092_up_a")
    mp3 = base64.b64encode(b"ID3" + b"\x00" * 32).decode()
    r = client.post(
        "/api/uploads",
        json={"data_url": f"data:audio/mpeg;base64,{mp3}"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    assert r.json()["url"].endswith(".mp3")


def test_split_prompt_v3_mention_syntax():
    """拆分 v3：引用角色用 @角色名 语法，派发时自动展开。"""
    from backend.app.services.storyboard_service import split_prompts

    system, _ = split_prompts("简报", "", 6, 6)
    assert "@角色名" in system
    assert "@引用" in system or "使用 @" in system


# ---- 0.9.4: 全局风格锚 ----


def test_patch_style_prompt_stored(client):
    headers = _register(client, "sb_m6_style_a")
    sid = _sb_with_segments(client, headers, n=1)
    r = client.patch(
        f"/api/storyboards/{sid}",
        json={"style_prompt": "cinematic film look, muted cool tones"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert (
        r.json()["storyboard"]["metadata"]["style_prompt"]
        == "cinematic film look, muted cool tones"
    )


def test_keyframe_injects_style(client, db_session):
    headers = _register(client, "sb_m6_style_b")
    sid = _sb_with_segments(client, headers, n=1)
    client.patch(
        f"/api/storyboards/{sid}",
        json={"style_prompt": "cinematic film look, muted cool tones"},
        headers=headers,
    )

    r = client.post(f"/api/storyboards/{sid}/keyframes", json={}, headers=headers)
    assert r.status_code == 200, r.text
    from backend.app.models import Task

    task_id = r.json()["tasks"][0]["task_id"]
    task = db_session.get(Task, task_id)
    assert "cinematic film look, muted cool tones" in task.prompt


def test_no_style_no_suffix(client, db_session):
    headers = _register(client, "sb_m6_style_c")
    sid = _sb_with_segments(client, headers, n=1)
    r = client.post(f"/api/storyboards/{sid}/keyframes", json={}, headers=headers)
    from backend.app.models import Task

    task = db_session.get(Task, r.json()["tasks"][0]["task_id"])
    assert "muted cool tones" not in task.prompt  # 无风格锚时不追加风格（16:9 指引除外）


# ---- 0.9.4-T2: 关键帧 16:9 构图指引 ----


def test_keyframe_prompt_includes_aspect_guide(client, db_session):
    headers = _register(client, "sb_m6_aspect")
    sid = _sb_with_segments(client, headers, n=1)
    r = client.post(f"/api/storyboards/{sid}/keyframes", json={}, headers=headers)
    from backend.app.models import Task

    task = db_session.get(Task, r.json()["tasks"][0]["task_id"])
    assert "16:9" in task.prompt


def test_keyframes_chain_mode_task_marks_chain(client, db_session, fake_arq_pool):
    """链模式派发的任务 params 带 keyframe_chain=true（终态钩子推进依据）。"""
    headers = _register(client, "sb_m6_chainmark")
    sid = _sb_with_segments(client, headers, n=1)
    r = client.post(
        f"/api/storyboards/{sid}/keyframes", json={"chain": True}, headers=headers
    )
    assert r.status_code == 200, r.text
    from backend.app.models import Task

    task = db_session.get(Task, r.json()["tasks"][0]["task_id"])
    assert task.params_dict()["keyframe_chain"] is True


# ---- 0.10: 按引擎并发 ----


def test_generate_concurrency_stored(client, db_session):
    headers = _register(client, "sb_010_cc_a")
    sid = _sb_with_segments(client, headers, n=2)
    r = client.post(
        f"/api/storyboards/{sid}/generate",
        json={"model": "fake-video", "confirm": True, "concurrency": 2},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    sb = client.get(f"/api/storyboards/{sid}", headers=headers).json()["storyboard"]
    assert sb["metadata"]["max_concurrent"] == 2


def test_generate_concurrency_invalid(client):
    headers = _register(client, "sb_010_cc_b")
    sid = _sb_with_segments(client, headers, n=1)
    r = client.post(
        f"/api/storyboards/{sid}/generate",
        json={"model": "fake-video", "confirm": True, "concurrency": 9},
        headers=headers,
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "CONCURRENCY_INVALID"




# ---- 0.10: 质量信号 ----


def test_save_records_edit_stats(client, db_session):
    """保存 3 段改 1 段 → last_edit_stats={edited:1,total:3}；重排序不算改。"""
    headers = _register(client, "sb_010_stats_a")
    sid, prompts = _sb_generated(client, db_session, headers)

    new_prompts = [prompts[0], "edited middle", prompts[2]]
    client.put(
        f"/api/storyboards/{sid}/segments",
        json={"segments": [{"prompt": p} for p in new_prompts]},
        headers=headers,
    )
    sb = client.get(f"/api/storyboards/{sid}", headers=headers).json()["storyboard"]
    stats = sb["metadata"].get("last_edit_stats")
    assert stats == {"edited": 1, "total": 3}

    # 无变化保存 → edited=0
    client.put(
        f"/api/storyboards/{sid}/segments",
        json={"segments": [{"prompt": p} for p in new_prompts]},
        headers=headers,
    )
    sb = client.get(f"/api/storyboards/{sid}", headers=headers).json()["storyboard"]
    assert sb["metadata"]["last_edit_stats"] == {"edited": 0, "total": 3}


def test_regenerate_increments_counter(client, db_session, fake_arq_pool):
    headers = _register(client, "sb_010_stats_b")
    sid, _ = _sb_generated(client, db_session, headers)
    client.post(f"/api/storyboards/{sid}/segments/2/regenerate", headers=headers)
    # pending → 422 不计数
    client.post(f"/api/storyboards/{sid}/segments/2/regenerate", headers=headers)
    sb = client.get(f"/api/storyboards/{sid}", headers=headers).json()["storyboard"]
    assert sb["metadata"].get("regenerate_total") == 1


def test_stats_endpoint(client, db_session, fake_arq_pool):
    headers = _register(client, "sb_010_stats_c")
    sid, _ = _sb_generated(client, db_session, headers)
    client.post(f"/api/storyboards/{sid}/segments/1/regenerate", headers=headers)

    r = client.get(f"/api/storyboards/{sid}/stats", headers=headers)
    assert r.status_code == 200, r.text
    stats = r.json()
    assert stats["segments_total"] == 3
    assert stats["completed"] == 2  # regenerate 置段 1 pending（其余 completed）
    assert stats["regenerations"] == 1
    assert "edit_ratio" in stats


def test_generate_rejects_when_all_completed(client, db_session):
    """P0（生产 sb5 案例）：全 completed 分镜重跑全片——无可入队段，若翻 generating
    将永久卡死（无终态钩子可翻转），必须显式 422 拒绝且不破坏既有终态。"""
    headers = _register(client, "sb_gen_done")
    sid = _sb_with_completed(client, db_session, headers, n=2)

    r = client.post(
        f"/api/storyboards/{sid}/generate",
        json={"model": "fake-video", "confirm": True},
        headers=headers,
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "NOTHING_TO_REGENERATE"
    sb = client.get(f"/api/storyboards/{sid}", headers=headers).json()["storyboard"]
    assert sb["status"] == "completed"


# ---- P1 SSRF 守卫（bgm_url / character_ref 仅限站内 /outputs/） ----

def test_patch_rejects_remote_bgm_url(client):
    """bgm_url 任意 http(s) 会经 ensure_local 下载且产物静态可取（SSRF 读取原语）→ 必须 422。"""
    headers = _register(client, "sb_ssrf_a")
    sid = _sb_with_segments(client, headers, n=1)
    r = client.patch(
        f"/api/storyboards/{sid}",
        json={"bgm_url": "http://169.254.169.254/latest/meta-data"},
        headers=headers,
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "URL_INVALID"


def test_patch_rejects_remote_character_ref(client):
    headers = _register(client, "sb_ssrf_b")
    sid = _sb_with_segments(client, headers, n=1)
    r = client.patch(
        f"/api/storyboards/{sid}",
        json={"character_ref_image_url": "http://10.0.0.1:8080/secret.png"},
        headers=headers,
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "URL_INVALID"


def test_patch_accepts_local_outputs_bgm(client, db_session):
    headers = _register(client, "sb_ssrf_c")
    sid = _sb_with_segments(client, headers, n=1)
    r = client.patch(
        f"/api/storyboards/{sid}",
        json={"bgm_url": "/outputs/uploads/20261004_abcd1234.mp3"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    from backend.app.models.storyboard import Storyboard as _SB

    sb = db_session.get(_SB, sid)
    import json as _j
    assert _j.loads(sb.metadata_json)["bgm_url"] == "/outputs/uploads/20261004_abcd1234.mp3"


def test_compose_rejects_remote_bgm_url(client, db_session):
    headers = _register(client, "sb_ssrf_d")
    sid = _sb_with_completed(client, db_session, headers, n=1)
    r = client.post(
        f"/api/storyboards/{sid}/compose",
        json={"bgm_url": "http://internal-service:9090/admin"},
        headers=headers,
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "URL_INVALID"


def test_stop_reaggregates_counts(client, db_session):
    """P2：stop 的事件/响应段计数必须重新聚合（原版携带翻转前的旧值）。"""
    headers = _register(client, "sb_stop_c")
    sid = _sb_with_segments(client, headers, n=3)
    assert client.post(
        f"/api/storyboards/{sid}/generate",
        json={"model": "fake-video", "confirm": True},
        headers=headers,
    ).status_code == 200
    _mark_segment_via_db(db_session, sid, 1, status="completed")

    r = client.post(f"/api/storyboards/{sid}/stop", headers=headers)
    assert r.status_code == 200, r.text
    sb = r.json()["storyboard"]
    assert sb["completed_segments"] == 1
    assert sb["failed_segments"] == 2


def test_generate_confirm_recalculates_aggregates(client, db_session):
    """R2 数据一致性：generate 启动必须重算段聚合——重编辑后的分镜
    completed_segments 仍是旧值（生产 sb4 曾 6/3 自相矛盾）。"""
    headers = _register(client, "sb_agg_a")
    sid = _sb_with_segments(client, headers, n=2)
    _mark_segment_via_db(db_session, sid, 1, status="completed")
    _mark_segment_via_db(db_session, sid, 2, status="failed")

    r = client.post(
        f"/api/storyboards/{sid}/generate",
        json={"model": "fake-video", "confirm": True},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    sb = r.json()["storyboard"]
    assert sb["completed_segments"] == 1  # completed 段保留
    assert sb["failed_segments"] == 0  # failed 段已复位 pending
    statuses = {s["segment_index"]: s["status"] for s in sb["segments"]}
    assert statuses[1] == "completed" and statuses[2] == "pending"
