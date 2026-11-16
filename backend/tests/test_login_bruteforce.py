"""登录锁定时序回归（P2 安全）。

旧实现只在“密码错误”后才计数，且超限后正确密码仍能登录：
- 泄露信息：攻击者可凭 200/429 区分本次猜测是否正确；
- 锁定形同虚设：暴力破解可继续验证猜测。
修复后：验密码前先只读预检（is_limited，不消耗额度），锁定期内一律 429；
成功登录不计数并清空该用户失败计数。

审查轮补充：预检只看用户名桶——IP 桶若参与预检，反代共享 IP 下攻击者
刷满 30 次失败即可让所有用户（含正确密码）永久 429，构成横向拒绝服务。
IP 桶只允许把“已失败”的尝试从 401 升级为 429，绝不得拦截正确密码。
"""

import time

import pytest

from backend.tests.test_auth import _register

PASSWORD = "password123"
WRONG = "wrong-password-123"


def _reset_login_limiters():
    """清空登录限流计数（内存 + Redis）：CI/共享 Redis 环境下避免跨测试残留
    （同 identifier/IP 桶 300s 窗口内会串扰，仓库惯例见 test_media_router）。"""
    from backend.app.services import rate_limiter

    rate_limiter._memory_store.clear()
    try:
        r = rate_limiter.get_sync_redis()
        for key in r.keys("canvas:ratelimit:auth:login:*"):
            r.delete(key)
    except Exception:  # noqa: BLE001 — 无 Redis 时即内存模式
        pass


@pytest.fixture(autouse=True)
def _isolated_login_limiters():
    """水密隔离：每个用例前后都清登录桶，既不继承残留也不污染后续用例。"""
    _reset_login_limiters()
    yield
    _reset_login_limiters()


def _login(client, identifier: str, password: str):
    return client.post("/api/auth/login", json={"identifier": identifier, "password": password})


def test_correct_password_locked_out_after_10_failures(client):
    """>=10 次错误后，正确密码也 429（锁定不再泄露猜测正确性）。"""
    _register(client)
    codes = [_login(client, "alice", WRONG).status_code for _ in range(10)]
    assert codes == [401] * 10, codes

    r = _login(client, "alice", PASSWORD)
    assert r.status_code == 429, r.text
    assert r.json()["detail"]["code"] == "RATE_LIMITED"


def test_lockout_is_checked_before_password(client):
    """锁定期间第 11 次起错误密码同样 429（先查限流，后验密码）。"""
    _register(client)
    for _ in range(10):
        assert _login(client, "alice", WRONG).status_code == 401
    codes = [_login(client, "alice", WRONG).status_code for _ in range(3)]
    assert codes == [429] * 3, codes


def test_correct_password_works_before_lockout(client):
    """正例：未达阈值前正确密码可登录；成功后失败额度清零（不累加）。"""
    _register(client)
    # 少量失败（未达阈值）
    for _ in range(9):
        assert _login(client, "alice", WRONG).status_code == 401
    # 正确密码仍可登录
    r = _login(client, "alice", PASSWORD)
    assert r.status_code == 200, r.text
    # 成功登录清零失败计数：再失败 9 次仍未锁定（若累加则早已 429）
    for _ in range(9):
        assert _login(client, "alice", WRONG).status_code == 401
    assert _login(client, "alice", PASSWORD).status_code == 200


def test_other_user_not_affected_by_per_identifier_lock(client):
    """按用户名锁定的桶不影响其他用户（同 IP 桶共 30 次额度，这里不触顶）。"""
    _register(client)
    client.post(
        "/api/auth/register",
        json={
            "username": "bob",
            "email": "bob@example.com",
            "password": PASSWORD,
            "invite_code": "letmein",
        },
    )
    for _ in range(10):
        assert _login(client, "alice", WRONG).status_code == 401
    # alice 已锁，bob 不受 per-identifier 桶影响
    r = _login(client, "bob", PASSWORD)
    assert r.status_code == 200, r.text


def test_memory_mode_lock_expires_after_window(client, monkeypatch):
    """内存回落（Redis 不可达）下锁定必须随窗口过期而解除，不得永久锁死。

    修复前：is_limited 内存分支忽略 expires_at，过期仍返回锁定；
    又因 login() 锁定期内直接 429、不再计数，计数永远无机会重置 →
    Redis 故障期间被锁过的用户在进程存活期内永久无法登录。
    """
    from backend.app.services import rate_limiter

    def _redis_down():
        raise RuntimeError("redis unavailable (test)")

    monkeypatch.setattr(rate_limiter, "get_sync_redis", _redis_down)

    _register(client)
    for _ in range(10):
        assert _login(client, "alice", WRONG).status_code == 401
    # 已锁定：正确密码也 429
    assert _login(client, "alice", PASSWORD).status_code == 429

    # 模拟 300s 窗口已过：把内存条目的 expires_at 改到过去
    key = rate_limiter._key("auth", "login:id:alice")
    count, _expires_at = rate_limiter._memory_store[key]
    assert count >= 10, "失败计数应落在内存回落存储里"
    rate_limiter._memory_store[key] = (count, time.time() - 1)

    r = _login(client, "alice", PASSWORD)
    assert r.status_code == 200, r.text


def test_ip_limit_never_blocks_correct_password(client):
    """IP 桶刷爆（30 次失败）后，同 IP 其他用户的正确密码仍必须 200。

    反代场景回归：全体用户共享 request.client.host，攻击者一人失败 30 次
    不应连累正常用户登录（预检只看用户名桶）。
    """
    _register(client)
    client.post(
        "/api/auth/register",
        json={
            "username": "bob",
            "email": "bob@example.com",
            "password": PASSWORD,
            "invite_code": "letmein",
        },
    )
    # 攻击者对不存在用户连错 30 次：每张卡的 per-id 桶只 +1，IP 桶 +30
    for i in range(30):
        assert _login(client, f"ghost{i}", WRONG).status_code == 401
    # IP 桶已超限，但 bob 密码正确：不得被预检拦截
    r = _login(client, "bob", PASSWORD)
    assert r.status_code == 200, r.text
    # 同 IP 再失败一次：IP 桶直接给 429（只影响错误密码）
    r = _login(client, "ghost30", WRONG)
    assert r.status_code == 429, r.text
    assert r.json()["detail"]["code"] == "RATE_LIMITED"
    # 429 之后 bob 的正确密码依旧畅通
    assert _login(client, "bob", PASSWORD).status_code == 200


def test_lockout_stops_bruteforce_not_oracle(client):
    """端到端：攻击者连续猜测拿到的永远是 429，没有 200/401 差异可观测。"""
    _register(client)
    observed = set()
    for i in range(15):
        guess = PASSWORD if i == 12 else WRONG  # 夹杂一次正确猜测
        observed.add(_login(client, "alice", guess).status_code)
    assert observed == {401, 429}, observed
    assert _login(client, "alice", PASSWORD).status_code == 429
