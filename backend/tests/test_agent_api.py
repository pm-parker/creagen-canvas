"""Agent API v1（/api/agent/v1 · X-API-Key）：最小可用闭环。

覆盖：key 校验 → 列/建画布 → 列模型 → 生成（含参数透传）→ 轮询任务结果。
旧端点 POST /api/canvas/projects/{id}/agent 保留兼容。
"""

import uuid


def _register_with_key(client, db_session):
    from backend.app.models import User

    name = f"ag_{uuid.uuid4().hex[:8]}"
    client.post(
        "/api/auth/register",
        json={
            "username": name, "email": f"{name}@x.io",
            "password": "password123", "invite_code": "letmein",
        },
    )
    r = client.post("/api/auth/login", json={"identifier": name, "password": "password123"})
    token = r.json()["access_token"]
    u = db_session.query(User).filter(User.username == name).first()
    u.api_key = f"cg-test-{uuid.uuid4().hex[:16]}"
    db_session.commit()
    return {"X-API-Key": u.api_key}, token


def _auth_headers(client, token):
    return {"Authorization": f"Bearer {token}"}


def test_me_validates_key(client, db_session):
    h, _ = _register_with_key(client, db_session)
    r = client.get("/api/agent/v1/me", headers=h)
    assert r.status_code == 200
    assert r.json()["agent_api"] is True
    assert "rate_limit" in r.json()
    # 坏 key 401
    r2 = client.get("/api/agent/v1/me", headers={"X-API-Key": "cg-bogus"})
    assert r2.status_code == 401


def test_projects_list_and_create(client, db_session):
    h, token = _register_with_key(client, db_session)
    web = _auth_headers(client, token)
    client.post("/api/canvas/projects", json={"title": "人类建的项目"}, headers=web)

    r = client.get("/api/agent/v1/projects", headers=h)
    assert r.status_code == 200
    titles = [p["title"] for p in r.json()["projects"]]
    assert "人类建的项目" in titles

    r2 = client.post("/api/agent/v1/projects", json={"title": "Agent 建的"}, headers=h)
    assert r2.status_code == 201
    pid = r2.json()["id"]
    got = client.get("/api/agent/v1/projects", headers=h).json()["projects"]
    assert any(p["id"] == pid for p in got)


def test_models_lists_user_channels(client, db_session):
    from backend.app.models import ProviderChannel, ProviderModel

    h, _ = _register_with_key(client, db_session)
    ch = ProviderChannel(
        domain="image", name="ag-ch", base_url="https://ag.example.com/v1", key_encrypted=""
    )
    db_session.add(ch)
    db_session.flush()
    db_session.add(ProviderModel(channel_id=ch.id, model_id="ag-img", enabled=1, is_free=1))
    # 注意：渠道属 owner（user_id 默认 None=系统渠道）——个人渠道测试更准，但系统渠道已可验证归一逻辑
    db_session.commit()
    r = client.get("/api/agent/v1/models?domain=image", headers=h)
    assert r.status_code == 200
    ids = [m["model_id"] for m in r.json()["models"]]
    assert "ag-img" in ids
    free = next(m for m in r.json()["models"] if m["model_id"] == "ag-img")
    assert free["is_free"] is True


def test_generate_and_poll_full_loop(client, worker_db, db_session):
    """生成 → 轮询到 completed 拿产物 URL（Agent 闭环的最后一环）。"""
    h, token = _register_with_key(client, db_session)
    pid = client.post("/api/agent/v1/projects", json={"title": "闭环"}, headers=h).json()["id"]

    async def fake_gen(t, on_event=None):
        return {"status": "completed", "output_url": "/outputs/images/ok.png"}

    from backend.worker import run_generation_task

    r = client.post(
        "/api/agent/v1/generate",
        json={
            "project_id": pid,
            "type": "image",
            "message": "一只橘猫",
            "params": {"image_count": 2},
        },
        headers=h,
    )
    assert r.status_code == 202, r.text
    task_id = r.json()["task_id"]
    node_cid = r.json()["node_client_id"]

    # 直接跑 worker（测试内同步完成）
    import asyncio

    result = asyncio.run(run_generation_task({}, int(task_id), _gen_override=fake_gen))
    assert result["status"] == "completed"

    r2 = client.get(f"/api/agent/v1/tasks/{task_id}", headers=h)
    assert r2.status_code == 200
    d = r2.json()
    assert d["status"] == "completed"
    assert d["output_url"] == "/outputs/images/ok.png"
    assert d["node_client_id"] == node_cid
    # 任务归属隔离：他人 key 不可见
    h2, _ = _register_with_key(client, db_session)
    r3 = client.get(f"/api/agent/v1/tasks/{task_id}", headers=h2)
    assert r3.status_code == 404


def test_generate_rejects_bad_type_and_missing_project(client, db_session):
    h, _ = _register_with_key(client, db_session)
    r = client.post(
        "/api/agent/v1/generate",
        json={"project_id": 999999, "type": "image", "message": "x"},
        headers=h,
    )
    assert r.status_code == 404
    pid = client.post("/api/agent/v1/projects", json={"title": "p"}, headers=h).json()["id"]
    r2 = client.post(
        "/api/agent/v1/generate",
        json={"project_id": pid, "type": "holo", "message": "x"},
        headers=h,
    )
    assert r2.status_code == 422


def test_skill_downloadable_without_auth(client):
    """Skill 经 Web 路径可下载（无需 key——agent 引导期只有基址）。"""
    r = client.get("/api/agent/v1/skill.md")
    assert r.status_code == 200
    assert "markdown" in r.headers["content-type"]
    assert "attachment" in r.headers.get("content-disposition", "")
    body = r.text
    assert "串联创作技能" in body  # v2.0 标题
    assert "/api/agent/v1/generate" in body
    assert "reference_urls" in body


# ---------- v1.1：Agent 记忆 / 渠道选择 / LLM 结果语义 ----------


def _gen(client, h, pid, type_="image", **extra):
    import uuid as _u

    return client.post(
        "/api/agent/v1/generate",
        json={"project_id": pid, "type": type_, "message": f"m{_u.uuid4().hex[:4]}", **extra},
        headers=h,
    )


def test_tasks_list_recent_and_isolated(client, worker_db, db_session):
    """Agent 记忆①：最近任务列表（倒序 + 归属隔离）。"""
    h, _ = _register_with_key(client, db_session)
    pid = client.post("/api/agent/v1/projects", json={"title": "记忆"}, headers=h).json()["id"]
    t1 = _gen(client, h, pid).json()["task_id"]
    t2 = _gen(client, h, pid).json()["task_id"]
    r = client.get("/api/agent/v1/tasks?limit=10", headers=h)
    assert r.status_code == 200
    ids = [t["id"] for t in r.json()["tasks"]]
    assert ids[0] == t2 and t1 in ids  # 倒序含两单
    # status 过滤
    r2 = client.get("/api/agent/v1/tasks?status=queued", headers=h)
    assert all(t["status"] == "queued" for t in r2.json()["tasks"])
    # 他人 key 看不到
    h2, _ = _register_with_key(client, db_session)
    other = client.get("/api/agent/v1/tasks", headers=h2).json()["tasks"]
    assert all(t["id"] not in (t1, t2) for t in other)


def test_project_nodes_readable(client, worker_db, db_session):
    """Agent 记忆②：读画布节点与产物（跨会话恢复接力）。"""
    h, _ = _register_with_key(client, db_session)
    pid = client.post("/api/agent/v1/projects", json={"title": "节点读"}, headers=h).json()["id"]
    r = _gen(client, h, pid)
    cid = r.json()["node_client_id"]

    nodes = client.get(f"/api/agent/v1/projects/{pid}/nodes", headers=h).json()["nodes"]
    assert len(nodes) >= 1
    n = nodes[0]
    assert n["client_id"] == cid
    assert n["canvas_agent"] is True
    assert "output_url" in n and "prompt" in n and "type" in n
    # 他人 key 404
    h2, _ = _register_with_key(client, db_session)
    assert client.get(f"/api/agent/v1/projects/{pid}/nodes", headers=h2).status_code == 404


def test_generate_first_class_channel_id(client, worker_db, db_session):
    """渠道选择：generate 显式 channel_id 一等字段（同名双模型可指定渠道）。"""
    from backend.app.models import ProviderChannel, ProviderModel

    h, _ = _register_with_key(client, db_session)
    pid = client.post("/api/agent/v1/projects", json={"title": "渠道"}, headers=h).json()["id"]
    ch_a = ProviderChannel(
        domain="image", name="甲", base_url="https://a.example.com/v1", key_encrypted=""
    )
    ch_b = ProviderChannel(
        domain="image", name="乙", base_url="https://b.example.com/v1", key_encrypted=""
    )
    db_session.add_all([ch_a, ch_b])
    db_session.flush()
    db_session.add_all([
        ProviderModel(channel_id=ch_a.id, model_id="dup-m", enabled=1),
        ProviderModel(channel_id=ch_b.id, model_id="dup-m", enabled=1),
    ])
    db_session.commit()

    r = _gen(client, h, pid, model="dup-m", channel_id=ch_b.id)
    assert r.status_code == 202, r.text
    from backend.app.models import Task

    t = db_session.get(Task, r.json()["task_id"])
    assert t.params_dict().get("channel_id") == ch_b.id


def test_models_include_channel_id(client, db_session):
    """models 返回 channel_id（选择渠道的依据）。"""
    from backend.app.models import ProviderChannel, ProviderModel

    h, _ = _register_with_key(client, db_session)
    ch = ProviderChannel(
        domain="llm", name="mch", base_url="https://m.example.com/v1", key_encrypted=""
    )
    db_session.add(ch)
    db_session.flush()
    db_session.add(ProviderModel(channel_id=ch.id, model_id="m-llm", enabled=1))
    db_session.commit()
    r = client.get("/api/agent/v1/models?domain=llm", headers=h)
    m = next(m for m in r.json()["models"] if m["model_id"] == "m-llm")
    assert m["channel_id"] == ch.id


def test_llm_task_returns_content(client, worker_db, db_session):
    """LLM 结果语义：tasks 返回 content 文本字段（output_url 塞文本是历史包袱）。"""
    import asyncio

    h, _ = _register_with_key(client, db_session)
    pid = client.post("/api/agent/v1/projects", json={"title": "llm语义"}, headers=h).json()["id"]
    r = _gen(client, h, pid, type_="llm")
    tid = r.json()["task_id"]

    async def fake_gen(t, on_event=None):
        return {"status": "completed", "output_url": "这是模型生成的文本"}

    from backend.worker import run_generation_task

    asyncio.run(run_generation_task({}, int(tid), _gen_override=fake_gen))
    d = client.get(f"/api/agent/v1/tasks/{tid}", headers=h).json()
    assert d["status"] == "completed"
    assert d["content"] == "这是模型生成的文本"
    # 列表也带
    lst = client.get("/api/agent/v1/tasks?limit=5", headers=h).json()["tasks"]
    row = next(t for t in lst if t["id"] == tid)
    assert row["content"] == "这是模型生成的文本"


def test_models_mark_system_default(client, db_session):
    """系统默认模型可见性：/models 标记 is_default + 顶层 defaults（admin 设置后 agent 可见）。"""
    from backend.app.models import ProviderChannel, ProviderModel, User

    h, _ = _register_with_key(client, db_session)
    # 提权为 admin 以设置默认模型
    u = db_session.query(User).filter(User.username.like("ag_%")).order_by(User.id.desc()).first()
    r0 = client.post("/api/auth/login", json={"identifier": u.username, "password": "password123"})
    admin_h = {"Authorization": f"Bearer {r0.json()['access_token']}"}
    u.role = "admin"
    db_session.commit()

    ch = ProviderChannel(
        domain="image", name="dch", base_url="https://d.example.com/v1", key_encrypted=""
    )
    db_session.add(ch)
    db_session.flush()
    db_session.add(ProviderModel(channel_id=ch.id, model_id="default-img", enabled=1))
    db_session.commit()

    r = client.put(
        "/api/settings/default-models",
        json={"image": {"channel_id": ch.id, "model": "default-img"}},
        headers=admin_h,
    )
    assert r.status_code == 200, r.text

    m = client.get("/api/agent/v1/models?domain=image", headers=h).json()
    row = next(x for x in m["models"] if x["model_id"] == "default-img")
    assert row["is_default"] is True
    assert m["defaults"]["image"]["model"] == "default-img"
    # 非默认模型不带标记
    others = [x for x in m["models"] if x["model_id"] != "default-img"]
    assert all(not x.get("is_default") for x in others)


def test_skill_versioned_and_etag(client):
    """Skill 版本：frontmatter 版本可读 + ETag 304 协商。"""
    import hashlib

    r1 = client.get("/api/agent/v1/skill.md")
    assert r1.status_code == 200
    assert "version:" in r1.text and "updated:" in r1.text  # frontmatter 版本可读
    etag = r1.headers.get("etag")
    assert etag
    body_hash = hashlib.md5(r1.content).hexdigest()
    assert etag == f'"{body_hash}"'

    r2 = client.get("/api/agent/v1/skill.md", headers={"If-None-Match": etag})
    assert r2.status_code == 304


# ---------- v1.3：Agent 接入渠道与模型 ----------


def test_agent_create_channel_with_models(client, db_session):
    """Agent 可代用户接入渠道+模型目录（key 服务端加密，响应仅掩码）。"""
    h, _ = _register_with_key(client, db_session)
    r = client.post(
        "/api/agent/v1/channels",
        json={
            "domain": "audio", "name": "Agent 接的语音", "base_url": "https://sf.example.com/v1",
            "key": "sk-agent-test",
            "models": [{"model_id": "tts-x", "is_free": True}],
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["domain"] == "audio" and d["key_masked"] == "sk-***test"
    assert d["models"][0]["model_id"] == "tts-x" and d["models"][0]["is_free"] is True
    # 归属：他人 key 看不到该渠道
    h2, _ = _register_with_key(client, db_session)
    lst = client.get("/api/agent/v1/channels", headers=h2).json()["channels"]
    assert all(c["id"] != d["id"] for c in lst)
    # 本人可见 + models 端点立即可选
    own = client.get("/api/agent/v1/channels", headers=h).json()["channels"]
    assert any(c["id"] == d["id"] for c in own)
    ms = client.get("/api/agent/v1/models?domain=audio", headers=h).json()["models"]
    assert any(m["model_id"] == "tts-x" for m in ms)


def test_agent_create_channel_validations(client, db_session):
    h, _ = _register_with_key(client, db_session)
    # 非法域
    r1 = client.post(
        "/api/agent/v1/channels",
        json={"domain": "holo", "name": "x", "base_url": "https://a.com/v1"},
        headers=h,
    )
    assert r1.status_code == 422
    # 非法计价（复用共用校验）
    r2 = client.post(
        "/api/agent/v1/channels",
        json={"domain": "image", "name": "x", "base_url": "https://a.com/v1",
              "models": [{"model_id": "m", "pricing_unit": "per_gazillion"}]},
        headers=h,
    )
    assert r2.status_code == 422


# ---------- v1.4：画布组织（节点定位/连线/取帧）——串联创作的地基 ----------


def test_canvas_node_crud_and_edges(client, db_session):
    """零基础 agent 的画布组织：建节点（定点）→ 连线（参考/首帧语义）→ 图回读。"""
    h, _ = _register_with_key(client, db_session)
    pid = client.post("/api/agent/v1/projects", json={"title": "串联"}, headers=h).json()["id"]

    # 定点建两个节点（图 → 视频，纵向工作流）
    n1 = client.post(
        f"/api/agent/v1/projects/{pid}/nodes",
        json={"type": "image", "title": "主角设定", "prompt": "一位红发少女", "x": 0, "y": 0},
        headers=h,
    )
    assert n1.status_code == 201, n1.text
    n2 = client.post(
        f"/api/agent/v1/projects/{pid}/nodes",
        json={
            "type": "video", "title": "开场镜头",
            "prompt": "红发少女走进咖啡馆", "x": 0, "y": 400,
        },
        headers=h,
    )
    id1, id2 = n1.json()["id"], n2.json()["id"]
    assert n1.json()["client_id"].startswith("agent-")

    # 连线（图→视频 = 首帧参考）
    e = client.post(
        f"/api/agent/v1/projects/{pid}/edges",
        json={"from_node_id": id1, "to_node_id": id2},
        headers=h,
    )
    assert e.status_code == 201

    # 图回读：节点带位置，连线可见
    g = client.get(f"/api/agent/v1/projects/{pid}/nodes", headers=h).json()
    assert {n["id"] for n in g["nodes"]} >= {id1, id2}
    assert g["nodes"][0]["x"] == 0
    assert any(ed["from_node_id"] == id1 and ed["to_node_id"] == id2 for ed in g["edges"])

    # PATCH 移动 + 改题
    r = client.patch(
        f"/api/agent/v1/projects/{pid}/nodes/{id2}",
        json={"x": 400, "y": 800, "title": "开场镜头（改）"},
        headers=h,
    )
    assert r.status_code == 200
    g2 = client.get(f"/api/agent/v1/projects/{pid}/nodes", headers=h).json()
    row = next(n for n in g2["nodes"] if n["id"] == id2)
    assert row["x"] == 400 and row["title"] == "开场镜头（改）"

    # DELETE 节点（连带清理其连线）
    assert client.delete(f"/api/agent/v1/projects/{pid}/nodes/{id1}", headers=h).status_code == 200
    g3 = client.get(f"/api/agent/v1/projects/{pid}/nodes", headers=h).json()
    assert all(n["id"] != id1 for n in g3["nodes"])
    assert all(ed["from_node_id"] != id1 for ed in g3["edges"])
    # 他人不可见
    h2, _ = _register_with_key(client, db_session)
    assert client.get(f"/api/agent/v1/projects/{pid}/nodes", headers=h2).status_code == 404


def test_generate_with_position(client, worker_db, db_session):
    """generate 支持定点投放（x/y）——串联创作的布局自由。"""
    h, _ = _register_with_key(client, db_session)
    pid = client.post("/api/agent/v1/projects", json={"title": "定点"}, headers=h).json()["id"]
    r = _gen(client, h, pid, params={"x": 1200, "y": 3600})
    assert r.status_code == 202
    g = client.get(f"/api/agent/v1/projects/{pid}/nodes", headers=h).json()
    assert g["nodes"][0]["x"] == 1200 and g["nodes"][0]["y"] == 3600


def test_frame_extraction_for_relay(client, worker_db, db_session, tmp_path, monkeypatch):
    """尾帧接力地基：agent 提取视频帧（time=-1 取末帧）→ 站内路径可作 reference_urls。"""
    h, _ = _register_with_key(client, db_session)
    # 造一个极小 mp4（ffmpeg 生成 0.5s 黑帧）放 outputs
    import subprocess

    from backend.app.services import media_service

    vid = tmp_path / "relay.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=black:size=64x64:rate=10",
         "-t", "0.5", str(vid)], check=True,
    )
    monkeypatch.setattr(media_service, "_outputs_root_default", lambda: tmp_path)
    (tmp_path / "videos").mkdir(parents=True, exist_ok=True)
    (tmp_path / "videos" / "relay.mp4").write_bytes(vid.read_bytes())

    r = client.post(
        "/api/agent/v1/frame",
        json={"url": "/outputs/videos/relay.mp4", "time": -1},
        headers=h,
    )
    assert r.status_code == 200, r.text
    out = r.json()["url"]
    assert out.startswith("/outputs/media/frame_")
