"""版本快照契约（M2-5，对标 BeefTV 画布保存保护与版本记录）。"""


def _setup(client):
    r = client.post(
        "/api/auth/register",
        json={
            "username": "snapper",
            "email": "snap@x.io",
            "password": "password123",
            "invite_code": "letmein",
        },
    )
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    pid = client.post("/api/canvas/projects", json={}, headers=headers).json()["project"]["id"]
    return headers, pid


def test_snapshot_create_list_restore(client):
    headers, pid = _setup(client)
    # 写入初始节点
    client.put(
        f"/api/canvas/projects/{pid}/nodes",
        json={"nodes": [{"type": "text", "title": "v1", "position_x": 0, "position_y": 0}]},
        headers=headers,
    )
    # 快照 v1
    r = client.post(
        f"/api/canvas/projects/{pid}/snapshots", json={"label": "第一版"}, headers=headers
    )
    assert r.status_code == 201
    assert r.json()["snapshot"]["version"] == 1

    # 改动画布
    client.put(
        f"/api/canvas/projects/{pid}/nodes",
        json={"nodes": [{"type": "image", "title": "v2", "position_x": 5, "position_y": 5}]},
        headers=headers,
    )

    # 恢复 v1 → 节点回到 v1
    snaps = client.get(f"/api/canvas/projects/{pid}/snapshots", headers=headers).json()["snapshots"]
    assert snaps[0]["version"] == 1
    r = client.post(
        f"/api/canvas/projects/{pid}/snapshots/{snaps[0]['id']}/restore", headers=headers
    )
    assert r.status_code == 200
    assert r.json()["restored"] is True
    nodes = r.json()["nodes"]
    assert len(nodes) == 1 and nodes[0]["title"] == "v1"
    # 恢复前自动建了安全网快照
    snaps = client.get(f"/api/canvas/projects/{pid}/snapshots", headers=headers).json()["snapshots"]
    assert len(snaps) == 2
    assert snaps[0]["label"].startswith("恢复前")


def test_snapshot_rolling_keep_20(client, db_session):
    headers, pid = _setup(client)
    for i in range(23):
        client.post(
            f"/api/canvas/projects/{pid}/snapshots", json={"label": f"v{i}"}, headers=headers
        )
    snaps = client.get(f"/api/canvas/projects/{pid}/snapshots", headers=headers).json()["snapshots"]
    assert len(snaps) <= 20


def test_snapshot_isolated_between_users(client):
    import uuid

    tag = uuid.uuid4().hex[:6]
    r1 = client.post(
        "/api/auth/register",
        json={
            "username": f"s1{tag}",
            "email": f"s1{tag}@x.io",
            "password": "password123",
            "invite_code": "letmein",
        },
    )
    h1 = {"Authorization": f"Bearer {r1.json()['access_token']}"}
    pid = client.post("/api/canvas/projects", json={}, headers=h1).json()["project"]["id"]
    client.post(f"/api/canvas/projects/{pid}/snapshots", json={}, headers=h1)

    r2 = client.post(
        "/api/auth/register",
        json={
            "username": f"s2{tag}",
            "email": f"s2{tag}@x.io",
            "password": "password123",
            "invite_code": "letmein",
        },
    )
    h2 = {"Authorization": f"Bearer {r2.json()['access_token']}"}
    assert client.get(f"/api/canvas/projects/{pid}/snapshots", headers=h2).status_code == 404
