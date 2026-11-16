"""画布保存数据丢失回归（PR6）：重载保存保持 id/尺寸/服务端元数据 + 并发保护。

复现的 bug：节点首次保存带 metadata.client_id 与服务端写入字段（extraUrls/
durationSec）及 width/height；页面重载后前端把数据库 id 当 client_id 回传且只带
metadata 子集 → 后端删旧建新：id 漂移、尺寸与服务端元数据丢失、Agent 建的节点被删。
"""

import json

REGISTER = {
    "username": "savefix",
    "email": "savefix@example.com",
    "password": "password123",
    "invite_code": "letmein",
}


def _headers(client) -> dict[str, str]:
    r = client.post("/api/auth/register", json=REGISTER)
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _project(client, headers) -> int:
    r = client.post("/api/canvas/projects", json={"title": "保存回归"}, headers=headers)
    return r.json()["project"]["id"]


def _put(client, headers, pid, nodes, base_revision=None):
    body: dict = {"nodes": nodes}
    if base_revision is not None:
        body["base_revision"] = base_revision
    return client.put(f"/api/canvas/projects/{pid}/nodes", json=body, headers=headers)


def _node(cid, x=0, y=0, width=None, height=None, meta=None, prompt="p"):
    m = {"prompt": prompt, "client_id": cid}
    if meta:
        m.update(meta)
    return {
        "type": "image",
        "title": f"节点{cid}",
        "position_x": x,
        "position_y": y,
        "width": width,
        "height": height,
        "metadata_json": json.dumps(m, ensure_ascii=False),
    }


class TestReloadSaveKeepsData:
    """(a)(b)：重载后回传 client_id → 原地更新，不丢 id/尺寸/服务端元数据。"""

    def test_reload_save_keeps_id_size_and_server_metadata(self, client, db_session):
        from backend.app.models import CanvasNode

        headers = _headers(client)
        pid = _project(client, headers)
        r = _put(client, headers, pid, [_node("uuid-1", width=280, height=180)], base_revision=0)
        assert r.status_code == 200
        node_id = r.json()["nodes"][0]["id"]
        revision = r.json()["revision"]

        # 服务端写入字段（Agent 回写：产物 URL / 备选 / 时长）
        node = db_session.get(CanvasNode, node_id)
        meta = json.loads(node.metadata_json)
        meta["outputUrl"] = "/outputs/main.png"
        meta["extraUrls"] = ["/outputs/main.png", "/outputs/extra.png"]
        meta["durationSec"] = 6
        node.metadata_json = json.dumps(meta, ensure_ascii=False)
        db_session.commit()

        # 前端重载后回传：client_id 命中 + 只带 metadata 子集（outputUrl=null）
        payload = _node("uuid-1", width=280, height=180)
        payload["metadata_json"] = json.dumps(
            {"prompt": "p", "outputUrl": None, "client_id": "uuid-1"}, ensure_ascii=False
        )
        r = _put(client, headers, pid, [payload], base_revision=revision)
        assert r.status_code == 200, r.text
        assert len(r.json()["nodes"]) == 1
        out = r.json()["nodes"][0]
        # 数据库 id 不变（不再删旧建新）
        assert out["id"] == node_id
        # 尺寸保留
        assert out["width"] == 280
        assert out["height"] == 180
        # 服务端写入的元数据保留（客户端 null 不得清空）
        assert out["metadata"]["extraUrls"] == ["/outputs/main.png", "/outputs/extra.png"]
        assert out["metadata"]["durationSec"] == 6
        assert out["metadata"]["outputUrl"] == "/outputs/main.png"

    def test_missing_width_height_keeps_existing(self, client):
        headers = _headers(client)
        pid = _project(client, headers)
        r = _put(client, headers, pid, [_node("uuid-1", width=280, height=180)], base_revision=0)
        node_id = r.json()["nodes"][0]["id"]
        r = _put(client, headers, pid, [_node("uuid-1")], base_revision=1)
        out = r.json()["nodes"][0]
        assert out["id"] == node_id
        assert out["width"] == 280
        assert out["height"] == 180

    def test_legacy_client_sending_db_id_updates_in_place(self, client):
        """旧客户端把数据库 id 当 client_id 回传 → 也命中同一节点（原地更新）。"""
        headers = _headers(client)
        pid = _project(client, headers)
        r = _put(client, headers, pid, [_node("uuid-1")], base_revision=0)
        node_id = r.json()["nodes"][0]["id"]
        legacy = {
            "type": "image",
            "title": "旧客户端",
            "position_x": 3,
            "position_y": 4,
            "metadata_json": json.dumps({"prompt": "p2", "client_id": str(node_id)}),
        }
        r = _put(client, headers, pid, [legacy], base_revision=1)
        assert r.status_code == 200
        assert len(r.json()["nodes"]) == 1
        assert r.json()["nodes"][0]["id"] == node_id
        assert r.json()["nodes"][0]["position"]["x"] == 3


class TestDeletionScope:
    """(b)：客户端保存只删"客户端本就知道"的节点，服务端新建的节点不被删。"""

    def test_agent_node_not_deleted_by_client_save(self, client, db_session):
        from backend.app.models import CanvasNode

        headers = _headers(client)
        pid = _project(client, headers)
        r = _put(client, headers, pid, [_node("c1")], base_revision=0)
        revision = r.json()["revision"]

        # 服务端 Agent 建的节点（canvas_agent 标记）
        db_session.add(
            CanvasNode(
                project_id=pid,
                type="image",
                title="Agent 节点",
                metadata_json=json.dumps(
                    {"client_id": "agent-abc", "canvas_agent": True}, ensure_ascii=False
                ),
            )
        )
        db_session.commit()

        r = _put(client, headers, pid, [_node("c1")], base_revision=revision)
        assert r.status_code == 200
        cids = [n["metadata"].get("client_id") for n in r.json()["nodes"]]
        assert "agent-abc" in cids

    def test_node_created_after_base_revision_survives(self, client, db_session):
        """客户端加载之后服务端新建的节点（_created_rev 晚于 base_revision）不被删。"""
        from backend.app.models import CanvasNode

        headers = _headers(client)
        pid = _project(client, headers)
        r = _put(client, headers, pid, [_node("c1")], base_revision=0)
        revision = r.json()["revision"]

        db_session.add(
            CanvasNode(
                project_id=pid,
                type="image",
                title="后建节点",
                metadata_json=json.dumps(
                    {"client_id": "srv-late", "_created_rev": revision + 5}, ensure_ascii=False
                ),
            )
        )
        db_session.commit()

        r = _put(client, headers, pid, [_node("c1")], base_revision=revision)
        cids = [n["metadata"].get("client_id") for n in r.json()["nodes"]]
        assert "srv-late" in cids

    def test_known_node_deleted_when_omitted(self, client):
        """客户端本就知道、且本次未提交的节点 → 正常删除（用户删节点）。"""
        headers = _headers(client)
        pid = _project(client, headers)
        r = _put(client, headers, pid, [_node("c1"), _node("c2")], base_revision=0)
        revision = r.json()["revision"]
        r = _put(client, headers, pid, [_node("c1")], base_revision=revision)
        cids = [n["metadata"].get("client_id") for n in r.json()["nodes"]]
        assert cids == ["c1"]


class TestMetadataValidation:
    """(d)：合法 JSON 但非对象 → 422（不 500）。"""

    def test_non_object_metadata_returns_422(self, client):
        headers = _headers(client)
        pid = _project(client, headers)
        for raw in ("[]", "1", '"x"', "null"):
            r = _put(
                client,
                headers,
                pid,
                [{"type": "text", "title": "n", "metadata_json": raw}],
            )
            assert r.status_code == 422, f"{raw} → {r.status_code}"
            assert r.json()["detail"]["code"] == "METADATA_INVALID"

    def test_invalid_json_still_422(self, client):
        headers = _headers(client)
        pid = _project(client, headers)
        r = _put(
            client,
            headers,
            pid,
            [{"type": "text", "title": "n", "metadata_json": "not-json"}],
        )
        assert r.status_code == 422


class TestRevisionGuards:
    """(f)：快照恢复 bump revision → 其他客户端拿到 409 而非静默覆盖。"""

    def test_snapshot_restore_bumps_revision(self, client):
        headers = _headers(client)
        pid = _project(client, headers)
        _put(client, headers, pid, [_node("c1")], base_revision=0)
        snap = client.post(
            f"/api/canvas/projects/{pid}/snapshots", json={"label": "v1"}, headers=headers
        ).json()["snapshot"]
        r = _put(client, headers, pid, [_node("c2")], base_revision=1)
        revision_before = r.json()["revision"]

        r = client.post(
            f"/api/canvas/projects/{pid}/snapshots/{snap['id']}/restore", headers=headers
        )
        assert r.status_code == 200
        r = client.get(f"/api/canvas/projects/{pid}", headers=headers)
        assert r.json()["project"]["revision"] > revision_before

    def test_stale_client_save_after_restore_conflicts(self, client):
        headers = _headers(client)
        pid = _project(client, headers)
        _put(client, headers, pid, [_node("c1")], base_revision=0)
        snap = client.post(
            f"/api/canvas/projects/{pid}/snapshots", json={"label": "v1"}, headers=headers
        ).json()["snapshot"]
        client.post(f"/api/canvas/projects/{pid}/snapshots/{snap['id']}/restore", headers=headers)
        # 客户端仍持旧 revision → 409（不静默覆盖恢复后的状态）
        r = _put(client, headers, pid, [_node("c1")], base_revision=0)
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "REVISION_CONFLICT"

    def test_restore_keeps_edges_valid(self, client):
        """恢复后连线端点随节点重建映射（不悬空）。"""
        headers = _headers(client)
        pid = _project(client, headers)
        r = _put(client, headers, pid, [_node("c1"), _node("c2")], base_revision=0)
        ids = [n["id"] for n in r.json()["nodes"]]
        client.put(
            f"/api/canvas/projects/{pid}/connections",
            json={"connections": [{"from_node_id": ids[0], "to_node_id": ids[1]}]},
            headers=headers,
        )
        snap = client.post(
            f"/api/canvas/projects/{pid}/snapshots", json={"label": "v1"}, headers=headers
        ).json()["snapshot"]
        # 恢复前再改一次（让节点 id 漂移）
        _put(client, headers, pid, [_node("c3")], base_revision=1)
        r = client.post(
            f"/api/canvas/projects/{pid}/snapshots/{snap['id']}/restore", headers=headers
        )
        assert r.status_code == 200
        body = r.json()
        node_ids = {n["id"] for n in body["nodes"]}
        assert len(node_ids) == 2
        for c in body["connections"]:
            assert c["from_node_id"] in node_ids
            assert c["to_node_id"] in node_ids


class TestWritebackMatching:
    """(c)：回写按 client_id 字段比对（解析 JSON），不做原始字符串子串匹配。"""

    class _FakeTask:
        id = 999

        def __init__(self, params):
            self._params = params

        def params_dict(self):
            return self._params

    def test_writeback_matches_compact_json_metadata(self, client, db_session):
        from backend.app.models import CanvasNode
        from backend.app.services.task_lifecycle import _writeback_canvas_node

        headers = _headers(client)
        pid = _project(client, headers)
        node = CanvasNode(
            project_id=pid,
            type="image",
            title="紧凑 JSON",
            # 客户端重存的紧凑 JSON（无空格）——旧的子串匹配会漏掉
            metadata_json=json.dumps({"client_id": "cid-x"}, separators=(",", ":")),
        )
        db_session.add(node)
        db_session.commit()
        node_id = node.id

        task = self._FakeTask({"canvas_target": {"project_id": pid, "node_client_id": "cid-x"}})
        _writeback_canvas_node(
            db_session,
            task,
            {
                "status": "completed",
                "output_url": "/outputs/x.png",
                "extra_urls": ["/outputs/y.png"],
                "meta": {"duration": 7},
            },
        )
        db_session.expire_all()
        meta = json.loads(db_session.get(CanvasNode, node_id).metadata_json)
        assert meta["outputUrl"] == "/outputs/x.png"
        assert meta["extraUrls"] == ["/outputs/x.png", "/outputs/y.png"]
        assert meta["durationSec"] == 7

    def test_writeback_does_not_match_similar_client_id(self, client, db_session):
        from backend.app.models import CanvasNode
        from backend.app.services.task_lifecycle import _writeback_canvas_node

        headers = _headers(client)
        pid = _project(client, headers)
        node = CanvasNode(
            project_id=pid,
            type="image",
            title="相似 id",
            metadata_json=json.dumps({"client_id": "cid-x-other"}, ensure_ascii=False),
        )
        db_session.add(node)
        db_session.commit()
        node_id = node.id

        task = self._FakeTask({"canvas_target": {"project_id": pid, "node_client_id": "cid-x"}})
        _writeback_canvas_node(
            db_session, task, {"status": "completed", "output_url": "/outputs/x.png"}
        )
        db_session.expire_all()
        meta = json.loads(db_session.get(CanvasNode, node_id).metadata_json)
        assert "outputUrl" not in meta
