"""auth 域契约：注册/登录/me + 邀请制（Review Focus #5 重放变体）。"""

REGISTER_OK = {"username": "alice", "email": "alice@example.com", "password": "password123"}


def _register(client, invite_code="letmein", **overrides):
    body = {**REGISTER_OK, "invite_code": invite_code, **overrides}
    return client.post("/api/auth/register", json=body)


def test_register_login_me_roundtrip(client):
    r = _register(client)
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]

    r = client.post("/api/auth/login", json={"identifier": "alice", "password": "password123"})
    assert r.status_code == 200
    token = r.json()["access_token"]

    r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["username"] == "alice"


def test_login_by_email(client):
    _register(client)
    r = client.post(
        "/api/auth/login", json={"identifier": "alice@example.com", "password": "password123"}
    )
    assert r.status_code == 200


def test_wrong_password_401(client):
    _register(client)
    r = client.post("/api/auth/login", json={"identifier": "alice", "password": "wrong-pass"})
    assert r.status_code == 401


def test_invite_mode_rejects_missing_code(client):
    r = _register(client, invite_code=None)
    assert r.status_code == 403


def test_invite_mode_rejects_wrong_code(client):
    r = _register(client, invite_code="nope")
    assert r.status_code == 403


def test_invite_mode_rejects_replay_variants(client):
    """Review Focus #5：空串/空白/None 等价物重放均拒绝。"""
    for variant in ("", "   ", None):
        r = _register(client, invite_code=variant)
        assert r.status_code == 403, f"variant={variant!r} 应被拒绝"


def test_duplicate_username_409(client):
    _register(client)
    r = _register(client)
    assert r.status_code == 409


def test_short_password_rejected(client):
    r = _register(client, password="short")
    assert r.status_code == 400


def test_me_requires_token(client):
    r = client.get("/api/auth/me")
    assert r.status_code == 401


def test_unicode_invite_code_not_500(client):
    """审计回归：unicode 邀请码不得触发 500（compare_digest bytes 化）。"""
    for bad in ("長" * 5000, "邀请码✨", "線"):
        r = client.post(
            "/api/auth/register",
            json={
                "username": "uc",
                "email": "uc@x.io",
                "password": "password123",
                "invite_code": bad,
            },
        )
        assert r.status_code == 403, f"{bad[:6]!r} → {r.status_code}"


def test_login_rate_limited_after_repeated_failures(client):
    """N2：登录暴力破解面——连续失败后 429（按用户名+IP 双桶）。"""
    body = {"identifier": "bruteforce-target", "password": "wrong-password"}
    codes = []
    for _ in range(11):
        r = client.post("/api/auth/login", json=body)
        codes.append(r.status_code)
    assert codes[:10] == [401] * 10
    assert codes[10] == 429, codes
    detail = r.json()["detail"]
    assert detail["code"] == "RATE_LIMITED"


def test_successful_login_does_not_consume_limit(client):
    """成功登录不消耗限流额度（e2e/多端并发登录不受误伤）。"""
    from backend.app.services import rate_limiter

    rate_limiter._memory_store.clear()
    REGISTER_OK = {
        "username": "oklogin",
        "email": "oklogin@x.io",
        "password": "password123",
        "invite_code": "letmein",
    }
    client.post("/api/auth/register", json=REGISTER_OK)
    codes = [
        client.post(
            "/api/auth/login", json={"identifier": "oklogin", "password": "password123"}
        ).status_code
        for _ in range(15)
    ]
    assert all(c == 200 for c in codes), codes


def test_tour_completed_flag_roundtrip(client):
    """新手引导标记服务端化（0.7.1）：新用户默认 False，标记后幂等置 True。"""
    reg = {
        **REGISTER_OK,
        "username": "alice_tour",
        "email": "alice_tour@example.com",
        "invite_code": "letmein",
    }
    token = client.post("/api/auth/register", json=reg).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    me = client.get("/api/auth/me", headers=headers).json()
    assert me["tour_completed"] is False

    r = client.post("/api/auth/tour-completed", headers=headers)
    assert r.status_code == 200
    assert r.json()["tour_completed"] is True

    # 幂等：重复调用不炸
    assert client.post("/api/auth/tour-completed", headers=headers).status_code == 200

    # me 反映 True
    me = client.get("/api/auth/me", headers=headers).json()
    print("ME_TOUR:", me.get("tour_completed"))
    assert me["tour_completed"] is True


def test_tour_completed_requires_auth(client):
    assert client.post("/api/auth/tour-completed").status_code == 401
