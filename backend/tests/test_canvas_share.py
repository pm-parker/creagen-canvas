import json

"""画布只读分享 + fork（P0）：公开链接、脱敏、复制到自己名下。"""

REGISTER = {
    "username": "shareowner",
    "email": "share@example.com",
    "password": "password123",
    "invite_code": "letmein",
}
REGISTER_B = {
    "username": "shareviewer",
    "email": "viewer@example.com",
    "password": "password123",
    "invite_code": "letmein",
}


def _headers(client, reg=REGISTER):
    client.post("/api/auth/register", json=reg)
    r = client.post(
        "/api/auth/login", json={"identifier": reg["username"], "password": reg["password"]}
    )
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _mk_project(client, headers, title="分享测试画布"):
    """建画布 + 2 节点（走真实同步 API 形状）+ 1 连线。"""
    r = client.post("/api/canvas/projects", json={"title": title}, headers=headers)
    pid = r.json()["project"]["id"]
    client.put(
        f"/api/canvas/projects/{pid}/nodes",
        json={"nodes": [
            {
                "type": "image", "title": "猫", "position_x": 0, "position_y": 0,
                "metadata_json": json.dumps({
                    "client_id": "c1", "prompt": "一只猫", "outputUrl": "/outputs/cat.png",
                }, ensure_ascii=False),
            },
            {
                "type": "text", "title": "文案", "position_x": 300, "position_y": 0,
                "metadata_json": json.dumps({"client_id": "c2", "prompt": "写文案"}),
            },
        ]},
        headers=headers,
    )
    proj = client.get(f"/api/canvas/projects/{pid}", headers=headers).json()["project"]
    ids = [n["id"] for n in proj["nodes"]]
    if len(ids) == 2:
        client.put(
            f"/api/canvas/projects/{pid}/connections",
            json={"connections": [{"from_node_id": ids[0], "to_node_id": ids[1]}]},
            headers=headers,
        )
    return pid


class TestShareLifecycle:
    def test_share_on_off(self, client, db_session):
        h = _headers(client)
        pid = _mk_project(client, h)
        # 未分享：to_dict 无 token
        d = client.get(f"/api/canvas/projects/{pid}", headers=h).json()["project"]
        assert d["share_token"] is None

        # 开启
        r = client.post(f"/api/canvas/projects/{pid}/share", headers=h)
        assert r.status_code == 200
        token = r.json()["share_token"]
        assert len(token) >= 8

        # 公开只读（无 Authorization）
        r = client.get(f"/api/canvas/shared/{token}")
        assert r.status_code == 200
        pub = r.json()
        assert pub["project"]["title"] == "分享测试画布"
        assert "user_id" not in pub["project"]  # 脱敏
        assert len(pub["nodes"]) == 2
        assert len(pub["connections"]) == 1
        assert pub["author"]  # 作者名保留

        # 关闭 → 链接立即失效
        r = client.delete(f"/api/canvas/projects/{pid}/share", headers=h)
        assert r.status_code == 200
        assert client.get(f"/api/canvas/shared/{token}").status_code == 404

    def test_share_token_stable_on_reopen(self, client, db_session):
        """再次开启生成新 token（旧 token 已废，不能复活）。"""
        h = _headers(client)
        pid = _mk_project(client, h)
        t1 = client.post(f"/api/canvas/projects/{pid}/share", headers=h).json()["share_token"]
        client.delete(f"/api/canvas/projects/{pid}/share", headers=h)
        t2 = client.post(f"/api/canvas/projects/{pid}/share", headers=h).json()["share_token"]
        assert t1 != t2
        assert client.get(f"/api/canvas/shared/{t1}").status_code == 404

    def test_other_cannot_manage_share(self, client, db_session):
        h = _headers(client)
        pid = _mk_project(client, h)
        hb = _headers(client, REGISTER_B)
        assert client.post(f"/api/canvas/projects/{pid}/share", headers=hb).status_code == 404
        assert client.delete(f"/api/canvas/projects/{pid}/share", headers=hb).status_code == 404

    def test_random_token_404(self, client, db_session):
        _headers(client)
        assert client.get("/api/canvas/shared/nonexistent123").status_code == 404

    def test_trashed_project_not_shared(self, client, db_session):
        """进回收站的画布：分享链接立即失效。"""
        h = _headers(client)
        pid = _mk_project(client, h)
        token = client.post(f"/api/canvas/projects/{pid}/share", headers=h).json()["share_token"]
        client.delete(f"/api/canvas/projects/{pid}", headers=h)
        assert client.get(f"/api/canvas/shared/{token}").status_code == 404


class TestFork:
    def test_fork_copies_to_viewer(self, client, db_session):
        h = _headers(client)
        pid = _mk_project(client, h, title="原作")
        token = client.post(f"/api/canvas/projects/{pid}/share", headers=h).json()["share_token"]

        hb = _headers(client, REGISTER_B)
        r = client.post(f"/api/canvas/shared/{token}/fork", headers=hb)
        assert r.status_code == 201
        new_p = r.json()["project"]
        assert new_p["title"] == "原作 - 副本"
        assert new_p["id"] != pid

        # 副本在 viewer 名下，节点/连线齐全
        d = client.get(f"/api/canvas/projects/{new_p['id']}", headers=hb).json()["project"]
        assert len(d["nodes"]) == 2
        assert len(d["connections"]) == 1

        # owner 看不到 viewer 的副本
        assert client.get(f"/api/canvas/projects/{new_p['id']}", headers=h).status_code == 404

    def test_fork_unshared_404(self, client, db_session):
        h = _headers(client)
        _mk_project(client, h)  # 未分享
        hb = _headers(client, REGISTER_B)
        r = client.post("/api/canvas/shared/nonexistent/fork", headers=hb)
        assert r.status_code == 404
