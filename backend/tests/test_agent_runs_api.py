"""agent_runs REST API 契约测试：创建/列表/详情/停止/SSE/护栏。"""

from __future__ import annotations

import uuid

import pytest


def _register(client, db_session):  # noqa: ANN001
    from backend.app.models import User

    name = f"ar_{uuid.uuid4().hex[:8]}"
    client.post(
        "/api/auth/register",
        json={
            "username": name,
            "email": f"{name}@x.io",
            "password": "password123",
            "invite_code": "letmein",
        },
    )
    r = client.post("/api/auth/login", json={"identifier": name, "password": "password123"})
    token = r.json()["access_token"]
    u = db_session.query(User).filter(User.username == name).first()
    return {"Authorization": f"Bearer {token}"}, u


@pytest.fixture(autouse=True)
def _brain_done(monkeypatch):
    """脑合成 done——API 契约测试不关心脑质量。"""
    from backend.app.services import agent_brain

    async def fake_call(db, *, user_id, messages, tool_manifest=None):  # noqa: ANN001
        return {"thought": "ok", "done": True, "summary": "完成", "usage": None}

    monkeypatch.setattr(agent_brain, "call_brain", fake_call)


def test_create_run_returns_project(client, db_session, fake_arq_pool):
    h, _ = _register(client, db_session)
    r = client.post(
        "/api/agent-runs",
        headers=h,
        json={"message": "画三张赛博朋克猫", "mentions": []},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["run_id"] > 0 and body["project_id"] > 0
    assert any(j[0] == "run_agent_step" for j in fake_arq_pool)


def test_create_run_message_empty(client, db_session):
    h, _ = _register(client, db_session)
    r = client.post("/api/agent-runs", headers=h, json={"message": "  ", "mentions": []})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "MESSAGE_EMPTY"


def test_create_run_conflict_409(client, db_session):

    h, u = _register(client, db_session)
    client.post("/api/agent-runs", headers=h, json={"message": "第一个", "mentions": []})
    r = client.post("/api/agent-runs", headers=h, json={"message": "第二个", "mentions": []})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "ACTIVE_RUN_EXISTS"
    assert "run_id" in r.json()["detail"]


def test_capability_switch_off(client, db_session, monkeypatch):
    h, u = _register(client, db_session)
    from backend.app.models import CapabilityConfig

    db_session.add(CapabilityConfig(key="agent_enabled", value_json="false"))
    db_session.commit()
    r = client.post("/api/agent-runs", headers=h, json={"message": "x", "mentions": []})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "AGENT_DISABLED"


def test_list_and_detail(client, db_session):
    h, u = _register(client, db_session)
    created = client.post(
        "/api/agent-runs", headers=h, json={"message": "列出来", "mentions": []}
    ).json()

    r = client.get("/api/agent-runs", headers=h)
    assert r.status_code == 200 and len(r.json()["runs"]) >= 1

    r2 = client.get(f"/api/agent-runs/{created['run_id']}", headers=h)
    assert r2.status_code == 200
    assert r2.json()["run"]["id"] == created["run_id"]
    assert isinstance(r2.json()["steps"], list)


def test_detail_not_found_and_ownership(client, db_session):
    h, u = _register(client, db_session)
    assert client.get("/api/agent-runs/99999", headers=h).status_code == 404

    h2, u2 = _register(client, db_session)
    created = client.post(
        "/api/agent-runs", headers=h, json={"message": "我的", "mentions": []}
    ).json()
    assert client.get(f"/api/agent-runs/{created['run_id']}", headers=h2).status_code == 404


def test_stop_run(client, db_session):
    h, u = _register(client, db_session)
    created = client.post(
        "/api/agent-runs", headers=h, json={"message": "停下", "mentions": []}
    ).json()
    r = client.post(f"/api/agent-runs/{created['run_id']}/stop", headers=h)
    assert r.status_code == 200 and r.json()["ok"] is True
    # 幂等
    r2 = client.post(f"/api/agent-runs/{created['run_id']}/stop", headers=h)
    assert r2.status_code == 200


def test_sse_requires_auth(client, db_session):
    r = client.get("/api/agent-runs/1/events")
    assert r.status_code == 401


def test_detail_includes_brain_channel_name(client, db_session):
    """抽屉展示脑模型名：详情接口附 brain_channel_name。"""
    from backend.app.models import AgentRun, ProviderChannel

    h, u = _register(client, db_session)
    created = client.post(
        "/api/agent-runs", headers=h, json={"message": "脑模型", "mentions": []}
    ).json()
    ch = ProviderChannel(
        user_id=None, domain="llm", name="MiniMax M2.7",
        base_url="https://m2.example/v1", key_encrypted="enc:x",
    )
    db_session.add(ch)
    db_session.commit()
    db_session.query(AgentRun).filter(AgentRun.id == created["run_id"]).update(
        {"brain_channel_id": ch.id}
    )
    db_session.commit()
    r = client.get(f"/api/agent-runs/{created['run_id']}", headers=h)
    assert r.status_code == 200
    assert r.json()["run"]["brain_channel_name"] == "MiniMax M2.7"
