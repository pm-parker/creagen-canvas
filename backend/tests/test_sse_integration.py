"""SSE 真实 Redis 集成：pubsub 空闲不断流（BUG：socket_timeout=2 复用连接致断流）。"""

import threading
import time

import pytest

from backend.tests.test_task_flow import _auth_headers, _submit


@pytest.mark.integration
def test_sse_survives_idle_gap_and_delivers_events(client):
    """订阅后空闲 3.5s（> socket_timeout=2s）仍存活，后续事件正常送达。

    修复前：pubsub 复用 get_sync_redis()（socket_timeout=2），listen() 空闲 2s
    即抛 TimeoutError → SSE 流中断 → 前端永远收不到 completed 帧。
    """
    from backend.app.services.task_queue import publish_task_event

    headers = _auth_headers(client)
    r = _submit(client, headers)
    task_id = r.json()["task"]["id"]

    received: list[str] = []
    done = threading.Event()

    def consume() -> None:
        try:
            with client.stream(
                "GET", f"/api/tasks/stream?task_id={task_id}", headers=headers
            ) as resp:
                for line in resp.iter_lines():
                    received.append(line)
                    if "completed" in line:
                        break
        finally:
            done.set()

    t = threading.Thread(target=consume, daemon=True)
    t.start()
    time.sleep(1.0)  # 等订阅建立

    publish_task_event(task_id, "phase", {"phase": "starting"})
    time.sleep(3.5)  # 空闲 > socket_timeout：修复前此处断流

    publish_task_event(task_id, "completed", {"output_url": "/outputs/x.png"})
    assert done.wait(timeout=8), f"未收到 completed 帧，已收到: {received}"
    assert any("starting" in x for x in received), received
    assert any("completed" in x for x in received), received
