"""角色资产库契约（0.9.3）：CRUD / 用户隔离 / 同名唯一 / 定妆照。"""

BASE = "/api/characters"


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


def test_character_crud_roundtrip(client):
    h = _auth(client, "char_a")

    r = client.post(
        BASE,
        json={
            "name": "阿珍",
            "description": "40yo woman, flour-dusted apron",
            "ref_image_url": "/outputs/uploads/z.png",
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    cid = r.json()["character"]["id"]
    assert r.json()["character"]["ref_image_url"] == "/outputs/uploads/z.png"

    r = client.get(BASE, headers=h)
    assert [c["name"] for c in r.json()["characters"]] == ["阿珍"]

    r = client.patch(
        f"{BASE}/{cid}", json={"description": "42yo, grey streak"}, headers=h
    )
    assert r.json()["character"]["description"] == "42yo, grey streak"

    assert client.delete(f"{BASE}/{cid}", headers=h).status_code == 204
    assert client.get(BASE, headers=h).json()["characters"] == []


def test_character_duplicate_name_409(client):
    h = _auth(client, "char_b")
    client.post(BASE, json={"name": "小雨", "description": "d1"}, headers=h)
    r = client.post(BASE, json={"name": "小雨", "description": "dup"}, headers=h)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "CHARACTER_NAME_EXISTS"


def test_character_user_isolation(client):
    h1 = _auth(client, "char_c1")
    h2 = _auth(client, "char_c2")
    cid = client.post(
        BASE, json={"name": "私有角色", "description": "grey cat"}, headers=h1
    ).json()["character"]["id"]

    assert client.get(BASE, headers=h2).json()["characters"] == []
    assert client.patch(f"{BASE}/{cid}", json={"description": "x"}, headers=h2).status_code == 404
    assert client.delete(f"{BASE}/{cid}", headers=h2).status_code == 404


def test_character_validation(client):
    h = _auth(client, "char_d")
    assert client.post(BASE, json={"name": "  ", "description": "x"}, headers=h).status_code == 422
    assert client.post(BASE, json={"name": "ok", "description": ""}, headers=h).status_code == 422


# ---- T2: attach 带入分镜 ----


def _mk_storyboard_with_split(client, h):
    sid = client.post(
        "/api/storyboards", json={"short_description": "雨夜面馆故事"}, headers=h
    ).json()["storyboard"]["id"]
    # 直接入 profiles（模拟拆分产出）


    # 用 API 不行（profiles 只能经 split）——走 db
    return sid


def test_attach_characters_merges_profiles(client, db_session):
    h = _auth(client, "char_attach_a")
    c1 = client.post(
        BASE, json={"name": "阿珍", "description": "40yo, apron"}, headers=h
    ).json()["character"]
    sid = client.post(
        "/api/storyboards", json={"short_description": "雨夜面馆"}, headers=h
    ).json()["storyboard"]["id"]

    r = client.post(
        f"/api/storyboards/{sid}/characters/attach",
        json={"character_ids": [c1["id"]]},
        headers=h,
    )
    assert r.status_code == 200, r.text
    profiles = r.json()["storyboard"]["character_profiles"]
    assert profiles == [{"name": "阿珍", "description": "40yo, apron"}]

    # 幂等：重复带入不重复
    r = client.post(
        f"/api/storyboards/{sid}/characters/attach",
        json={"character_ids": [c1["id"]]},
        headers=h,
    )
    assert len(r.json()["storyboard"]["character_profiles"]) == 1


def test_attach_update_refreshes_profile(client, db_session):
    h = _auth(client, "char_attach_b")
    c1 = client.post(
        BASE, json={"name": "小雨", "description": "18yo student"}, headers=h
    ).json()["character"]
    sid = client.post(
        "/api/storyboards", json={"short_description": "离别故事"}, headers=h
    ).json()["storyboard"]["id"]
    client.post(
        f"/api/storyboards/{sid}/characters/attach", json={"character_ids": [c1["id"]]}, headers=h
    )
    # 库中编辑描述 → 再次带入刷新快照
    client.patch(f"{BASE}/{c1['id']}", json={"description": "19yo, college freshman"}, headers=h)
    r = client.post(
        f"/api/storyboards/{sid}/characters/attach", json={"character_ids": [c1["id"]]}, headers=h
    )
    profiles = r.json()["storyboard"]["character_profiles"]
    assert profiles[0]["description"] == "19yo, college freshman"


def test_delete_asset_keeps_attached_profile(client, db_session):
    h = _auth(client, "char_attach_c")
    c1 = client.post(
        BASE, json={"name": "老王", "description": "shop owner"}, headers=h
    ).json()["character"]
    sid = client.post(
        "/api/storyboards", json={"short_description": "面馆日常"}, headers=h
    ).json()["storyboard"]["id"]
    client.post(
        f"/api/storyboards/{sid}/characters/attach", json={"character_ids": [c1["id"]]}, headers=h
    )
    client.delete(f"{BASE}/{c1['id']}", headers=h)
    # 分镜快照不受影响
    sb = client.get(f"/api/storyboards/{sid}", headers=h).json()["storyboard"]
    assert sb["character_profiles"][0]["name"] == "老王"


def test_attach_storyboard_isolation(client):
    h1 = _auth(client, "char_attach_d1")
    h2 = _auth(client, "char_attach_d2")
    c1 = client.post(
        BASE, json={"name": "甲角色", "description": "d"}, headers=h1
    ).json()["character"]
    sid = client.post(
        "/api/storyboards", json={"short_description": "别人的分镜"}, headers=h2
    ).json()["storyboard"]["id"]
    r = client.post(
        f"/api/storyboards/{sid}/characters/attach",
        json={"character_ids": [c1["id"]]},
        headers=h1,
    )
    assert r.status_code == 404
