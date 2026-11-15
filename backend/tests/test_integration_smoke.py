"""真 Redis 集成冒烟：事件发布/订阅闭环（CI 带 service 容器时执行）。"""

import pytest

pytestmark = [pytest.mark.integration]


def test_redis_pubsub_roundtrip():
    import threading
    import time

    from backend.app.services.task_queue import publish_task_event, subscribe_task_events

    received = []

    def listen():
        pubsub = subscribe_task_events(999999)
        for msg in pubsub.listen():
            if msg.get("type") == "message":
                received.append(msg.get("data"))
                pubsub.close()
                return

    t = threading.Thread(target=listen, daemon=True)
    t.start()
    time.sleep(0.5)  # 等订阅建立
    publish_task_event(999999, "completed", {"output_url": "/x.png"})
    t.join(timeout=5)
    assert received and "completed" in received[0]
