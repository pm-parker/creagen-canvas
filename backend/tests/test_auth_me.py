
def test_me_has_no_decorative_balance(client):
    """P3：balance 列全库零写入（纯装饰）——/me 不得返回假余额。"""
    client.post(
        "/api/auth/register",
        json={
            "username": "no_bal", "email": "nb@x.io",
            "password": "password123", "invite_code": "letmein",
        },
    )
    r = client.post("/api/auth/login", json={"identifier": "no_bal", "password": "password123"})
    token = r.json()["access_token"]
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert "balance" not in me.json()


def test_user_to_dict_has_no_balance(db_session):
    """D2：balance 列物理移除——模型 to_dict 不再携带假余额字段。

    前序：P3 裁定假余额违背信任原则（schemas/auth.py 注释）；/me 已由 response_model 剥离；
    本测试钉住模型层，防止 to_dict 回流该字段。
    """
    from backend.app.models import User

    u = User(username="no_bal_dict", email="nbd@x.io", password_hash="x")
    db_session.add(u)
    db_session.commit()
    assert "balance" not in u.to_dict()
