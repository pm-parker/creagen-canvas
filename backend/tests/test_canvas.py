"""画布域契约：项目 CRUD / 节点连线同步 / 用户隔离 / 健康检查。"""

REGISTER = {
    "username": "canvaser",
    "email": "canvas@example.com",
    "password": "password123",
    "invite_code": "letmein",
}


def _headers(client) -> dict[str, str]:
    r = client.post("/api/auth/register", json=REGISTER)
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["app"] == "creagen-canvas"


def test_project_crud_and_bulk_sync(client):
    headers = _headers(client)

    r = client.post("/api/canvas/projects", json={"title": "测试画布"}, headers=headers)
    assert r.status_code == 201
    pid = r.json()["project"]["id"]

    nodes = [
        {
            "type": "text",
            "title": "提示词",
            "position_x": 100,
            "position_y": 200,
            "metadata_json": '{"prompt": "a cat"}',
        },
        {
            "type": "image",
            "title": "生成图",
            "position_x": 400,
            "position_y": 200,
            "metadata_json": "{}",
        },
    ]
    r = client.put(f"/api/canvas/projects/{pid}/nodes", json={"nodes": nodes}, headers=headers)
    assert r.status_code == 200
    node_ids = [n["id"] for n in r.json()["nodes"]]
    assert len(node_ids) == 2

    conns = [{"from_node_id": node_ids[0], "to_node_id": node_ids[1]}]
    r = client.put(
        f"/api/canvas/projects/{pid}/connections", json={"connections": conns}, headers=headers
    )
    assert r.status_code == 200
    assert len(r.json()["connections"]) == 1

    r = client.get(f"/api/canvas/projects/{pid}", headers=headers)
    body = r.json()["project"]
    assert body["title"] == "测试画布"
    assert len(body["nodes"]) == 2
    assert body["nodes"][0]["metadata"]["prompt"] == "a cat"
    assert body["connections"][0]["from_node_id"] == node_ids[0]

    r = client.put(f"/api/canvas/projects/{pid}", json={"title": "改名画布"}, headers=headers)
    assert r.json()["project"]["title"] == "改名画布"


def test_node_bulk_sync_is_full_replace(client):
    headers = _headers(client)
    pid = client.post("/api/canvas/projects", json={}, headers=headers).json()["project"]["id"]
    client.put(
        f"/api/canvas/projects/{pid}/nodes",
        json={"nodes": [{"type": "text", "position_x": 0, "position_y": 0}]},
        headers=headers,
    )
    r = client.put(
        f"/api/canvas/projects/{pid}/nodes",
        json={"nodes": [{"type": "image", "position_x": 1, "position_y": 1}]},
        headers=headers,
    )
    assert len(r.json()["nodes"]) == 1
    assert r.json()["nodes"][0]["type"] == "image"


def test_project_isolated_between_users(client):
    import uuid

    tag = uuid.uuid4().hex[:6]
    r1 = client.post(
        "/api/auth/register",
        json={
            "username": f"u1{tag}",
            "email": f"u1{tag}@p.com",
            "password": "password123",
            "invite_code": "letmein",
        },
    )
    h1 = {"Authorization": f"Bearer {r1.json()['access_token']}"}
    pid = client.post("/api/canvas/projects", json={}, headers=h1).json()["project"]["id"]

    r2 = client.post(
        "/api/auth/register",
        json={
            "username": f"u2{tag}",
            "email": f"u2{tag}@p.com",
            "password": "password123",
            "invite_code": "letmein",
        },
    )
    h2 = {"Authorization": f"Bearer {r2.json()['access_token']}"}

    assert client.get(f"/api/canvas/projects/{pid}", headers=h2).status_code == 404
    assert client.delete(f"/api/canvas/projects/{pid}", headers=h2).status_code == 404


def test_delete_project(client):
    headers = _headers(client)
    pid = client.post("/api/canvas/projects", json={}, headers=headers).json()["project"]["id"]
    assert client.delete(f"/api/canvas/projects/{pid}", headers=headers).status_code == 204
    r = client.get("/api/canvas/projects", headers=headers)
    assert r.json()["projects"] == []


def test_invalid_metadata_json_rejected_not_poisoned(client):
    """审计回归：非法 metadata_json 422 拒绝，不落毒数据。"""
    headers = _headers(client)
    pid = client.post("/api/canvas/projects", json={}, headers=headers).json()["project"]["id"]
    r = client.put(
        f"/api/canvas/projects/{pid}/nodes",
        json={
            "nodes": [
                {"type": "text", "position_x": 0, "position_y": 0, "metadata_json": "not-json"}
            ]
        },
        headers=headers,
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "METADATA_INVALID"
    # 画布仍可正常读写
    r = client.get(f"/api/canvas/projects/{pid}", headers=headers)
    assert r.status_code == 200


def test_legacy_corrupted_metadata_does_not_500(client, db_session):
    """审计回归：历史脏 metadata 读回不 500（容错为空 dict）。"""
    from backend.app.models import CanvasNode

    headers = _headers(client)
    p = client.post("/api/canvas/projects", json={}, headers=headers).json()["project"]
    node = CanvasNode(project_id=p["id"], type="text", metadata_json="not-json")
    db_session.add(node)
    db_session.commit()
    r = client.get(f"/api/canvas/projects/{p['id']}", headers=headers)
    assert r.status_code == 200
    assert r.json()["project"]["nodes"][0]["metadata"] == {}


def _create_project(client, headers, title="回收站测试") -> int:
    r = client.post("/api/canvas/projects", json={"title": title}, headers=headers)
    return r.json()["project"]["id"]


def test_trash_flow_soft_delete_restore_purge(client, db_session):
    """回收站三段式：删除=软删（列表消失/回收站可见）→ 恢复 → 彻底删除。"""
    headers = _headers(client)
    pid = _create_project(client, headers)

    # 删除 → 软删
    r = client.delete(f"/api/canvas/projects/{pid}", headers=headers)
    assert r.status_code == 204
    r = client.get("/api/canvas/projects", headers=headers)
    assert all(p["id"] != pid for p in r.json()["projects"])
    r = client.get("/api/canvas/projects/trash", headers=headers)
    assert any(p["id"] == pid for p in r.json()["projects"])
    # 软删后详情 404（防误编辑）
    r = client.get(f"/api/canvas/projects/{pid}", headers=headers)
    assert r.status_code == 404

    # 恢复
    r = client.post(f"/api/canvas/projects/{pid}/restore", headers=headers)
    assert r.status_code == 200
    r = client.get("/api/canvas/projects", headers=headers)
    assert any(p["id"] == pid for p in r.json()["projects"])

    # 再删 → 彻底删除（需在回收站中）
    client.delete(f"/api/canvas/projects/{pid}", headers=headers)
    r = client.delete(f"/api/canvas/projects/{pid}", headers=headers)
    assert r.status_code == 204
    r = client.get("/api/canvas/projects/trash", headers=headers)
    assert all(p["id"] != pid for p in r.json()["projects"])
    from backend.app.models import CanvasProject

    assert db_session.get(CanvasProject, pid) is None


def test_purge_requires_trashed(client):
    """未软删的项目不能直接 purge（防误触彻底删除）。"""
    headers = _headers(client)
    pid = _create_project(client, headers)
    r = client.delete(f"/api/canvas/projects/{pid}/purge", headers=headers)
    assert r.status_code == 404


def test_trash_isolated_between_users(client):
    a = _headers(client)
    REGISTER_OTHER = {
        "username": "trashother",
        "email": "trashother@x.io",
        "password": "password123",
        "invite_code": "letmein",
    }
    r = client.post("/api/auth/register", json=REGISTER_OTHER)
    b = {"Authorization": f"Bearer {r.json()['access_token']}"}
    pid = _create_project(client, a, "别人的")
    client.delete(f"/api/canvas/projects/{pid}", headers=a)
    r = client.get("/api/canvas/projects/trash", headers=b)
    assert all(p["id"] != pid for p in r.json()["projects"])


class TestSyncNodesUpsert:
    """H1+M6：client_id 稳定 upsert + revision 乐观锁。"""

    def _put(self, client, headers, pid, nodes, base_revision=None):
        body: dict = {"nodes": nodes}
        if base_revision is not None:
            body["base_revision"] = base_revision
        return client.put(
            f"/api/canvas/projects/{pid}/nodes", json=body, headers=headers
        )

    def _node(self, cid, x=0):
        import json as _json

        return {
            "type": "text",
            "title": "n",
            "position_x": x,
            "position_y": 0,
            "metadata_json": _json.dumps({"prompt": "p", "client_id": cid}),
        }

    def test_upsert_keeps_ids_and_increments_revision(self, client):
        headers = _headers(client)
        pid = _create_project(client, headers, "upsert")

        r1 = self._put(client, headers, pid, [self._node("c1")], base_revision=0)
        assert r1.status_code == 200
        id1 = r1.json()["nodes"][0]["id"]
        assert r1.json()["revision"] == 1

        # 同 client_id 再存：id 不变（churn 修复），仅更新
        r2 = self._put(client, headers, pid, [self._node("c1", x=99)], base_revision=1)
        assert r2.status_code == 200
        assert r2.json()["nodes"][0]["id"] == id1
        assert r2.json()["nodes"][0]["position"]["x"] == 99
        assert r2.json()["revision"] == 2

    def test_removed_client_nodes_deleted(self, client):
        headers = _headers(client)
        pid = _create_project(client, headers, "del")
        self._put(client, headers, pid, [self._node("c1"), self._node("c2")], base_revision=0)
        r = self._put(client, headers, pid, [self._node("c1")], base_revision=1)
        cids = [n["metadata"].get("client_id") for n in r.json()["nodes"]]
        assert cids == ["c1"]

    def test_stale_revision_conflicts_409(self, client):
        headers = _headers(client)
        pid = _create_project(client, headers, "conflict")
        self._put(client, headers, pid, [self._node("c1")], base_revision=0)
        # 旧 revision 重放 → 409 + 当前 revision
        r = self._put(client, headers, pid, [self._node("c2")], base_revision=0)
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "REVISION_CONFLICT"
        assert r.json()["detail"]["current_revision"] == 1

    def test_new_client_nodes_inserted_without_base_revision(self, client):
        """不带 base_revision（外部工具/旧客户端）：直接应用不校验。"""
        headers = _headers(client)
        pid = _create_project(client, headers, "nobase")
        r = self._put(client, headers, pid, [self._node("c1")])
        assert r.status_code == 200
        assert r.json()["revision"] >= 1


def test_list_projects_includes_node_count_and_thumb(client):
    """列表卡片重构（0.7.1）：返回 node_count 与首图缩略图，避免前端 N+1。"""
    headers = _headers(client)
    pid = client.post(
        "/api/canvas/projects", json={"title": "卡片画布"}, headers=headers
    ).json()["project"]["id"]

    # 空画布：node_count=0、无缩略图
    r = client.get("/api/canvas/projects", headers=headers)
    empty = next(p for p in r.json()["projects"] if p["id"] == pid)
    assert empty["node_count"] == 0
    assert empty["thumb_url"] is None

    nodes = [
        {
            "type": "text",
            "title": "提示词",
            "position_x": 0,
            "position_y": 0,
            "metadata_json": '{"prompt": "a cat"}',
        },
        {
            "type": "image",
            "title": "生成图",
            "position_x": 300,
            "position_y": 0,
            "metadata_json": '{"outputUrl": "/outputs/images/img_x.png"}',
        },
    ]
    r = client.put(f"/api/canvas/projects/{pid}/nodes", json={"nodes": nodes}, headers=headers)
    assert r.status_code == 200

    r = client.get("/api/canvas/projects", headers=headers)
    card = next(p for p in r.json()["projects"] if p["id"] == pid)
    assert card["node_count"] == 2
    assert card["thumb_url"] == "/outputs/images/img_x.png"


def test_sync_connections_rejects_foreign_node_ids(client):
    """审计批次三（P3）：连线端点未校验 node 归属——跨项目 node_id 可写入悬空连线。"""
    import uuid

    uname = f"cconn_{uuid.uuid4().hex[:6]}"
    client.post(
        "/api/auth/register",
        json={
            "username": uname, "email": f"{uname}@x.io",
            "password": "password123", "invite_code": "letmein",
        },
    )
    tok = client.post(
        "/api/auth/login", json={"identifier": uname, "password": "password123"}
    ).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    pid = client.post(
        "/api/canvas/projects", json={"title": "A"}, headers=h
    ).json()["project"]["id"]

    r = client.put(
        f"/api/canvas/projects/{pid}/connections",
        json={"connections": [{"from_node_id": 99999, "to_node_id": 88888}]},
        headers=h,
    )
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "NODE_INVALID"
