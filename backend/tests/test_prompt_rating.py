"""内容分级（决策 2026-10-07：B 方案）：共享 mature 默认隐藏 + 管理员/owner 标记。

拍板：①治理范围 B（共享内容分级标记）②机制确认（rating 字段+列表过滤+开关+标记）
③生产库不动 ④首发物料不动。
"""

import uuid


def _register(client):
    name = f"rate_{uuid.uuid4().hex[:8]}"
    client.post(
        "/api/auth/register",
        json={
            "username": name, "email": f"{name}@x.io",
            "password": "password123", "invite_code": "letmein",
        },
    )
    r = client.post("/api/auth/login", json={"identifier": name, "password": "password123"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}, name


def _mk_prompt(db_session, *, user_id=None, rating=None, title="t"):
    from backend.app.models import Prompt

    p = Prompt(user_id=user_id, title=title, content="c", rating=rating)
    db_session.add(p)
    db_session.commit()
    return p


def test_list_hides_shared_mature_by_default(client, db_session):
    """共享 mature 默认隐藏；show_mature=true 才含；个人 mature 不受限。"""
    headers, uname = _register(client)
    from backend.app.models import User

    u = db_session.query(User).filter(User.username == uname).first()
    _mk_prompt(db_session, user_id=None, rating="mature", title="shared-mature")
    _mk_prompt(db_session, user_id=None, rating=None, title="shared-normal")
    _mk_prompt(db_session, user_id=u.id, rating="mature", title="own-mature")

    titles = [p["title"] for p in client.get("/api/prompts", headers=headers).json()["prompts"]]
    assert "shared-mature" not in titles
    assert "shared-normal" in titles
    assert "own-mature" in titles  # 个人条目不受限（中立立场）

    titles2 = [
        p["title"]
        for p in client.get("/api/prompts?show_mature=true", headers=headers).json()["prompts"]
    ]
    assert "shared-mature" in titles2


def test_rating_mark_permissions(client, db_session):
    """标记：种子共享条目仅 admin；个人条目 owner；枚举校验。"""
    headers, uname = _register(client)
    from backend.app.models import User

    u = db_session.query(User).filter(User.username == uname).first()
    seed = _mk_prompt(db_session, user_id=None, title="seed-p")
    own = _mk_prompt(db_session, user_id=u.id, title="own-p")

    # 普通用户标种子 → 403
    r = client.patch(
        f"/api/prompts/{seed.id}/rating", json={"rating": "mature"}, headers=headers
    )
    assert r.status_code == 403
    # owner 标自己的 → 200
    r2 = client.patch(
        f"/api/prompts/{own.id}/rating", json={"rating": "mature"}, headers=headers
    )
    assert r2.status_code == 200
    assert r2.json()["rating"] == "mature"
    # 非法枚举 → 422
    r3 = client.patch(
        f"/api/prompts/{own.id}/rating", json={"rating": "xxx"}, headers=headers
    )
    assert r3.status_code == 422
    # 不存在 → 404
    r4 = client.patch("/api/prompts/999999/rating", json={"rating": None}, headers=headers)
    assert r4.status_code == 404


def test_admin_marks_shared_and_clears(client, db_session):
    headers, uname = _register(client)
    from backend.app.models import User

    u = db_session.query(User).filter(User.username == uname).first()
    u.role = "admin"
    db_session.commit()
    seed = _mk_prompt(db_session, user_id=None, title="seed-a")

    r = client.patch(
        f"/api/prompts/{seed.id}/rating", json={"rating": "mature"}, headers=headers
    )
    assert r.status_code == 200
    # 标记后默认列表隐藏
    titles = [p["title"] for p in client.get("/api/prompts", headers=headers).json()["prompts"]]
    assert "seed-a" not in titles
    # null 清除
    r2 = client.patch(f"/api/prompts/{seed.id}/rating", json={"rating": None}, headers=headers)
    assert r2.status_code == 200
    titles2 = [p["title"] for p in client.get("/api/prompts", headers=headers).json()["prompts"]]
    assert "seed-a" in titles2
