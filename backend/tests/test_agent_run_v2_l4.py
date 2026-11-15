"""Agent Run v2 · L4 面板数据：completion 富化 / notices 通道 / to_dict 扩展。"""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest

from backend.app.models import CanvasProject, Task, User
from backend.app.services import agent_run_store as store
from backend.app.services import agent_tools


@pytest.fixture
def user(db_session):  # noqa: ANN001
    u = User(username="l4user", email="l4@t.local", password_hash="x")
    db_session.add(u)
    db_session.commit()
    return u


def _v2_run(db_session, user):  # noqa: ANN001
    proj = CanvasProject(title="l4", user_id=user.id)
    db_session.add(proj)
    db_session.commit()
    return store.create_run(
        db_session, user_id=int(user.id), project_id=int(proj.id), initial_message="m", engine="v2"
    )


def _register(client, db_session):  # noqa: ANN001
    name = f"l4_{uuid.uuid4().hex[:8]}"
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
    from backend.app.models import User

    u = db_session.query(User).filter(User.username == name).first()
    return {"Authorization": f"Bearer {r.json()['access_token']}"}, u


def test_detail_includes_completion_and_engine(client, db_session, user):
    h, u = _register(client, db_session)
    run = _v2_run(db_session, u)
    from backend.app.services.agent_tools import ToolContext

    ctx = ToolContext(
        db=db_session, user=u, run_id=int(run.id), project_id=int(run.project_id),
        reference_whitelist=[], pinned_model=None,
    )
    asyncio.run(
        agent_tools.execute_action(
            ctx,
            {
                "tool": "submit_plan",
                "args": {
                    "deliverables": [
                        {"kind": "video", "label": "镜头1"},
                        {"kind": "video", "label": "镜头2"},
                    ],
                    "est_steps": 4,
                },
            },
        )
    )
    t = Task(
        user_id=u.id, type="video", prompt="p", model="m", status="completed",
        output_url="/outputs/v.mp4", params=json.dumps({"agent_run_id": int(run.id)}),
    )
    db_session.add(t)
    db_session.commit()
    store.map_deliverable_task(db_session, int(run.id), "镜头1", int(t.id))

    r = client.get(f"/api/agent-runs/{int(run.id)}", headers=h)
    assert r.status_code == 200
    detail = r.json()["run"]
    assert detail["engine"] == "v2"
    assert detail["completion"] == {"done": 1, "total": 2}, "L4 完成度投影（1/2）"


def test_notices_endpoint_stream(client, db_session):
    h, _u = _register(client, db_session)
    with client.stream("GET", "/api/agent-runs/notices", headers=h) as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        resp.close()


def test_list_enriches_completion_for_active_v2(client, db_session, user):
    """列表端点为活跃/incomplete 的 v2 run 富化 completion。

    c7d4e4f 提交信息声称"详情+列表"均已富化——列表实为幻觉，本测钉死回归。
    """
    h, u = _register(client, db_session)
    run = _v2_run(db_session, u)
    from backend.app.services.agent_tools import ToolContext

    ctx = ToolContext(
        db=db_session, user=u, run_id=int(run.id), project_id=int(run.project_id),
        reference_whitelist=[], pinned_model=None,
    )
    asyncio.run(
        agent_tools.execute_action(
            ctx,
            {
                "tool": "submit_plan",
                "args": {
                    "deliverables": [
                        {"kind": "video", "label": "镜头1"},
                        {"kind": "video", "label": "镜头2"},
                    ],
                    "est_steps": 4,
                },
            },
        )
    )
    t = Task(
        user_id=u.id, type="video", prompt="p", model="m", status="completed",
        output_url="/outputs/v.mp4", params=json.dumps({"agent_run_id": int(run.id)}),
    )
    db_session.add(t)
    db_session.commit()
    store.map_deliverable_task(db_session, int(run.id), "镜头1", int(t.id))
    store.set_status(db_session, int(run.id), "waiting")

    r = client.get("/api/agent-runs", headers=h)
    mine = [x for x in r.json()["runs"] if x["id"] == int(run.id)]
    assert mine and mine[0].get("completion") == {"done": 1, "total": 2}
