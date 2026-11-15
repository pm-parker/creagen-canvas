"""时间序列化契约：所有 API 时间字段必须带 UTC 时区后缀。

库内 naive DateTime 一律视为 UTC（连接会话时区=UTC 的既有约定）；
序列化若不带时区，前端 new Date() 按本地解析会差 8 小时（走查 P0 #2）。
"""

from datetime import UTC, datetime

from backend.app.core.times import iso_utc

REGISTER_OK = {
    "username": "bob",
    "email": "bob@example.com",
    "password": "password123",
    "invite_code": "letmein",
}


def _auth_headers(client) -> dict[str, str]:
    r = client.post("/api/auth/register", json=REGISTER_OK)
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_iso_utc_helper():
    assert iso_utc(None) is None
    naive = datetime(2026, 10, 2, 15, 31, 9)
    assert iso_utc(naive) == "2026-10-02T15:31:09+00:00"
    aware = datetime(2026, 10, 2, 15, 31, 9, tzinfo=UTC)
    assert iso_utc(aware) == "2026-10-02T15:31:09+00:00"


def test_task_created_at_is_tz_aware(client):
    headers = _auth_headers(client)
    r = client.post(
        "/api/tasks",
        json={"type": "image", "prompt": "x", "model": "fake-image"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    created = r.json()["task"]["created_at"]
    assert created is not None
    assert created.endswith("+00:00"), f"时间无时区后缀: {created}"
    assert datetime.fromisoformat(created).tzinfo is not None


def test_task_list_created_at_is_tz_aware(client):
    headers = _auth_headers(client)
    client.post(
        "/api/tasks",
        json={"type": "image", "prompt": "x", "model": "fake-image"},
        headers=headers,
    )
    r = client.get("/api/tasks?limit=5", headers=headers)
    assert r.status_code == 200, r.text
    tasks = r.json()["tasks"]
    assert tasks, "应有任务"
    for t in tasks:
        assert t["created_at"].endswith("+00:00"), f"时间无时区后缀: {t['created_at']}"
