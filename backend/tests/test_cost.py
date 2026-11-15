"""成本治理契约：计价 / LLM 预算熔断 / 限流回落 / 生命周期成本快照。"""

import asyncio

import pytest

REGISTER = {
    "username": "costuser",
    "email": "cost@example.com",
    "password": "password123",
    "invite_code": "letmein",
}


def test_image_cost_positive():
    from backend.app.services.cost_calculator import calculate_cost

    assert calculate_cost("image", "Tongyi-MAI/Z-Image-Turbo") == 0.1
    assert calculate_cost("image", "Tongyi-MAI/Z-Image-Turbo", {"image_count": 4}) == 0.4


def test_video_cost_by_duration():
    from backend.app.services.cost_calculator import calculate_cost

    assert calculate_cost("video", "seedance") == 6.0  # 1.0/s × 6s 默认（MiniMax Hailuo）
    assert calculate_cost("video", "seedance", {"duration": 10}) == 10.0


def test_llm_audio_zero_cash_cost():
    from backend.app.services.cost_calculator import calculate_cost

    assert calculate_cost("llm", "gpt-x") == 0.0
    assert calculate_cost("audio", "tts-x") == 0.0


def test_budget_exhausted(db_session):
    from backend.app.services.llm_budget import BudgetExceeded, budget_check, budget_consume

    budget_consume(db_session, "storyboard:42", 900_000, 99_999)
    assert budget_check(db_session, "storyboard:42") == 1  # 还剩 1 token

    budget_consume(db_session, "storyboard:42", 0, 1)
    with pytest.raises(BudgetExceeded):
        budget_check(db_session, "storyboard:42")


def test_budget_scopes_isolated(db_session):
    from backend.app.services.llm_budget import budget_check, budget_consume

    budget_consume(db_session, "scope-a", 500_000, 500_000)
    assert budget_check(db_session, "scope-b") == 1_000_000


def test_rate_limit_memory_fallback():
    """无 Redis 时回落进程内存，超限抛 RateLimitExceeded。"""
    from backend.app.services import rate_limiter

    rate_limiter._memory_store.clear()
    count = 0
    for _ in range(3):
        count, _ = rate_limiter.consume("test", "k1", limit=3, window_seconds=60)
    assert count == 3
    with pytest.raises(rate_limiter.RateLimitExceeded):
        rate_limiter.enforce("test", "k1", limit=3, window_seconds=60, message="太频繁")
    rate_limiter._memory_store.clear()


def test_worker_writes_cost_amount(client, worker_db):
    """生命周期完成时按计价表快照 cost_amount（Task 6 挂到 Task 5 生命周期的钩子）。"""
    from backend.app.models import Task
    from backend.worker import run_generation_task

    r = client.post("/api/auth/register", json=REGISTER)
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    r = client.post(
        "/api/tasks",
        json={"type": "image", "prompt": "p", "model": "Tongyi-MAI/Z-Image-Turbo"},
        headers=headers,
    )
    task_id = r.json()["task"]["id"]

    result = asyncio.run(run_generation_task({}, task_id))
    assert result["status"] == "completed"

    session = worker_db()
    task = session.get(Task, task_id)
    assert task is not None
    assert task.cost_amount == 0.1


def test_llm_submit_enforces_budget(client, worker_db, db_session):
    """llm 提交经预算检查：耗尽后提交被拒（Task 7 接线验证）。"""
    from backend.app.services.llm_budget import budget_consume

    r = client.post("/api/auth/register", json=REGISTER)
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    # 直接把预算打爆（默认 1M）
    budget_consume(db_session, "llm:1", 2_000_000, 0)
    db_session.commit()
    r = client.post(
        "/api/tasks",
        json={"type": "llm", "prompt": "p", "model": "fake-llm"},
        headers=headers,
    )
    assert r.status_code in (422, 429), r.text


def test_cost_stats_endpoint(client, worker_db):
    """成本看板聚合：结构完整且数据正确。"""
    r = client.post("/api/auth/register", json=REGISTER)
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

    r = client.post(
        "/api/tasks",
        json={"type": "image", "prompt": "cost stats", "model": "Tongyi-MAI/Z-Image-Turbo"},
        headers=headers,
    )
    task_id = r.json()["task"]["id"]

    import asyncio

    from backend.worker import run_generation_task

    result = asyncio.run(run_generation_task({}, task_id))
    assert result["status"] == "completed"

    stats = client.get("/api/costs/stats", headers=headers).json()
    assert stats["total_cost"] > 0
    assert len(stats["daily_breakdown"]) == 7
    assert stats["month_completed_count"] >= 1
    assert any(b["model"] == "Tongyi-MAI/Z-Image-Turbo" for b in stats["model_breakdown"])


def test_h3_local_video_free():
    """H3 本地视频模型零现金成本（分镜预估不误导）。"""
    from backend.app.services.cost_calculator import calculate_cost

    assert calculate_cost("video", "minimax-h3-video", {"duration": 6}) == 0.0


# ---------- D3 成本看板二批：环比/窗口/CSV/下钻/LLM token 计量 ----------


def _register_cost_user(client):
    """每测试独立用户，避免限流/数据串扰。"""
    import uuid

    name = f"cost_{uuid.uuid4().hex[:8]}"
    client.post(
        "/api/auth/register",
        json={
            "username": name, "email": f"{name}@x.io",
            "password": "password123", "invite_code": "letmein",
        },
    )
    r = client.post("/api/auth/login", json={"identifier": name, "password": "password123"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _run_image_task(client, headers, model="Tongyi-MAI/Z-Image-Turbo"):
    r = client.post(
        "/api/tasks", json={"type": "image", "prompt": "p", "model": model}, headers=headers
    )
    task_id = r.json()["task"]["id"]
    import asyncio

    from backend.worker import run_generation_task

    result = asyncio.run(run_generation_task({}, task_id))
    assert result["status"] == "completed"
    return task_id


def test_stats_days_window_and_prev_month(client, worker_db):
    """D3.1：daily_breakdown 支持 ?days= 窗口（默认 7 兼容、越界钳制）；stats 增上月同期基线。"""
    headers = _register_cost_user(client)
    stats = client.get("/api/costs/stats", headers=headers).json()
    assert len(stats["daily_breakdown"]) == 7
    assert "prev_month_cost" in stats
    stats30 = client.get("/api/costs/stats", params={"days": 30}, headers=headers).json()
    assert len(stats30["daily_breakdown"]) == 30
    clamped = client.get("/api/costs/stats", params={"days": 9999}, headers=headers).json()
    assert len(clamped["daily_breakdown"]) == 90


def test_cost_export_csv(client, worker_db):
    """D3.2：任务级明细 CSV 导出——含 header/附件 disposition/数据行。"""
    headers = _register_cost_user(client)
    _run_image_task(client, headers)
    r = client.get("/api/costs/export", headers=headers)
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    assert "attachment" in r.headers.get("content-disposition", "")
    lines = r.text.strip().splitlines()
    assert lines[0].startswith("id,")
    assert "tokens_used" in lines[0]
    assert any("Tongyi-MAI/Z-Image-Turbo" in ln for ln in lines[1:])


def test_cost_tasks_drilldown(client, worker_db):
    """D3.3：模型下钻——分页任务明细，按模型过滤，含 token 字段。"""
    headers = _register_cost_user(client)
    _run_image_task(client, headers)
    client.post(
        "/api/tasks", json={"type": "image", "prompt": "q", "model": "other-model"}, headers=headers
    )
    data = client.get(
        "/api/costs/tasks", params={"model": "Tongyi-MAI/Z-Image-Turbo"}, headers=headers
    ).json()
    assert data["total"] == 1
    item = data["items"][0]
    assert item["model"] == "Tongyi-MAI/Z-Image-Turbo"
    assert set(item) >= {
        "id", "created_at", "type", "model", "status", "cost_amount", "tokens_used",
    }
    paged = client.get("/api/costs/tasks", params={"limit": 1, "offset": 0}, headers=headers).json()
    assert len(paged["items"]) == 1
    assert paged["total"] >= 1


def test_llm_usage_extracted_from_provider(respx_mock):
    """D3.4：chat/completions 非流式响应的 usage 透传到 result（计价识别后端化）。"""
    import asyncio

    import httpx

    from backend.app.providers.openai_compat import OpenAICompatProvider

    base = "https://llm-d3.example.com/v1"
    respx_mock.post(f"{base}/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "好的"}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 34, "total_tokens": 46},
            },
        )
    )
    provider = OpenAICompatProvider(model="m", base_url=base, key_plain="sk-x", task_type="llm")

    class _T:
        prompt = "你好"

    result = asyncio.run(provider.run(_T(), on_event=None))
    assert result["status"] == "completed"
    assert result["usage"]["total_tokens"] == 46


def test_llm_task_tokens_recorded(db_session):
    """D3.4：生命周期把 result.usage.total_tokens 落到 tasks.tokens_used。"""
    import asyncio

    from backend.app.models import Task
    from backend.app.services.task_lifecycle import run_task_lifecycle
    from backend.app.services.task_store import create_task

    task = create_task(db_session, user_id=1, type_="llm", prompt="p", model="fake-llm")

    async def fake_gen(t, on_event=None):
        return {
            "status": "completed", "output_url": "x",
            "usage": {"prompt_tokens": 12, "completion_tokens": 34, "total_tokens": 46},
        }

    result = asyncio.run(run_task_lifecycle(db_session, int(task.id), fake_gen))
    assert result["status"] == "completed"
    db_session.expire_all()
    assert db_session.get(Task, task.id).tokens_used == 46


def test_optimize_budget_uses_real_usage(client, worker_db, monkeypatch):
    """D3.4：optimize 的预算记账用真实 usage 替代 len(prompt)*2 粗估。"""
    from backend.app.models import LLMBudget
    from backend.app.providers.openai_compat import OpenAICompatProvider

    async def _run(self, task, on_event=None):
        return {
            "status": "completed", "output_url": "优化后的提示词",
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        }

    monkeypatch.setattr(OpenAICompatProvider, "run", _run)
    headers = _register_cost_user(client)
    r = client.post(
        "/api/providers/channels",
        json={
            "domain": "llm", "name": "llm-ch",
            "base_url": "http://llm.invalid/v1", "key": "sk-x",
        },
        headers=headers,
    )
    assert r.status_code in (200, 201), r.text
    r = client.post("/api/prompts/optimize", json={"prompt": "一只奔跑的橘猫"}, headers=headers)
    assert r.status_code == 200, r.text
    rows = worker_db().query(LLMBudget).filter(LLMBudget.scope.like("llm:%")).all()
    assert rows, "预算行应已创建"
    assert rows[0].tokens_used == 30


def test_cost_stats_reports_free_flag(client, worker_db, db_session):
    """Task 9：模型分布行携带 is_free（看板「免费」语义而非误导性 ¥0）。"""
    from backend.app.models import ProviderChannel, ProviderModel

    ch = ProviderChannel(
        domain="image", name="free-ch", base_url="https://fr.example.com/v1", key_encrypted=""
    )
    db_session.add(ch)
    db_session.flush()
    db_session.add(
        ProviderModel(
            channel_id=ch.id, model_id="free-flag-img", is_free=1,
            pricing_unit="per_image", price_input=0.0,
        )
    )
    db_session.commit()

    headers = _register_cost_user(client)
    r = client.post(
        "/api/tasks",
        json={"type": "image", "prompt": "p", "model": "free-flag-img", "channel_id": ch.id},
        headers=headers,
    )
    task_id = int(r.json()["task"]["id"])

    import asyncio

    from backend.worker import run_generation_task

    async def fake_gen(t, on_event=None):
        return {"status": "completed", "output_url": "/outputs/images/ok.png"}

    result = asyncio.run(run_generation_task({}, task_id, _gen_override=fake_gen))
    assert result["status"] == "completed", result
    stats = client.get("/api/costs/stats", headers=headers).json()
    row = next(b for b in stats["model_breakdown"] if b["model"] == "free-flag-img")
    assert row["is_free"] is True
