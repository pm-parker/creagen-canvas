

def test_compose_endpoint_validates(client, db_session, monkeypatch):
    """合成端点契约：<2 段 422；正常路径（mock ffmpeg）。"""

    from backend.app.models import User
    from backend.app.services.auth_service import create_access_token

    u = User(username="cmp", email="cmp@t.local", password_hash="x")
    db_session.add(u)
    db_session.commit()
    h = {"Authorization": f"Bearer {create_access_token(u)}"}

    r = client.post("/api/media/compose", headers=h, json={"urls": ["/outputs/videos/a.mp4"]})
    assert r.status_code == 422

    from pathlib import Path as _P

    from backend.app.services import media_service as ms

    calls = {}

    def fake_run(args, **kw):  # noqa: ANN001
        calls["args"] = args
        out = _P(args[-1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"mp4")
        return True

    monkeypatch.setattr(ms, "_run_ffmpeg", fake_run)
    monkeypatch.setattr(ms, "ensure_local", lambda u, r=None: _P(f"/tmp/{u.split('/')[-1]}"))
    monkeypatch.setattr(ms, "_ffprobe_dims", lambda v: (1280, 720))

    r2 = client.post(
        "/api/media/compose",
        headers=h,
        json={"urls": ["/outputs/videos/a.mp4", "/outputs/videos/b.mp4"]},
    )
    assert r2.status_code == 200
    assert r2.json()["url"].startswith("/outputs/")
    # ffmpeg 参数含 concat 归一链
    assert "concat=n=2" in " ".join(calls["args"])
