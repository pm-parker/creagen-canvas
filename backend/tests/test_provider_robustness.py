"""provider 健壮性横向加固（2026-10-08 五项）测试。



P1 H3Warehouse：下载独立超时 300s + 轮询协作取消（cancel_check 注入）

P2 mark_started 终态写保护（477 复活实证）

P3 Agent 入口视频串行校验（422 提前拒绝，教育性文案）

P4 超时常量集中声明（序关系单点约束）

P5 撤销协议推广：openai_compat 统一发任务头 + 尝试撤销；zimage shim 加端点

"""



from __future__ import annotations

import asyncio
import importlib.util
import inspect
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from backend.app.models import Task

_ZSHIM = Path(__file__).resolve().parents[2] / "scripts" / "zimage_shim.py"

if not _ZSHIM.is_file():
    pytest.skip("shim script missing in public snapshot", allow_module_level=True)








def _load_zshim():

    spec = importlib.util.spec_from_file_location("zimage_shim_test", _ZSHIM)
    assert spec is not None and spec.loader is not None  # 收窄 Optional

    mod = importlib.util.module_from_spec(spec)

    sys.modules[spec.name] = mod

    if "PIL" not in sys.modules:  # 本地 venv 无 PIL：stub 仅为 import（i2i 未触）

        _pil: Any = types.ModuleType("PIL")

        _pil.Image = types.ModuleType("PIL.Image")

        sys.modules["PIL"] = _pil

        sys.modules["PIL.Image"] = _pil.Image

    spec.loader.exec_module(mod)

    return mod





# ---- P1 H3Warehouse ------------------------------------------------------





def test_h3w_download_has_own_generous_timeout():

    """算力仓产物下载：独立 client 300s（视频几十 MB，共用 30s 轮询超时必炸）。"""

    from backend.app.providers import h3_warehouse



    src = inspect.getsource(h3_warehouse)

    assert "H3W_DOWNLOAD_TIMEOUT_S" in src





def test_h3w_polling_checks_cancellation():

    """轮询循环每轮查协作取消——shield 孤儿不再白轮 15 分钟。"""

    from backend.app.providers import h3_warehouse



    src = inspect.getsource(h3_warehouse.H3WarehouseProvider.run)

    assert "_check_cancelled" in src





def test_worker_wires_cancel_check():

    """worker 注入 cancel_check（DB 查询闭包）→ provider 轮询可感知取消。"""

    import backend.worker as w



    src = inspect.getsource(w.run_generation_task)

    assert "cancel_check" in src





def test_provider_base_default_cancel_check_false():

    # ProviderBase 是 ABC：用具体子类验证默认行为（未注入 cancel_check → False）

    from backend.app.providers.h3_warehouse import H3WarehouseProvider



    p = H3WarehouseProvider.__new__(H3WarehouseProvider)

    assert p._check_cancelled() is False





# ---- P2 mark_started 终态写保护 ------------------------------------------





def test_mark_started_does_not_revive_terminal_task(db_session):

    """477 实证：arq 重投执行的 mark_started 不得覆盖 failed/completed 终态。"""

    t = Task(user_id=1, type="image", prompt="p", model="m", status="failed")

    t.error_msg = "已失败"

    db_session.add(t)

    db_session.commit()



    t.mark_started()

    db_session.commit()

    db_session.expire_all()

    t2 = db_session.get(Task, t.id)

    assert t2.status == "failed"  # 不复活





def test_mark_started_normal_path_still_works(db_session):

    t = Task(user_id=1, type="image", prompt="p", model="m", status="queued")

    db_session.add(t)

    db_session.commit()



    t.mark_started()

    assert t.status == "generating"





# ---- P3 Agent 入口视频串行校验 -------------------------------------------





def test_agent_video_serial_reject(client, worker_db, db_session):

    """已有进行中视频时，agent 再提视频 → 422 + 教育性文案（0 秒反馈取代

    58 分钟排队超时失败）。"""

    from backend.tests.test_agent_api import _register_with_key



    h, _ = _register_with_key(client, db_session)

    pid = client.post("/api/agent/v1/projects", json={"title": "串行"}, headers=h).json()["id"]



    t = Task(user_id=1, type="video", prompt="p", model="minimax-h3-video", status="generating")

    db_session.add(t)

    db_session.commit()



    r = client.post(

        "/api/agent/v1/generate",

        headers=h,

        json={"project_id": pid, "type": "video", "message": "第二个视频"},

    )

    assert r.status_code == 422

    assert "串行" in str(r.json()) or "完成" in str(r.json())





def test_agent_video_first_submission_allowed(client, worker_db, db_session):

    from backend.tests.test_agent_api import _register_with_key



    h, uid = _register_with_key(client, db_session)

    pid = client.post("/api/agent/v1/projects", json={"title": "首发"}, headers=h).json()["id"]

    # 清场：确保无进行中视频

    db_session.query(Task).filter(Task.type == "video").delete()

    db_session.commit()



    r = client.post(

        "/api/agent/v1/generate",

        headers=h,

        json={"project_id": pid, "type": "video", "message": "首个"},

    )

    assert r.status_code == 202





# ---- P4 超时集中声明 ------------------------------------------------------





def test_timeout_constants_ordering():

    """单点约束：GATE_TTL > ARQ_JOB > GATE_QUEUE > provider 各超时。"""

    from backend.app.core import timeouts



    assert timeouts.GATE_TTL_S > timeouts.ARQ_JOB_TIMEOUT_S

    assert timeouts.ARQ_JOB_TIMEOUT_S > timeouts.GATE_QUEUE_TIMEOUT_S

    assert timeouts.GATE_QUEUE_TIMEOUT_S > timeouts.H3_LOCAL_TOTAL_S

    assert timeouts.GATE_QUEUE_TIMEOUT_S > timeouts.H3W_DEADLINE_S

    assert max(

        timeouts.H3_LOCAL_TOTAL_S, timeouts.H3W_DEADLINE_S

    ) < timeouts.CLEANUP_STALE_MIN * 60





def test_worker_uses_central_timeouts():

    import backend.worker as w



    assert w.WorkerSettings.job_timeout == 3600

    from backend.app.core import timeouts



    assert w.WorkerSettings.job_timeout == timeouts.ARQ_JOB_TIMEOUT_S





# ---- P5 撤销协议推广 ------------------------------------------------------





def test_openai_compat_sends_task_header_and_tries_cancel(monkeypatch):

    """OpenAICompat 统一：请求带 X-Creagen-Task-Id（shim 可忽略）；cancel_remote_job

    按 shim 协议尝试 DELETE /internal/jobs（404=不支持 → False）。"""

    from backend.app.providers.openai_compat import OpenAICompatProvider



    p = OpenAICompatProvider.__new__(OpenAICompatProvider)

    p.base_url = "http://x:8310"

    p.key_plain = "k"



    sent: dict = {}



    class FakeResp404:

        status_code = 404



        def json(self):

            return {"detail": "Not Found"}



    class FakeClient:

        def __init__(self, timeout=None):

            pass



        async def __aenter__(self):

            return self



        async def __aexit__(self, *a):

            return None



        async def delete(self, url, params=None):

            sent["url"] = url

            return FakeResp404()



    monkeypatch.setattr(

        "backend.app.providers.openai_compat.httpx.AsyncClient", FakeClient

    )

    assert asyncio.run(p.cancel_remote_job(5)) is False  # 不支持的渠道优雅降级

    assert "/internal/jobs/5" in sent["url"]





def test_zimage_shim_has_cancel_endpoint():

    """zimage shim 支持 /internal/jobs/{task_id} 撤销协议（与 h3 shim 一致）。"""

    from fastapi.testclient import TestClient



    mod = _load_zshim()

    mod._JOBS.clear()

    mod._CANCELLED.clear()

    client = TestClient(mod.app)

    r = client.delete("/internal/jobs/404")

    assert r.status_code == 200

    assert r.json()["known"] is False
