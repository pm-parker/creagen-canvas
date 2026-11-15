
def test_optimize_rate_limited(client):
    """审计批次三（P2）：optimize 走用户 LLM 渠道但原无限流——11 次/分钟即 429。"""
    client.post(
        "/api/auth/register",
        json={
            "username": "opt_rl", "email": "or@x.io",
            "password": "password123", "invite_code": "letmein",
        },
    )
    tok = client.post(
        "/api/auth/login", json={"identifier": "opt_rl", "password": "password123"}
    ).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    codes = [
        client.post(
            "/api/prompts/optimize", json={"prompt": "一只奔跑的橘猫"}, headers=h
        ).status_code
        for _ in range(10)
    ]
    assert all(c == 200 for c in codes), codes
    r = client.post(
        "/api/prompts/optimize", json={"prompt": "一只奔跑的橘猫"}, headers=h
    )
    assert r.status_code == 429


def test_optimize_no_hardcoded_model(client, monkeypatch):
    """渠道模型修复（2026-10-07 生产 400）：optimize 不得硬编码 gpt-4o-mini——
    用户渠道无此模型时兜底渠道+坏模型名=必 400。应自动用渠道目录内的模型。"""
    import uuid as _u

    name = f"opt_{_u.uuid4().hex[:8]}"
    client.post(
        "/api/auth/register",
        json={
            "username": name, "email": f"{name}@x.io",
            "password": "password123", "invite_code": "letmein",
        },
    )
    tok = client.post(
        "/api/auth/login", json={"identifier": name, "password": "password123"}
    ).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}

    captured = {}

    class FakeProv:
        def __init__(self, model="", channel=None, key_plain="", base_url="", task_type="llm"):
            self.model = model
            self.channel = channel
            captured["model"] = model
            captured["is_openai"] = True

        async def run(self, task, on_event=None):
            return {"status": "completed", "output_url": "优化后的提示词"}

    import backend.app.services.provider_service as ps

    def fake_resolve(db, user_id, domain, model, channel_id=None):
        captured["requested_model"] = model
        return FakeProv(model=model or "Qwen/Qwen3.5-9B"), None

    monkeypatch.setattr(ps, "resolve_provider_for_task", fake_resolve)

    r = client.post("/api/prompts/optimize", json={"prompt": "一只戴帽子的橘猫"}, headers=h)
    assert r.status_code == 200, r.text
    # 关键断言：解析请求的模型不再是硬编码 gpt-4o-mini（空=让 resolver 自动选目录内模型）
    assert captured["requested_model"] != "gpt-4o-mini"
    assert captured["model"] in ("", "Qwen/Qwen3.5-9B")
