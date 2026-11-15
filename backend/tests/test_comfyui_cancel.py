"""ComfyUI 僵尸 job 根治（2026-10-08 升级版五件套）的测试。



链路：provider → shim(8311) → ComfyUI(8198)。prompt_id 在 shim 侧，

撤销经 shim 新增的 /internal/jobs/{task_id} 端点完成；shim 轮询循环

协作取消（收到撤销立即退出——provider 的 shield 孤儿随之消亡）。

"""



from __future__ import annotations

import importlib.util
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

_SHIM = Path(__file__).resolve().parents[2] / "scripts" / "h3_video_shim.py"

if not _SHIM.is_file():
    pytest.skip("shim script missing in public snapshot", allow_module_level=True)








def _load_shim():

    spec = importlib.util.spec_from_file_location("h3_video_shim_test", _SHIM)
    assert spec is not None and spec.loader is not None  # 收窄 Optional

    mod = importlib.util.module_from_spec(spec)

    import sys as _sys

    _sys.modules[spec.name] = mod  # pydantic 需经 sys.modules 解析注解

    spec.loader.exec_module(mod)

    return mod





@pytest.fixture()

def shim(monkeypatch):

    mod = _load_shim()

    mod._JOBS.clear()

    mod._CANCELLED.clear()

    from typing import Any



    calls: dict[str, Any] = {"delete": [], "interrupt": 0}



    def fake_post(url, json=None, timeout=None, headers=None):  # noqa: ANN001

        class R:

            status_code = 200



            def raise_for_status(self) -> None:

                return None



            def json(self):

                return {"prompt_id": "pid-123"}



        if "/prompt" in url:

            return R()

        if "/interrupt" in url:

            calls["interrupt"] += 1

            return R()

        raise AssertionError(f"unexpected POST {url}")



    def fake_request(method, url, json=None, timeout=None):  # noqa: ANN001

        if method == "DELETE":

            calls["delete"].append((url, json))

        return None



    def fake_delete(url, json=None, timeout=None):  # noqa: ANN001

        calls["delete"].append((url, json))

        return None



    def fake_get(url, timeout=None):  # noqa: ANN001

        class R:

            status_code = 200



            def json(self):

                return {}  # history 永不完成 → 等待循环挂起



        return R()



    monkeypatch.setattr(mod.httpx, "post", fake_post)

    monkeypatch.setattr(mod.httpx, "delete", fake_delete)

    monkeypatch.setattr(mod.httpx, "request", fake_request)

    monkeypatch.setattr(mod.httpx, "get", fake_get)

    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)

    mod._test_calls = calls  # type: ignore[attr-defined]  # 测试注入，shim 模块无此属性声明

    return mod





def test_shim_registers_and_cancels_job(shim):

    """chat 带 X-Creagen-Task-Id → 登记 pid；DELETE /internal/jobs 精确撤销

    pending（DELETE /queue 带 pid）且等待循环协作退出。"""

    from fastapi.testclient import TestClient



    client = TestClient(shim.app)

    # 造一个已登记的等待 job（直接登记，避免阻塞式 chat 的并发测试复杂度）

    shim._JOBS["777"] = {"pid": "pid-777", "ts": time.time()}



    r = client.delete("/internal/jobs/777")

    assert r.status_code == 200

    assert r.json()["cancelled"] is True

    # 精确删 pending：ComfyUI DELETE /queue body 带 prompt_id

    url, body = shim._test_calls["delete"][0]

    assert "/queue" in url and body == {"delete": ["pid-777"]}

    # 不 interrupt（默认防误伤）

    assert shim._test_calls["interrupt"] == 0

    # 协作取消标记已立（等待循环下一轮退出）

    assert "777" in shim._CANCELLED





def test_shim_cancel_with_interrupt(shim):

    """收尸场景允许 interrupt（全孤儿时）。"""

    from fastapi.testclient import TestClient



    client = TestClient(shim.app)

    shim._JOBS["888"] = {"pid": "pid-888", "ts": time.time()}

    r = client.delete("/internal/jobs/888?interrupt=true")

    assert r.status_code == 200

    assert shim._test_calls["interrupt"] == 1





def test_shim_cancel_unknown_job(shim):

    """未知 task_id（shim 重启后映射丢失）→ 幂等成功、不炸。"""

    from fastapi.testclient import TestClient



    client = TestClient(shim.app)

    r = client.delete("/internal/jobs/404")

    assert r.status_code == 200

    assert r.json() == {"cancelled": False, "known": False}





def test_chat_respects_cancel_flag(shim):

    """等待循环协作取消：已标记撤销的 job，chat 立即返回失败。"""

    from fastapi.testclient import TestClient



    client = TestClient(shim.app)

    shim._JOBS["999"] = {"pid": "pid-999", "ts": time.time()}

    shim._CANCELLED.add("999")

    r = client.post(

        "/v1/chat/completions",

        json={"messages": [{"role": "user", "content": "测试"}]},

        headers={"X-Creagen-Task-Id": "999"},

    )

    content = r.json()["choices"][0]["message"]["content"]

    assert "撤销" in content





def test_provider_cancel_remote_job(shim, monkeypatch):

    """provider.cancel_remote_job：调 shim 撤销端点（5s 短超时）。"""

    from backend.app.providers.minimax_h3_local import H3LocalVideoProvider



    sent = {}



    class FakeResp:

        status_code = 200



        def json(self):

            return {"cancelled": True}



    class FakeClient:

        def __init__(self, timeout=None):

            self.timeout = timeout



        async def __aenter__(self):

            return self



        async def __aexit__(self, *a):

            return None



        async def delete(self, url, params=None):

            sent["url"] = url

            sent["timeout"] = self.timeout

            return FakeResp()



    monkeypatch.setattr(

        "backend.app.providers.minimax_h3_local.httpx.AsyncClient", FakeClient

    )

    p = H3LocalVideoProvider.__new__(H3LocalVideoProvider)

    p.base_url = "http://asus:8311"

    import asyncio



    ok = asyncio.run(p.cancel_remote_job(777))

    assert ok is True

    assert "/internal/jobs/777" in sent["url"]





def test_cleanup_reaps_stale_and_cancels_remote(client, worker_db, db_session, monkeypatch):

    """收尸：generating 超 30 分钟 → 条件 UPDATE 标 failed + 远程撤销(interrupt)

    + 发布终态事件。已完成任务不被覆盖。"""

    from backend.app.models import Task



    t_old = Task(user_id=1, type="video", prompt="旧", model="minimax-h3-video")

    t_done = Task(user_id=1, type="video", prompt="完", model="minimax-h3-video")

    db_session.add_all([t_old, t_done])

    db_session.flush()

    t_old.status, t_old.started_at = "generating", datetime.utcnow() - timedelta(minutes=31)

    t_done.status = "completed"

    t_done.output_url, t_done.completed_at = "/outputs/x.mp4", datetime.utcnow()

    db_session.commit()



    remote = {"calls": []}



    class FakeProvider:

        async def cancel_remote_job(self, task_id, interrupt=False):  # noqa: ANN001

            remote["calls"].append((task_id, interrupt))

            return True



    import backend.app.services.provider_service as ps



    def fake_resolve(db, user_id, type_, model, channel_id=None):  # noqa: ANN001

        return FakeProvider(), None



    monkeypatch.setattr(ps, "resolve_provider_for_task", fake_resolve)

    import asyncio

    import backend.worker as w



    asyncio.run(w._cleanup_stale_tasks())



    db_session.expire_all()

    old = db_session.get(Task, t_old.id)

    done = db_session.get(Task, t_done.id)

    assert old.status == "failed"

    assert old.error_msg is not None and old.error_msg != ""

    assert done.status == "completed"  # 终态写保护：不被收尸覆盖

    assert (t_old.id, True) in remote["calls"]  # 收尸场景 interrupt=True





def test_cancel_endpoint_triggers_remote_cancel(client, db_session, monkeypatch, worker_db):

    """取消端点：用户取消 → 经 provider 精确撤销（interrupt=False 防误伤）。"""

    from backend.app.models import Task



    t = Task(user_id=1, type="video", prompt="p", model="minimax-h3-video", status="generating")

    db_session.add(t)

    db_session.commit()



    remote = {"calls": []}



    class FakeProvider:

        async def cancel_remote_job(self, task_id, interrupt=False):  # noqa: ANN001

            remote["calls"].append((task_id, interrupt))

            return True



    import backend.app.services.provider_service as ps



    def fake_resolve(db, user_id, type_, model, channel_id=None):  # noqa: ANN001

        return FakeProvider(), None



    monkeypatch.setattr(ps, "resolve_provider_for_task", fake_resolve)



    headers = _auth_headers(client)

    r = client.delete(f"/api/tasks/{t.id}", headers=headers)

    assert r.status_code == 200

    assert remote["calls"] and remote["calls"][0][1] is False  # 精确删，不 interrupt





def _auth_headers(client):  # noqa: ANN001

    from backend.tests.test_task_flow import _auth_headers as _h



    return _h(client)





def test_h3_local_gate_limit_is_one():

    """修（2026-10-08 二批事故）：本地单卡双并发必双双超时（15s i2v 独跑 13min、

    双并发各 >20min 撞超时线）——串行吞吐最高零超时。本地 provider 闸门=1。"""

    from backend.app.providers.minimax_h3_local import H3LocalVideoProvider



    p = H3LocalVideoProvider.__new__(H3LocalVideoProvider)

    assert p.gate_limit == 1





def test_worker_gate_uses_provider_limit(client, worker_db, monkeypatch):

    """worker 闸门限数取 provider.gate_limit（本地=1，远端默认 2）。"""

    import inspect

    import backend.worker as w



    src = inspect.getsource(w.run_generation_task)

    assert 'getattr(provider, "gate_limit"' in src or "provider.gate_limit" in src
