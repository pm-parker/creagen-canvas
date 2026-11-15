"""分镜模板契约（0.10）：CRUD / 隔离 / from-template 复用。"""

BASE = "/api/storyboard-templates"


def _auth(client, username):
    r = client.post(
        "/api/auth/register",
        json={
            "username": username,
            "email": f"{username}@x.io",
            "password": "password123",
            "invite_code": "letmein",
        },
    )
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_template_crud_roundtrip(client):
    h = _auth(client, "tpl_a")
    segs = [
        {
            "prompt": "opening shot", "camera_motion": "缓推",
            "mood": "平静", "audio_cue": "雨声", "target_duration_s": 6,
        },
        {
            "prompt": "climax shot", "camera_motion": "拉远",
            "mood": "紧张", "audio_cue": "鼓点", "target_duration_s": 10,
        },
    ]
    r = client.post(
        BASE,
        json={"title": "雨夜面馆结构", "description": "三段式情绪模板", "segments": segs},
        headers=h,
    )
    assert r.status_code == 201, r.text
    tid = r.json()["template"]["id"]
    assert len(r.json()["template"]["segments"]) == 2

    r = client.get(BASE, headers=h)
    assert [t["title"] for t in r.json()["templates"]] == ["雨夜面馆结构"]

    assert client.delete(f"{BASE}/{tid}", headers=h).status_code == 204
    assert client.get(BASE, headers=h).json()["templates"] == []


def test_template_isolation(client):
    h1 = _auth(client, "tpl_b1")
    h2 = _auth(client, "tpl_b2")
    tid = client.post(
        BASE, json={"title": "私有模板", "segments": [{"prompt": "x"}]}, headers=h1
    ).json()["template"]["id"]
    assert client.get(BASE, headers=h2).json()["templates"] == []
    assert client.delete(f"{BASE}/{tid}", headers=h2).status_code == 404


def test_from_template_creates_storyboard(client, db_session):
    h = _auth(client, "tpl_c")
    segs = [
        {"prompt": "opening shot", "camera_motion": "缓推", "mood": "平静",
         "audio_cue": "雨声", "target_duration_s": 6},
        {"prompt": "middle shot", "target_duration_s": 6},
        {"prompt": "climax shot", "target_duration_s": 10},
    ]
    tid = client.post(
        BASE, json={"title": "三段式", "segments": segs}, headers=h
    ).json()["template"]["id"]

    r = client.post(
        "/api/storyboards/from-template",
        json={"template_id": tid, "title": "新片·用三段式"},
        headers=h,
    )
    assert r.status_code == 201, r.text
    sb = r.json()["storyboard"]
    assert sb["title"] == "新片·用三段式"
    assert len(sb["segments"]) == 3
    assert [s["prompt"] for s in sb["segments"]] == ["opening shot", "middle shot", "climax shot"]
    assert sb["segments"][0]["camera_motion"] == "缓推"
    # 产物/状态为空（模板只带结构）
    assert all(s["status"] in (None, "pending") for s in sb["segments"])
    assert all(not s["output_video_url"] for s in sb["segments"])
    # 模板本身不受影响
    templates = client.get(BASE, headers=h).json()["templates"]
    assert any(t["id"] == tid for t in templates)


def test_from_template_404_not_owned(client):
    h1 = _auth(client, "tpl_d1")
    h2 = _auth(client, "tpl_d2")
    tid = client.post(
        BASE, json={"title": "别人的", "segments": [{"prompt": "x"}]}, headers=h1
    ).json()["template"]["id"]
    r = client.post(
        "/api/storyboards/from-template", json={"template_id": tid}, headers=h2
    )
    assert r.status_code == 404
