"""画布级 Agent API（P2 战略项）：API Key 认证 + 一句话建节点入队 + 完成回写。

场景：外部 agent（OpenClaw 等）持用户 API Key，直接向画布提交创作指令，
服务端闭环：建节点 → 入队 → worker 完成 → 回写节点 outputUrl——无需前端在线。
"""

REGISTER = {
    "username": "agentowner",
    "email": "agent@example.com",
    "password": "password123",
    "invite_code": "letmein",
}


def _headers(client):
    client.post("/api/auth/register", json=REGISTER)
    r = client.post(
        "/api/auth/login",
        json={"identifier": REGISTER["username"], "password": REGISTER["password"]},
    )
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _mk_project(client, headers):
    r = client.post("/api/canvas/projects", json={"title": "Agent 画布"}, headers=headers)
    return r.json()["project"]["id"]


class TestApiKey:
    def test_generate_and_regenerate(self, client, db_session):
        h = _headers(client)
        r = client.post("/api/settings/api-key", headers=h)
        assert r.status_code == 201
        key1 = r.json()["api_key"]
        assert key1.startswith("cg-") and len(key1) >= 20

        # 轮换：旧 key 立即失效
        r = client.post("/api/settings/api-key", headers=h)
        key2 = r.json()["api_key"]
        assert key2 != key1

    def test_revoke(self, client, db_session):
        h = _headers(client)
        key = client.post("/api/settings/api-key", headers=h).json()["api_key"]
        assert client.delete("/api/settings/api-key", headers=h).status_code == 200
        # 撤销后 401
        r = client.post(
            "/api/canvas/projects/1/agent",
            headers={"X-API-Key": key},
            json={"message": "生成一只猫"},
        )
        assert r.status_code == 401

    def test_get_current_key_masked(self, client, db_session):
        h = _headers(client)
        key = client.post("/api/settings/api-key", headers=h).json()["api_key"]
        r = client.get("/api/settings/api-key", headers=h)
        shown = r.json()["api_key"]
        assert shown != key and shown.startswith("cg-") and "*" in shown  # 脱敏


class TestAgentEndpoint:
    def test_agent_creates_node_and_task(self, client, db_session, monkeypatch):
        h = _headers(client)
        pid = _mk_project(client, h)
        key = client.post("/api/settings/api-key", headers=h).json()["api_key"]

        # mock 入队（不依赖 Redis）
        from unittest.mock import patch

        with patch("backend.app.services.task_queue.enqueue_generation_task") as eq:
            eq.return_value = None
            r = client.post(
                f"/api/canvas/projects/{pid}/agent",
                headers={"X-API-Key": key},
                json={"message": "赛博朋克城市夜景，霓虹雨后街道"},
            )
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["task_id"] > 0
        assert body["node_client_id"]

        # 画布上出现带 prompt 的节点
        proj = client.get(f"/api/canvas/projects/{pid}", headers=h).json()["project"]
        nodes = [
            n for n in proj["nodes"]
            if n["metadata"].get("client_id") == body["node_client_id"]
        ]
        assert len(nodes) == 1
        assert "赛博朋克" in nodes[0]["metadata"]["prompt"]
        assert nodes[0]["metadata"].get("canvas_agent") is True

        # 任务路由正确（image 默认）且带 canvas_target
        import json as _json

        from backend.app.models import Task

        task = db_session.get(Task, body["task_id"])
        params = _json.loads(task.params)
        assert task.type == "image"
        assert params["canvas_target"] == {
            "project_id": pid,
            "node_client_id": body["node_client_id"],
        }

    def test_invalid_key_401(self, client, db_session):
        h = _headers(client)
        pid = _mk_project(client, h)
        r = client.post(
            f"/api/canvas/projects/{pid}/agent",
            headers={"X-API-Key": "cg-invalid-invalid-invalid"},
            json={"message": "x"},
        )
        assert r.status_code == 401

    def test_other_users_canvas_404(self, client, db_session):
        h = _headers(client)
        pid = _mk_project(client, h)

        # 第二个用户
        reg2 = dict(REGISTER, username="agentother", email="other@example.com")
        client.post("/api/auth/register", json=reg2)
        login2 = client.post(
            "/api/auth/login", json={"identifier": "agentother", "password": "password123"}
        )
        key2 = client.post(
            "/api/settings/api-key",
            headers={"Authorization": f"Bearer {login2.json()['access_token']}"},
        ).json()["api_key"]

        r = client.post(
            f"/api/canvas/projects/{pid}/agent",
            headers={"X-API-Key": key2},
            json={"message": "x"},
        )
        assert r.status_code == 404


class TestCanvasWriteback:
    """任务完成/失败回写画布节点（服务端闭环）。"""

    def _task_with_target(self, db_session, pid, node_cid, type_="image", status="queued"):
        import json as _json

        from backend.app.models import Task

        t = Task(
            user_id=1, type=type_, prompt="测试", model="m", status=status,
            params=_json.dumps({
                "canvas_target": {"project_id": pid, "node_client_id": node_cid},
            }),
        )
        db_session.add(t)
        db_session.commit()
        return t.id

    def test_completed_writes_output_to_node(self, client, db_session):
        import json as _json

        # 直接造画布节点（DB 层，避免走同步 API 的形态细节）
        from backend.app.models import CanvasNode, CanvasProject
        from backend.app.services.task_lifecycle import run_task_lifecycle

        proj = CanvasProject(user_id=1, title="回写测试")
        db_session.add(proj)
        db_session.commit()
        node = CanvasNode(
            project_id=proj.id, type="image", title="待生成",
            position_x=0, position_y=0,
            metadata_json=_json.dumps({"client_id": "agent-node-1", "prompt": "一只猫"}),
        )
        db_session.add(node)
        db_session.commit()

        tid = self._task_with_target(db_session, proj.id, "agent-node-1")

        async def fake_gen(t):
            return {
                "status": "completed",
                "output_url": "/outputs/agent_cat.png",
                "extra_urls": ["/outputs/agent_cat2.png"],
                "cost_amount": 0.02,
            }

        import asyncio

        asyncio.run(run_task_lifecycle(db_session, tid, fake_gen))

        db_session.refresh(node)
        meta = _json.loads(str(node.metadata_json))
        assert meta["outputUrl"] == "/outputs/agent_cat.png"
        # 主图在首位（前端 1/N 切换器约定）
        assert meta["extraUrls"] == ["/outputs/agent_cat.png", "/outputs/agent_cat2.png"]
        assert meta.get("canvas_agent_error") is None

    def test_failed_writes_error_to_node(self, client, db_session):
        import json as _json

        from backend.app.models import CanvasNode, CanvasProject
        from backend.app.services.task_lifecycle import run_task_lifecycle

        proj = CanvasProject(user_id=1, title="失败回写")
        db_session.add(proj)
        db_session.commit()
        node = CanvasNode(
            project_id=proj.id, type="image", title="待生成",
            position_x=0, position_y=0,
            metadata_json=_json.dumps({"client_id": "agent-node-2", "prompt": "x"}),
        )
        db_session.add(node)
        db_session.commit()

        tid = self._task_with_target(db_session, proj.id, "agent-node-2")

        async def fake_gen(t):
            return {"status": "failed", "error": "渠道限流"}

        import asyncio

        asyncio.run(run_task_lifecycle(db_session, tid, fake_gen))

        db_session.refresh(node)
        meta = _json.loads(str(node.metadata_json))
        assert meta.get("outputUrl") is None
        assert "渠道限流" in meta.get("canvas_agent_error", "")


class TestDefaultModelFormat:
    """default_models KV 是 {channel_id, model} dict 格式（settings 端点写入）。"""

    def test_dict_format_parsed(self, client, db_session):
        from backend.app.models import CapabilityConfig

        db_session.add(CapabilityConfig(
            key="default_models",
            value_json='{"image": {"channel_id": 9, "model": "Tongyi-MAI/Z-Image-Turbo"}}',
        ))
        db_session.commit()

        from backend.app.routers.canvas import _default_model_for

        assert _default_model_for(db_session, "image") == "Tongyi-MAI/Z-Image-Turbo"

    def test_string_format_compatible(self, client, db_session):
        from backend.app.models import CapabilityConfig

        db_session.add(CapabilityConfig(
            key="default_models", value_json='{"video": "MiniMax-Hailuo-2.3"}',
        ))
        db_session.commit()

        from backend.app.routers.canvas import _default_model_for

        assert _default_model_for(db_session, "video") == "MiniMax-Hailuo-2.3"

    def test_fallback_channel_model(self, client, db_session):
        """无 KV 配置 → 域内第一个启用渠道的 display_name 优先。"""
        from backend.app.routers.canvas import _default_model_for

        got = _default_model_for(db_session, "image")
        assert isinstance(got, str) and got


class TestAgentHardening:
    """审计批次二（P2）：限流 + 入队失败清理孤儿节点。"""

    def _fresh_user(self, client):
        import uuid as _u

        name = f"agent_{_u.uuid4().hex[:8]}"
        client.post(
            "/api/auth/register",
            json={
                "username": name, "email": f"{name}@x.io",
                "password": "password123", "invite_code": "letmein",
            },
        )
        r = client.post("/api/auth/login", json={"identifier": name, "password": "password123"})
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    def test_agent_rate_limited(self, client):
        h = self._fresh_user(client)
        pid = _mk_project(client, h)
        key = client.post("/api/settings/api-key", headers=h).json()["api_key"]
        from unittest.mock import patch

        with patch("backend.app.services.task_queue.enqueue_generation_task") as eq:
            eq.return_value = None
            codes = []
            for _ in range(10):
                codes.append(
                    client.post(
                        f"/api/canvas/projects/{pid}/agent",
                        headers={"X-API-Key": key},
                        json={"message": "一只猫"},
                    ).status_code
                )
            assert all(c == 202 for c in codes)
            r = client.post(
                f"/api/canvas/projects/{pid}/agent",
                headers={"X-API-Key": key},
                json={"message": "一只猫"},
            )
        assert r.status_code == 429

    def test_agent_enqueue_failure_cleans_orphan_node(self, client, db_session):
        from unittest.mock import patch

        from backend.app.services.task_queue import QueueUnavailableError

        h = self._fresh_user(client)
        pid = _mk_project(client, h)
        key = client.post("/api/settings/api-key", headers=h).json()["api_key"]

        async def _boom(*a, **k):  # noqa: ANN002, ANN003
            raise QueueUnavailableError("down")

        with patch("backend.app.services.task_queue.enqueue_generation_task", _boom):
            r = client.post(
                f"/api/canvas/projects/{pid}/agent",
                headers={"X-API-Key": key},
                json={"message": "一只狗"},
            )
        assert r.status_code == 503
        proj = client.get(f"/api/canvas/projects/{pid}", headers=h).json()["project"]
        agents = [n for n in proj["nodes"] if n["metadata"].get("canvas_agent")]
        assert agents == [], "入队失败不得遗留孤儿节点"
