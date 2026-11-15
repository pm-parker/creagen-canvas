"""对外 OpenAI 兼容 API（/v1/images/generations）：限流 + 任务/成本落账 + 入参健壮性。

审计批次二（P2）：该端点原先绕过任务表（成本看板/资产库盲区）、无任何限流、
n 非数字直接 500。
"""

import uuid

import pytest


def _h(client):
    """每测试独立用户——限流计数不跨测试串扰。"""
    name = f"compat_{uuid.uuid4().hex[:8]}"
    client.post(
        "/api/auth/register",
        json={
            "username": name,
            "email": f"{name}@x.io",
            "password": "password123",
            "invite_code": "letmein",
        },
    )
    r = client.post(
        "/api/auth/login", json={"identifier": name, "password": "password123"}
    )
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _ch(client, headers):
    r = client.post(
        "/api/providers/channels",
        json={
            "domain": "image", "name": "compat-img",
            "base_url": "http://compat.invalid/v1", "key": "sk-x",
        },
        headers=headers,
    )
    assert r.status_code in (200, 201), r.text


@pytest.fixture
def fake_run(monkeypatch):
    from backend.app.providers.openai_compat import OpenAICompatProvider

    async def _run(self, task, on_event=None):  # noqa: ANN001
        return {"status": "completed", "output_url": "/outputs/uploads/fake_gen.png"}

    monkeypatch.setattr(OpenAICompatProvider, "run", _run)


def test_compat_n_invalid_returns_422(client):
    h = _h(client)
    _ch(client, h)
    r = client.post(
        "/v1/images/generations", json={"prompt": "a cat", "n": "abc"}, headers=h
    )
    assert r.status_code == 422, r.text


def test_compat_records_task_with_cost(client, db_session, fake_run):
    h = _h(client)
    _ch(client, h)
    r = client.post(
        "/v1/images/generations", json={"prompt": "a cat", "model": "fake-image"}, headers=h
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"][0]["url"] == "/outputs/uploads/fake_gen.png"

    from backend.app.models import Task

    row = (
        db_session.query(Task)
        .filter(Task.type == "image", Task.output_url == "/outputs/uploads/fake_gen.png")
        .order_by(Task.id.desc())
        .first()
    )
    assert row is not None, "兼容端点生成必须落任务表（成本看板/资产库可见）"
    assert row.status == "completed"
    assert (row.cost_amount or 0) > 0


def test_compat_rate_limited(client, fake_run):
    h = _h(client)
    _ch(client, h)
    for _ in range(20):
        r = client.post("/v1/images/generations", json={"prompt": "a cat"}, headers=h)
        assert r.status_code == 200, r.text
    r = client.post("/v1/images/generations", json={"prompt": "a cat"}, headers=h)
    assert r.status_code == 429
