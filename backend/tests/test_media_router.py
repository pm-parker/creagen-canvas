"""视频轻加工路由契约（P2）：取帧/截取端点鉴权与 envelope。"""

REGISTER = {
    "username": "mediauser",
    "email": "media@example.com",
    "password": "password123",
    "invite_code": "letmein",
}


def _headers(client) -> dict[str, str]:
    r = client.post("/api/auth/register", json=REGISTER)
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_media_requires_auth(client):
    r1 = client.post("/api/media/frame", json={"url": "/outputs/v.mp4", "time": 0})
    r2 = client.post("/api/media/trim", json={"url": "/outputs/v.mp4", "start": 0, "end": 1})
    assert r1.status_code == 401
    assert r2.status_code == 401


def test_frame_endpoint_returns_url(client, monkeypatch, tmp_path):
    (tmp_path / "videos").mkdir()
    (tmp_path / "videos" / "v.mp4").write_bytes(b"V")

    from backend.app.routers import media as media_router
    from backend.app.services import media_service

    monkeypatch.setattr(media_router, "_outputs_root", lambda: tmp_path)
    monkeypatch.setattr(
        media_service, "extract_frame", lambda video, t, root: root / "media" / "frame_x.png"
    )
    (tmp_path / "media").mkdir(exist_ok=True)
    (tmp_path / "media" / "frame_x.png").write_bytes(b"P")

    r = client.post(
        "/api/media/frame",
        json={"url": "/outputs/videos/v.mp4", "time": 1.2},
        headers=_headers(client),
    )
    assert r.status_code == 200, r.text
    assert r.json()["url"] == "/outputs/media/frame_x.png"


def test_trim_endpoint_invalid_params_envelope(client, monkeypatch, tmp_path):
    (tmp_path / "videos").mkdir()
    (tmp_path / "videos" / "v.mp4").write_bytes(b"V")
    monkeypatch.setattr("backend.app.routers.media._outputs_root", lambda: tmp_path)
    r = client.post(
        "/api/media/trim",
        json={"url": "/outputs/videos/v.mp4", "start": 3, "end": 1},
        headers=_headers(client),
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "MEDIA_PARAMS_INVALID"


def test_media_error_maps_to_envelope(client, monkeypatch, tmp_path):
    from backend.app.routers import media as media_router
    from backend.app.services import media_service

    monkeypatch.setattr(media_router, "_outputs_root", lambda: tmp_path)
    monkeypatch.setattr(
        media_service,
        "ensure_local",
        lambda url, root: (_ for _ in ()).throw(
            media_service.MediaError("MEDIA_NOT_FOUND", "素材不存在", "请确认产物仍在")
        ),
    )
    r = client.post(
        "/api/media/frame",
        json={"url": "/outputs/videos/gone.mp4", "time": 0},
        headers=_headers(client),
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "MEDIA_NOT_FOUND"


def _upload(client, headers, mime: str, payload: bytes):
    import base64

    return client.post(
        "/api/uploads",
        json={"data_url": f"data:{mime};base64,{base64.b64encode(payload).decode()}"},
        headers=headers,
    )


def test_upload_accepts_video_and_text(client):
    headers = _headers(client)
    r = _upload(client, headers, "video/mp4", b"MP4DATA")
    assert r.status_code == 201, r.text
    assert r.json()["url"].endswith(".mp4")
    r = _upload(client, headers, "text/plain", "你好画布".encode())
    assert r.status_code == 201
    assert r.json()["url"].endswith(".txt")


def test_upload_video_size_cap(client):
    headers = _headers(client)
    r = _upload(client, headers, "video/mp4", b"x" * (201 * 1024 * 1024))
    assert r.status_code == 413


def test_media_rate_limited(client, monkeypatch, tmp_path):
    """H3：media 处理限流——超限 429（防 CPU DoS）。
    用唯一 scope 防跨测试残留（CI Redis 共享计数器）。"""
    from backend.app.services import rate_limiter

    rate_limiter._memory_store.clear()
    # 清除 Redis 中的 media 限流计数（CI 环境用 Redis）
    try:
        for key in rate_limiter.get_sync_redis().keys("canvas:ratelimit:media:*"):
            rate_limiter.get_sync_redis().delete(key)
    except Exception:
        pass
    from backend.app.routers import media as media_router
    from backend.app.services import media_service

    monkeypatch.setattr(media_router, "_outputs_root", lambda: tmp_path)
    (tmp_path / "media").mkdir()
    (tmp_path / "media" / "seed.mp4").write_bytes(b"V")
    monkeypatch.setattr(
        media_service, "trim", lambda v, s, e, root: tmp_path / "media" / "out.mp4"
    )
    (tmp_path / "media" / "out.mp4").write_bytes(b"O")

    headers = _headers(client)
    codes = [
        client.post(
            "/api/media/trim",
            json={"url": "/outputs/media/seed.mp4", "start": 0, "end": 1},
            headers=headers,
        ).status_code
        for _ in range(14)
    ]
    assert codes[:12] == [200] * 12, codes
    assert all(c == 429 for c in codes[12:]), codes


def test_upload_rate_limited(client):
    """上传限流 60/分钟（留足批量切分/批量调整余量）。"""
    from backend.app.services import rate_limiter

    rate_limiter._memory_store.clear()
    try:
        for key in rate_limiter.get_sync_redis().keys("canvas:ratelimit:uploads:*"):
            rate_limiter.get_sync_redis().delete(key)
    except Exception:
        pass
    import base64

    headers = _headers(client)
    data = f"data:image/png;base64,{base64.b64encode(b'x').decode()}"
    codes = [
        client.post("/api/uploads", json={"data_url": data}, headers=headers).status_code
        for _ in range(62)
    ]
    assert codes[:60] == [201] * 60
    assert all(c == 429 for c in codes[60:])


class TestAssetsSearch:
    """P1-6: 资产搜索（按提示词）+ 分页正确性。"""

    def test_search_filters_by_prompt(self, client, db_session):
        from datetime import datetime

        from backend.app.models import Task

        for i, kw in enumerate(["赛博朋克城市", "水墨山水", "赛博猫"]):
            db_session.add(Task(
                user_id=1, type="image", prompt=f"{kw} 高清", model="m", status="completed",
                output_url=f"/outputs/{i}.webp", completed_at=datetime(2026, 10, 1, 12, i),
            ))
        db_session.commit()

        r = client.get("/api/assets?search=赛博", headers=_headers(client))
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 2
        assert all("赛博" in a["prompt"] for a in data["assets"])

    def test_pagination_beyond_first_page(self, client, db_session):
        """offset 分页：第 2 页应返回数据（修复双重 offset bug）。"""
        from datetime import datetime, timedelta

        from backend.app.models import Task

        base = datetime(2026, 10, 1)
        for i in range(8):
            db_session.add(Task(
                user_id=1, type="image", prompt=f"图 {i}", model="m", status="completed",
                output_url=f"/outputs/p{i}.webp", completed_at=base + timedelta(minutes=i),
            ))
        db_session.commit()

        headers = _headers(client)
        r1 = client.get("/api/assets?limit=5&offset=0", headers=headers)
        r2 = client.get("/api/assets?limit=5&offset=5", headers=headers)
        assert len(r1.json()["assets"]) == 5
        assert len(r2.json()["assets"]) == 3  # 修复前：0（SQL offset + Python 二次切片）
        urls1 = {a["url"] for a in r1.json()["assets"]}
        urls2 = {a["url"] for a in r2.json()["assets"]}
        assert not (urls1 & urls2)


class TestAssetsRefUrl:
    """P2-10: 资产返回参考图（对比视图数据源）。"""

    def test_edit_task_exposes_ref_url(self, client, db_session):
        from datetime import datetime

        from backend.app.models import Task

        db_session.add(Task(
            user_id=1, type="image", prompt="改成油画", model="m", status="completed",
            output_url="/outputs/out.webp", completed_at=datetime(2026, 10, 1),
            params='{"image": "/outputs/src.webp"}',
        ))
        db_session.commit()

        r = client.get("/api/assets", headers=_headers(client))
        a = r.json()["assets"][0]
        assert a["ref_url"] == "/outputs/src.webp"

    def test_first_frame_video_exposes_ref(self, client, db_session):
        from datetime import datetime

        from backend.app.models import Task

        db_session.add(Task(
            user_id=1, type="video", prompt="让画面动起来", model="m", status="completed",
            output_url="/outputs/v.mp4", completed_at=datetime(2026, 10, 1),
            params='{"first_frame_image": "/outputs/ff.png"}',
        ))
        db_session.commit()

        r = client.get("/api/assets", headers=_headers(client))
        assert r.json()["assets"][0]["ref_url"] == "/outputs/ff.png"

    def test_t2i_no_ref(self, client, db_session):
        from datetime import datetime

        from backend.app.models import Task

        db_session.add(Task(
            user_id=1, type="image", prompt="纯文生图", model="m", status="completed",
            output_url="/outputs/t2i.webp", completed_at=datetime(2026, 10, 1),
        ))
        db_session.commit()

        r = client.get("/api/assets", headers=_headers(client))
        assert r.json()["assets"][0].get("ref_url") in (None, "")


def test_media_endpoints_reject_remote_urls(client):
    """审计批次三（P1）：media 四端点 url 直通 ensure_local http 分支——任意远端
    会被服务端下载且经 ffmpeg 产物静态回取（SSRF 读取原语）→ 仅放行站内 /outputs/。"""
    client.post(
        "/api/auth/register",
        json={
            "username": "media_rl", "email": "mr@x.io",
            "password": "password123", "invite_code": "letmein",
        },
    )
    tok = client.post(
        "/api/auth/login", json={"identifier": "media_rl", "password": "password123"}
    ).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    for path in ("/api/media/frame", "/api/media/subtitles", "/api/media/zoom", "/api/media/trim"):
        body = {"url": "http://169.254.169.254/latest/meta-data", "time": 0}
        if path.endswith("subtitles"):
            body = {"url": "http://internal:8080/x.mp4", "text": "hi"}
        elif path.endswith("zoom"):
            body = {"url": "http://internal:8080/x.mp4", "factor": 2.0}
        elif path.endswith("trim"):
            body = {"url": "http://internal:8080/x.mp4", "start": 0, "end": 1}
        r = client.post(path, json=body, headers=h)
        assert r.status_code == 422, (path, r.text)
        assert r.json()["detail"]["code"] == "MEDIA_URL_INVALID"
