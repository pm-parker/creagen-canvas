

# ---- 审计批次二（P2）：归属记账 + 滚动配额 ----

def _auth(client):
    client.post(
        "/api/auth/register",
        json={
            "username": "up_quota", "email": "uq@x.io",
            "password": "password123", "invite_code": "letmein",
        },
    )
    r = client.post("/api/auth/login", json={"identifier": "up_quota", "password": "password123"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_upload_records_ownership(client, db_session):
    h = _auth(client)
    import base64

    png = base64.b64encode(b"\x89PNG fake-bytes").decode()
    r = client.post(
        "/api/uploads", json={"data_url": f"data:image/png;base64,{png}"}, headers=h
    )
    assert r.status_code == 201, r.text
    from backend.app.models import UploadRecord

    rec = db_session.query(UploadRecord).order_by(UploadRecord.id.desc()).first()
    assert rec is not None
    assert rec.path == r.json()["url"]
    assert rec.mime == "image/png"
    assert rec.size_bytes == len(b"\x89PNG fake-bytes")


def test_upload_quota_exceeded(client, db_session, monkeypatch):
    h = _auth(client)
    from datetime import datetime

    from backend.app.core.config import get_settings
    from backend.app.models import UploadRecord

    get_settings.cache_clear()
    monkeypatch.setattr(
        "backend.app.routers.uploads.get_settings",
        lambda: type("S", (), {"upload_quota_bytes": 100})(),
    )
    db_session.add(
        UploadRecord(
            user_id=_uid(client, h), path="/outputs/uploads/old.mp4",
            mime="video/mp4", size_bytes=95, created_at=datetime.utcnow(),
        )
    )
    db_session.commit()

    import base64

    payload = base64.b64encode(b"x" * 10).decode()
    r = client.post(
        "/api/uploads", json={"data_url": f"data:image/png;base64,{payload}"}, headers=h
    )
    assert r.status_code == 413
    assert r.json()["detail"]["code"] == "QUOTA_EXCEEDED"


def _uid(client, headers):
    me = client.get("/api/auth/me", headers=headers)
    return me.json()["id"]


def test_cleanup_media_downloads_removes_stale(tmp_path, monkeypatch):
    """远端素材暂存 >7 天清理；新文件保留。"""
    import os
    import time

    from backend.app.services.media_service import cleanup_media_downloads

    dl = tmp_path / "media" / "downloads"
    dl.mkdir(parents=True)
    stale = dl / "dl_stale.mp4"
    fresh = dl / "dl_fresh.mp4"
    stale.write_bytes(b"old")
    fresh.write_bytes(b"new")
    old_ts = time.time() - 8 * 86400
    os.utime(stale, (old_ts, old_ts))

    removed = cleanup_media_downloads(tmp_path)

    assert removed == 1
    assert not stale.exists()
    assert fresh.exists()
