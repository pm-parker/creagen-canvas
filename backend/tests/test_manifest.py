"""manifest 闸钩子测试：G1 校验 + readiness + 硬闸 422。"""







import json
import uuid


def _register_with_key(client, db_session):



    from backend.app.models import User







    name = f"ag_{uuid.uuid4().hex[:8]}"



    client.post(



        "/api/auth/register",



        json={"username": name, "email": f"{name}@x.io",



              "password": "Passw0rd!234", "invite_code": "letmein"},



    )



    r = client.post("/api/auth/login",



                    json={"identifier": name, "password": "Passw0rd!234"})



    _token = r.json()["access_token"]



    u = db_session.query(User).filter(User.username == name).first(
        )



    u.api_key = f"cg-test-{uuid.uuid4().hex[:16]}"



    db_session.commit()



    return {"X-API-Key": u.api_key}











GOOD_SCRIPT = json.dumps({"shots": [



    {"cid": "S01", "dur": 10, "dialogue": "订单是两个人的，你上周三说加班，可是订单显示两个人",

         "desc": "女子中景说话"},



    {"cid": "S02", "dur": 5, "dialogue": "", "desc": "空镜雨夜街道"},



    {"cid": "S03", "dur": 8, "dialogue": "", "desc": "男子低头沉默"},



]}, ensure_ascii=False)











def _setup_ready_manifest(client, h, pid):



    client.post(f"/api/agent/v1/projects/{pid}/manifest", headers=h)



    client.patch(f"/api/agent/v1/projects/{pid}/manifest/slots", headers=h, json={



        "slots": {



            "style_bible": {"status": "filled", "value": "雨夜蓝黑写实"},



            "characters": {"status": "filled", "value": ["/outputs/images/img_1_abc123.png",
                 "/outputs/images/img_2_def456.png"]},



            "scenes": {"status": "filled", "value": ["/outputs/images/img_3_789ghi.png"]},



            "script": {"status": "filled", "value": GOOD_SCRIPT},



            "audio_plan": {"status": "filled", "value": "BGM 站内 /outputs/bgm.mp3"},



            "delivery": {"status": "filled", "value": "16:9 720p 90s"},



            "probes": {"status": "filled", "value": "done"},



        }})



    client.patch(f"/api/agent/v1/projects/{pid}/manifest/probes", headers=h, json={



        "dialogue_route": "tts", "style_ok": True, "identity_ok": True})



    # v2: scenes 锚图证据 soft — 测试夹具用豁免留痕 (顺带验证 waive 通道)



    client.post(f"/api/agent/v1/projects/{pid}/manifest/waive", headers=h, json={



        "rule": "slot:scenes", "reason": "测试夹具: 场景证据以豁免留痕方式通过闸门"})











def test_manifest_template_and_upsert(client, db_session):



    from backend.tests.test_agent_api import _register_with_key



    h, _ = _register_with_key(client, db_session)



    pid = client.post("/api/agent/v1/projects", json={"title": "闸门"},



                      headers=h).json()["id"]



    # 未创建时返回模板预览



    r = client.get(f"/api/agent/v1/projects/{pid}/manifest", headers=h)



    assert r.status_code == 200 and r.json()["created"] is False



    assert len(r.json()["template"]) >= 8



    # 创建



    r = client.post(f"/api/agent/v1/projects/{pid}/manifest", headers=h)



    assert r.status_code == 201



    d = r.json()



    assert d["readiness"]["ready"] is False



    assert d["readiness"]["completeness"] == 0.0



    assert len(d["readiness"]["missing"]) == 7  # 8 槽中 probes 由探针判定











def test_g1_speed_and_spacial_rules(client, db_session):



    from backend.tests.test_agent_api import _register_with_key



    h, _ = _register_with_key(client, db_session)



    pid = client.post("/api/agent/v1/projects", json={"title": "G1"},



                      headers=h).json()["id"]



    client.post(f"/api/agent/v1/projects/{pid}/manifest", headers=h)



    # 语速超标 + 单人镜空间召唤词 + 匀速



    bad = json.dumps({"shots": [



        {"cid": "A", "dur": 10, "dialogue": "这句话有十五个字所以一定超标了你看",



         "desc": "单人特写"},



        {"cid": "B", "dur": 11, "dialogue": "三个字",



         "desc": "他把协议书推到对面"},



        {"cid": "C", "dur": 10, "dialogue": "", "desc": "空镜"},



    ]}, ensure_ascii=False)



    r = client.post(f"/api/agent/v1/projects/{pid}/manifest/g1", headers=h,



                    json={"script": bad})



    rules = {p["rule"] for p in r.json()["problems"]}



    assert "speed_band" in rules



    assert "spacial_summon" in rules



    assert "monotone_duration" in rules











def test_gate_blocks_video_when_not_ready(client, db_session):



    """strict 清单未 ready → generate(video) 422 MANIFEST_NOT_READY。"""



    from backend.tests.test_agent_api import _register_with_key



    h, _ = _register_with_key(client, db_session)



    pid = client.post("/api/agent/v1/projects", json={"title": "硬闸"},



                      headers=h).json()["id"]



    client.post(f"/api/agent/v1/projects/{pid}/manifest", headers=h)  # 空槽 strict



    r = client.post("/api/agent/v1/generate", headers=h, json={



        "project_id": pid, "type": "video",



        "message": "测试镜头", "model": "minimax-h3-video"})



    assert r.status_code == 422



    assert r.json()["detail"]["code"] == "MANIFEST_NOT_READY"



    assert r.json()["detail"]["completeness"] == 0.0











def test_gate_allows_when_ready(client, db_session, worker_db):



    from backend.tests.test_agent_api import _register_with_key



    h, _ = _register_with_key(client, db_session)



    pid = client.post("/api/agent/v1/projects", json={"title": "放行"},



                      headers=h).json()["id"]



    _setup_ready_manifest(client, h, pid)



    rd = client.get(f"/api/agent/v1/projects/{pid}/manifest", headers=h).json()



    assert rd["readiness"]["ready"] is True, rd["readiness"]



    r = client.post("/api/agent/v1/generate", headers=h, json={



        "project_id": pid, "type": "video",



        "message": "测试镜头", "model": "minimax-h3-video"})



    assert r.status_code == 202, r.text











def test_non_strict_bypasses_gate(client, db_session):



    from backend.tests.test_agent_api import _register_with_key



    h, _ = _register_with_key(client, db_session)



    pid = client.post("/api/agent/v1/projects", json={"title": "软闸"},



                      headers=h).json()["id"]



    client.post(f"/api/agent/v1/projects/{pid}/manifest", headers=h)



    client.patch(f"/api/agent/v1/projects/{pid}/manifest", headers=h,



                 json={"strict": False})



    r = client.post("/api/agent/v1/generate", headers=h, json={



        "project_id": pid, "type": "video",



        "message": "草稿镜", "model": "minimax-h3-video"})



    assert r.status_code == 202











def test_probes_tts_updates_audio_plan(client, db_session):



    from backend.tests.test_agent_api import _register_with_key



    h, _ = _register_with_key(client, db_session)



    pid = client.post("/api/agent/v1/projects", json={"title": "联动"},



                      headers=h).json()["id"]



    client.post(f"/api/agent/v1/projects/{pid}/manifest", headers=h)



    r = client.patch(f"/api/agent/v1/projects/{pid}/manifest/probes", headers=h,



                     json={"dialogue_route": "tts"})



    note = r.json()["probes"]



    assert note["dialogue_route"] == "tts"











def test_no_manifest_legacy_project_passes(client, db_session):



    """老项目无清单 → 不拦（兼容）。"""



    from backend.tests.test_agent_api import _register_with_key



    h, _ = _register_with_key(client, db_session)



    pid = client.post("/api/agent/v1/projects", json={"title": "老项目"},



                      headers=h).json()["id"]



    r = client.post("/api/agent/v1/generate", headers=h, json={



        "project_id": pid, "type": "video",



        "message": "x", "model": "minimax-h3-video"})



    assert r.status_code == 202
