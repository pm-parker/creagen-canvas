"""提示词路由契约：错误必须真正抛出（BUG-D：_bad 曾被 return 而非 raise）。"""
import json

REGISTER = {
    "username": "promptuser",
    "email": "prompt@example.com",
    "password": "password123",
    "invite_code": "letmein",
}


def _headers(client) -> dict[str, str]:
    r = client.post("/api/auth/register", json=REGISTER)
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_create_empty_content_returns_422_envelope(client):
    """空内容必须 422 + code envelope（修复前 HTTPException 实例被 return，响应形态错误）。"""
    headers = _headers(client)
    r = client.post("/api/prompts", json={"content": "   "}, headers=headers)
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "CONTENT_REQUIRED"


def test_favorite_missing_prompt_returns_404_envelope(client):
    headers = _headers(client)
    r = client.post("/api/prompts/999999/favorite", headers=headers)
    assert r.status_code == 404, r.text
    assert r.json()["detail"]["code"] == "PROMPT_NOT_FOUND"


def test_import_rejects_non_list_items(client):
    headers = _headers(client)
    r = client.post("/api/prompts/import", json={"items": "not-a-list"}, headers=headers)
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "ITEMS_REQUIRED"


def test_prompts_isolated_between_users_seeds_shared(client, db_session):
    """N4：个人提示词按用户隔离；内置种子全员共享。"""
    headers_a = _headers(client)
    import uuid

    tag = uuid.uuid4().hex[:6]
    r = client.post(
        "/api/auth/register",
        json={
            "username": f"pb{tag}",
            "email": f"pb{tag}@x.io",
            "password": "password123",
            "invite_code": "letmein",
        },
    )
    headers_b = {"Authorization": f"Bearer {r.json()['access_token']}"}

    # A 创建个人提示词
    created = client.post(
        "/api/prompts",
        json={"content": "A的私有提示词", "title": "A私有"},
        headers=headers_a,
    ).json()["prompt"]

    # 预置一条共享条目（等价内置种子：user_id NULL）
    from backend.app.models import Prompt as PromptModel

    db_session.add(PromptModel(user_id=None, content="共享种子提示词", source="builtin"))
    db_session.commit()

    # B 看不到 A 的，但能看到共享条目
    list_b = client.get("/api/prompts?limit=200", headers=headers_b).json()["prompts"]
    ids = {p["id"] for p in list_b}
    assert created["id"] not in ids
    assert any(p["content"] == "共享种子提示词" for p in list_b)

    # A 自己能看到
    list_a = client.get("/api/prompts?limit=200", headers=headers_a).json()["prompts"]
    assert created["id"] in {p["id"] for p in list_a}

    # 导入同样隔离
    client.post(
        "/api/prompts/import",
        json={"items": [{"content": f"B导入{tag}"}]},
        headers=headers_b,
    )
    list_a2 = client.get("/api/prompts?search=" + tag, headers=headers_a).json()["prompts"]
    assert all(f"B导入{tag}" not in (p["content"] or "") for p in list_a2)


class TestPromptsCRUD:
    """提示词库补全：收藏状态返回 / 删除 / 编辑 / 收藏筛选。"""

    def test_list_returns_favorited_flag(self, client, db_session):
        """列表 API 应返回每条提示词的 is_favorited 状态（当前用户视角）。"""
        headers = _headers(client)
        from backend.app.models import Prompt as PM
        from backend.app.models import PromptFavorite

        p = PM(user_id=1, content="测试收藏状态", source="manual")
        db_session.add(p)
        db_session.commit()
        db_session.add(PromptFavorite(user_id=1, prompt_id=p.id))
        db_session.commit()

        r = client.get("/api/prompts?limit=200", headers=headers)
        assert r.status_code == 200
        items = {x["id"]: x for x in r.json()["prompts"]}
        assert items[p.id]["is_favorited"] is True

    def test_delete_own_prompt(self, client, db_session):
        headers = _headers(client)
        r = client.post("/api/prompts", json={"content": "待删除"}, headers=headers)
        pid = r.json()["prompt"]["id"]

        r = client.delete(f"/api/prompts/{pid}", headers=headers)
        assert r.status_code == 204
        r = client.get("/api/prompts?limit=200", headers=headers)
        assert pid not in [x["id"] for x in r.json()["prompts"]]

    def test_cannot_delete_seed(self, client, db_session):
        """种子提示词（user_id=NULL）不可删除。"""
        headers = _headers(client)
        from backend.app.models import Prompt as PM

        p = PM(user_id=None, content="种子", source="builtin")
        db_session.add(p)
        db_session.commit()

        r = client.delete(f"/api/prompts/{p.id}", headers=headers)
        assert r.status_code == 403

    def test_update_own_prompt(self, client, db_session):
        headers = _headers(client)
        r = client.post(
            "/api/prompts", json={"content": "原始内容", "title": "原标题"}, headers=headers
        )
        pid = r.json()["prompt"]["id"]

        r = client.patch(
            f"/api/prompts/{pid}",
            json={"content": "修改后内容", "title": "新标题"},
            headers=headers,
        )
        assert r.status_code == 200
        assert r.json()["prompt"]["content"] == "修改后内容"
        assert r.json()["prompt"]["title"] == "新标题"

    def test_favorites_only_filter(self, client, db_session):
        """?favorites_only=true 只返回当前用户收藏的。"""
        headers = _headers(client)
        from backend.app.models import Prompt as PM
        from backend.app.models import PromptFavorite

        p1 = PM(user_id=1, content="收藏的", source="manual")
        p2 = PM(user_id=1, content="未收藏", source="manual")
        db_session.add_all([p1, p2])
        db_session.commit()
        db_session.add(PromptFavorite(user_id=1, prompt_id=p1.id))
        db_session.commit()

        r = client.get("/api/prompts?favorites_only=true", headers=headers)
        ids = [x["id"] for x in r.json()["prompts"]]
        assert p1.id in ids
        assert p2.id not in ids


class TestPromptsAlignment:
    """对齐生产版：标签筛选 / 模型筛选 / 排序 / 套用状态。"""

    def test_tag_filter(self, client, db_session):
        headers = _headers(client)
        from backend.app.models import Prompt as PM

        p1 = PM(user_id=1, content="有标签的", tags='["人像", "摄影"]', source="manual")
        p2 = PM(user_id=1, content="无标签的", tags="[]", source="manual")
        db_session.add_all([p1, p2])
        db_session.commit()

        r = client.get("/api/prompts?tag=人像", headers=headers)
        ids = [x["id"] for x in r.json()["prompts"]]
        assert p1.id in ids
        assert p2.id not in ids

    def test_model_filter(self, client, db_session):
        headers = _headers(client)
        from backend.app.models import Prompt as PM

        p1 = PM(user_id=1, content="Kolors 的", model="Kwai-Kolors/Kolors", source="manual")
        p2 = PM(user_id=1, content="其他的", model=None, source="manual")
        db_session.add_all([p1, p2])
        db_session.commit()

        r = client.get("/api/prompts?model=Kolors", headers=headers)
        ids = [x["id"] for x in r.json()["prompts"]]
        assert p1.id in ids
        assert p2.id not in ids

    def test_sort_random(self, client):
        """sort=random 返回 200（不崩）。"""
        headers = _headers(client)
        r = client.get("/api/prompts?sort=random&limit=5", headers=headers)
        assert r.status_code == 200


def test_categories_endpoint(client, db_session):
    """全库分类统计端点——不依赖当前页。"""
    headers = _headers(client)
    from backend.app.models import Prompt as PM

    db_session.add_all([
        PM(user_id=None, content="a", category="image:人像", source="builtin"),
        PM(user_id=None, content="b", category="image:人像", source="builtin"),
        PM(user_id=None, content="c", category="image:风景", source="builtin"),
        PM(user_id=None, content="d", category="llm:效率", source="builtin"),
        PM(user_id=None, content="e", category="摄影与写实", source="builtin"),
    ])
    db_session.commit()

    r = client.get("/api/prompts/categories", headers=headers)
    assert r.status_code == 200
    cats = r.json()["categories"]
    cat_map = {c["category"]: c["count"] for c in cats}
    assert cat_map.get("image:人像") == 2
    assert cat_map.get("image:风景") == 1
    assert cat_map.get("llm:效率") == 1
    assert cat_map.get("摄影与写实") == 1


class TestPromptEditable:
    """P1-3: 列表返回 user_id，前端据此显示编辑按钮。"""

    def test_list_returns_user_id_flag(self, client, db_session):
        headers = _headers(client)
        r = client.post("/api/prompts", json={"content": "我的提示词"}, headers=headers)
        pid = r.json()["prompt"]["id"]

        from backend.app.models import Prompt as PM

        seed = PM(user_id=None, content="种子", source="builtin")
        db_session.add(seed)
        db_session.commit()

        r = client.get("/api/prompts?limit=200", headers=headers)
        items = {x["id"]: x for x in r.json()["prompts"]}
        # 个人提示词：user_id = 当前用户（可编辑）
        assert items[pid]["user_id"] is not None
        # 种子提示词：user_id = None（不可编辑）
        assert items[seed.id]["user_id"] is None


class TestPromptThumbAutoUpdate:
    """P1-5: 图像任务完成后自动把结果绑定为提示词效果图。"""

    def test_image_completion_updates_own_prompt_thumb(self, client, db_session):
        """个人提示词：生成完成 → thumb_url 更新 + usage_count +1。"""
        from backend.app.models import Prompt as PM
        from backend.app.models import Task
        from backend.app.services.task_lifecycle import run_task_lifecycle

        headers = _headers(client)
        r = client.post("/api/prompts", json={"content": "赛博猫"}, headers=headers)
        pid = r.json()["prompt"]["id"]

        # 提交任务带 prompt_id（走 params）
        task = Task(
            user_id=1, type="image", prompt="赛博猫", model="m",
            params=json.dumps({'prompt_id': pid}), status="queued",
        )
        db_session.add(task)
        db_session.commit()

        async def fake_gen(t):
            return {
                "status": "completed",
                "output_url": "/outputs/new_cat.webp",
                "cost_amount": 0.01,
            }

        import asyncio
        asyncio.run(run_task_lifecycle(db_session, int(task.id), fake_gen))

        p = db_session.get(PM, pid)
        assert p.thumb_url == "/outputs/new_cat.webp"
        assert p.usage_count == 1

    def test_seed_prompt_keeps_existing_thumb(self, client, db_session):
        """种子提示词已有图：不覆盖精选缩略图，只记 usage。"""
        from backend.app.models import Prompt as PM
        from backend.app.models import Task
        from backend.app.services.task_lifecycle import run_task_lifecycle

        seed = PM(user_id=None, content="种子", source="builtin",
                  thumb_url="/outputs/prompt-thumbs-full/curated.webp")
        db_session.add(seed)
        db_session.commit()

        task = Task(
            user_id=1, type="image", prompt="种子", model="m",
            params=json.dumps({'prompt_id': seed.id}), status="queued",
        )
        db_session.add(task)
        db_session.commit()

        async def fake_gen(t):
            return {
                "status": "completed",
                "output_url": "/outputs/user_gen.webp",
                "cost_amount": 0.01,
            }

        import asyncio
        asyncio.run(run_task_lifecycle(db_session, int(task.id), fake_gen))

        p = db_session.get(PM, seed.id)
        assert p.thumb_url == "/outputs/prompt-thumbs-full/curated.webp"  # 未覆盖
        assert p.usage_count == 1

    def test_seed_prompt_without_thumb_never_auto_bound(self, client, db_session):
        """审计批次三（P2，行为变更）：无图种子不再被用户生成图自动补全——
        防跨用户内容注入；补图属策展职责（seed/管理员）。"""
        from backend.app.models import Prompt as PM
        from backend.app.models import Task
        from backend.app.services.task_lifecycle import run_task_lifecycle

        seed = PM(user_id=None, content="无图种子2", source="builtin")
        db_session.add(seed)
        db_session.commit()

        task = Task(
            user_id=1, type="image", prompt="无图种子2", model="m",
            params=json.dumps({"prompt_id": seed.id}), status="queued",
        )
        db_session.add(task)
        db_session.commit()

        async def fake_gen(t):
            return {"status": "completed", "output_url": "/outputs/fill.webp", "cost_amount": 0}

        import asyncio
        asyncio.run(run_task_lifecycle(db_session, int(task.id), fake_gen))

        p = db_session.get(PM, seed.id)
        assert p.thumb_url is None
        assert p.usage_count == 1


class TestExtraOutputs:
    """P2-9: 多图结果持久化到 params（详情端点可取回）。"""

    def test_extra_urls_persisted_in_params(self, client, db_session):
        from backend.app.models import Task
        from backend.app.services.task_lifecycle import run_task_lifecycle

        task = Task(user_id=1, type="image", prompt="多图", model="m",
                    params='{"image_count": 3}', status="queued")
        db_session.add(task)
        db_session.commit()

        async def fake_gen(t):
            return {"status": "completed", "output_url": "/outputs/a.png",
                    "extra_urls": ["/outputs/b.png", "/outputs/c.png"], "cost_amount": 0.03}

        import asyncio
        asyncio.run(run_task_lifecycle(db_session, int(task.id), fake_gen))

        db_session.refresh(task)
        d = task.to_dict()
        assert d["output_url"] == "/outputs/a.png"
        assert d["extra_urls"] == ["/outputs/b.png", "/outputs/c.png"]


class TestCreateWithMeta:
    """新建/编辑支持 tags / model / thumb_url（表单与种子字段对齐）。"""

    def test_create_with_tags_model_thumb(self, client, db_session):
        headers = _headers(client)
        r = client.post(
            "/api/prompts",
            json={
                "content": "赛博朋克城市夜景",
                "title": "赛博城市",
                "category": "image:风景",
                "tags": ["赛博朋克", "夜景"],
                "model": "Z-Image-Turbo",
                "thumb_url": "/outputs/uploads/eff123.png",
            },
            headers=headers,
        )
        assert r.status_code == 201, r.text
        p = r.json()["prompt"]
        assert p["tags"] == ["赛博朋克", "夜景"]
        assert p["model"] == "Z-Image-Turbo"
        assert p["thumb_url"] == "/outputs/uploads/eff123.png"
        assert p["category"] == "image:风景"

    def test_create_rejects_external_thumb(self, client, db_session):
        """效果图只收本地 /outputs/ 路径（防外链挂图/追踪）。"""
        headers = _headers(client)
        r = client.post(
            "/api/prompts",
            json={"content": "x", "thumb_url": "https://evil.com/track.png"},
            headers=headers,
        )
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "THUMB_URL_INVALID"

    def test_tags_normalized(self, client, db_session):
        """标签去空/去重/截断（≤8 个，每个 ≤20 字）。"""
        headers = _headers(client)
        r = client.post(
            "/api/prompts",
            json={"content": "x", "tags": ["猫", " ", "猫", "x" * 30, ""]},
            headers=headers,
        )
        p = r.json()["prompt"]
        assert p["tags"] == ["猫", "x" * 20]

    def test_patch_updates_tags_and_thumb(self, client, db_session):
        headers = _headers(client)
        r0 = client.post("/api/prompts", json={"content": "原始"}, headers=headers)
        pid = r0.json()["prompt"]["id"]
        r = client.patch(
            f"/api/prompts/{pid}",
            json={
                "content": "改后", "tags": ["新标签"],
                "thumb_url": "/outputs/uploads/new.webp", "model": "Kolors",
            },
            headers=headers,
        )
        p = r.json()["prompt"]
        assert p["tags"] == ["新标签"]
        assert p["thumb_url"] == "/outputs/uploads/new.webp"
        assert p["model"] == "Kolors"

    def test_patch_rejects_external_thumb(self, client, db_session):
        headers = _headers(client)
        r0 = client.post("/api/prompts", json={"content": "原始"}, headers=headers)
        pid = r0.json()["prompt"]["id"]
        r = client.patch(
            f"/api/prompts/{pid}",
            json={"content": "x", "thumb_url": "https://cdn.example.com/a.png"},
            headers=headers,
        )  # noqa: E501
        assert r.status_code == 422


class TestHasThumbFilter:
    """首页灵感墙：只取带效果图的提示词（sort=random + has_thumb）。"""

    def test_has_thumb_returns_only_with_image(self, client, db_session):
        headers = _headers(client)
        client.post("/api/prompts", json={"content": "无图A"}, headers=headers)
        client.post(
            "/api/prompts",
            json={"content": "有图B", "thumb_url": "/outputs/uploads/b.webp"},
            headers=headers,
        )

        r = client.get("/api/prompts?has_thumb=true&limit=50", headers=headers)
        contents = [p["content"] for p in r.json()["prompts"]]
        assert "有图B" in contents
        assert "无图A" not in contents

    def test_has_thumb_random_with_limit(self, client, db_session):
        """随机排序 + has_thumb + limit 组合（灵感墙精确取 12 张）。"""
        headers = _headers(client)
        for i in range(5):
            client.post(
                "/api/prompts",
                json={"content": f"图{i}", "thumb_url": f"/outputs/uploads/{i}.webp"},
                headers=headers,
            )
        r = client.get("/api/prompts?has_thumb=true&sort=random&limit=3", headers=headers)
        assert len(r.json()["prompts"]) == 3
        assert all(p["thumb_url"] for p in r.json()["prompts"])


class TestCompletedMeta:
    """P0: completed SSE 事件携带视频时长/分辨率（节点徽标数据源）。"""

    def test_completed_event_carries_video_meta(self, client, db_session):
        from unittest.mock import patch

        from backend.app.models import Task
        from backend.app.services import task_lifecycle as lc

        task = Task(
            user_id=1, type="video", prompt="猫跳舞", model="m",
            params='{"duration": 6, "resolution": "720p"}', status="queued",
        )
        db_session.add(task)
        db_session.commit()

        events = []

        def _capture(tid, ev, payload=None):
            events.append((ev, payload))

        with patch.object(lc, "publish_task_event", _capture):
            import asyncio

            async def fake_gen(t):
                return {"status": "completed", "output_url": "/outputs/v.mp4", "cost_amount": 0.1}

            asyncio.run(lc.run_task_lifecycle(db_session, int(task.id), fake_gen))

        ev, payload = [e for e in events if e[0] == "completed"][0]
        assert payload["meta"] == {"duration": 6, "resolution": "720p"}

    def test_image_task_meta_empty(self, client, db_session):
        from unittest.mock import patch

        from backend.app.models import Task
        from backend.app.services import task_lifecycle as lc

        task = Task(user_id=1, type="image", prompt="猫", model="m", params="{}", status="queued")
        db_session.add(task)
        db_session.commit()

        events = []

        def _capture(tid, ev, payload=None):
            events.append((ev, payload))

        with patch.object(lc, "publish_task_event", _capture):
            import asyncio

            async def fake_gen(t):
                return {"status": "completed", "output_url": "/outputs/i.png", "cost_amount": 0}

            asyncio.run(lc.run_task_lifecycle(db_session, int(task.id), fake_gen))

        ev, payload = [e for e in events if e[0] == "completed"][0]
        assert payload["meta"] == {}


class TestSeedPromptInjectionGuard:
    """审计批次三（P2）：种子提示词（user_id=None，全员可见）不得被任意用户的
    生成结果自动绑定缩略图——那是跨用户内容注入（恶意图可上全员灵感墙）。"""

    def test_seed_prompt_no_thumb_not_bound_by_user_generation(self, client, db_session):
        from backend.app.models import Prompt as PM
        from backend.app.models import Task
        from backend.app.services.task_lifecycle import run_task_lifecycle

        seed = PM(user_id=None, content="无图种子", source="builtin")
        db_session.add(seed)
        db_session.commit()

        task = Task(
            user_id=1, type="image", prompt="无图种子", model="m",
            params=json.dumps({"prompt_id": seed.id}), status="queued",
        )
        db_session.add(task)
        db_session.commit()

        async def fake_gen(t):
            return {"status": "completed", "output_url": "/outputs/evil.webp", "cost_amount": 0.01}

        import asyncio
        asyncio.run(run_task_lifecycle(db_session, int(task.id), fake_gen))

        p = db_session.get(PM, seed.id)
        assert p.thumb_url is None, "种子提示词不得被用户生成图自动绑定（内容注入）"
        assert p.usage_count == 1  # 热度统计保留
