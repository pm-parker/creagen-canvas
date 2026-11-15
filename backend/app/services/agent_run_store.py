"""AgentRun 持久化：创建 / CAS 状态机 / pending / steps / messages。

CAS 语义（spec §1）：UPDATE ... SET status='planning' WHERE status IN ('queued','waiting')
判 rowcount——双 kick（终态钩子 + sweeper）幂等的基石。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from backend.app.models import AgentRun, AgentStep
from backend.app.models.agent_run import AGENT_RUN_ACTIVE_STATUSES, AGENT_RUN_TERMINAL_STATUSES

SYSTEM_PROMPT_MARK = "system"


def create_run(
    db: Session,
    *,
    user_id: int,
    project_id: int,
    initial_message: str,
    mentions: list[dict[str, Any]] | None = None,
    pinned_model: dict[str, Any] | None = None,
    brain_channel_id: int | None = None,
    max_steps: int = 12,
    system_prompt: str = "",
    engine: str = "v1",
) -> AgentRun:
    """创建 run；首条 user 消息 = 系统提示之外的第一条（脑服务负责注入 system）。"""
    run = AgentRun(
        user_id=user_id,
        project_id=project_id,
        status="queued",
        messages_json=json.dumps(
            [{"role": "user", "content": initial_message}], ensure_ascii=False
        ),
        pending_json="[]",
        max_steps=max_steps,
        pinned_model_json=json.dumps(pinned_model, ensure_ascii=False) if pinned_model else None,
        brain_channel_id=brain_channel_id,
        engine=engine,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def get_run(db: Session, run_id: int) -> AgentRun | None:
    return db.query(AgentRun).filter(AgentRun.id == run_id).first()


def get_active_run(db: Session, user_id: int) -> AgentRun | None:
    """单用户单活跃 run（spec §6 护栏）。"""
    return (
        db.query(AgentRun)
        .filter(AgentRun.user_id == user_id, AgentRun.status.in_(AGENT_RUN_ACTIVE_STATUSES))
        .order_by(AgentRun.id.desc())
        .first()
    )


def list_runs(db: Session, user_id: int, limit: int = 50) -> list[AgentRun]:
    return (
        db.query(AgentRun)
        .filter(AgentRun.user_id == user_id)
        .order_by(AgentRun.id.desc())
        .limit(limit)
        .all()
    )


def cas_begin_iteration(db: Session, run_id: int) -> bool:
    """queued/waiting → planning 的乐观锁抢占；返回 False = 别的迭代已在跑或已终态。"""
    rows = (
        db.query(AgentRun)
        .filter(AgentRun.id == run_id, AgentRun.status.in_(("queued", "waiting")))
        .update({"status": "planning"}, synchronize_session=False)
    )
    db.commit()
    db.expire_all()  # query.update 不刷新身份映射，失效保证后续 get_run 读新值
    return rows == 1


def set_status(db: Session, run_id: int, status: str) -> None:
    run = get_run(db, run_id)
    if run is None:
        return
    if status not in AGENT_RUN_ACTIVE_STATUSES + AGENT_RUN_TERMINAL_STATUSES:
        raise ValueError(f"非法 agent run 状态: {status}")
    run.status = status
    db.commit()


def finish_run(db: Session, run_id: int, status: str, error: str | None = None) -> None:
    """终态写入（completed/failed/stopped/incomplete）+ 通知钩子（M0 止血）。"""
    run = get_run(db, run_id)
    if run is None:
        return
    if status not in AGENT_RUN_TERMINAL_STATUSES:
        raise ValueError(f"非法终态: {status}")
    run.status = status
    run.error = error
    db.commit()

    from backend.app.services.agent_notify import notify_terminal

    notify_terminal(db, run_id, status, error)  # 永不抛（内部全包）


def get_pending(db: Session, run_id: int, kind: str | None = None) -> list[dict[str, Any]]:
    run = get_run(db, run_id)
    if run is None:
        return []
    pending = run.pending()
    return [p for p in pending if kind is None or p.get("type") == kind]


def set_pending(db: Session, run_id: int, pending: list[dict[str, Any]]) -> None:
    run = get_run(db, run_id)
    if run is None:
        return
    run.pending_json = json.dumps(pending, ensure_ascii=False)
    db.commit()


def set_brain_channel(db: Session, run_id: int, channel_id: int) -> None:
    """首轮锁定脑渠道 id（抽屉展示"用的哪个脑模型"）。"""
    run = get_run(db, run_id)
    if run is None or run.brain_channel_id is not None:
        return
    run.brain_channel_id = channel_id
    db.commit()


def get_reference_whitelist(db: Session, run_id: int) -> list[str]:
    run = get_run(db, run_id)
    return run.reference_whitelist() if run else []


def extend_reference_whitelist(db: Session, run_id: int, urls: list[str]) -> None:
    run = get_run(db, run_id)
    if run is None:
        return
    wl = run.reference_whitelist()
    for u in urls:
        if u and u not in wl:
            wl.append(u)
    run.reference_whitelist_json = json.dumps(wl, ensure_ascii=False)
    db.commit()


def get_messages(db: Session, run_id: int) -> list[dict[str, Any]]:
    run = get_run(db, run_id)
    return run.messages() if run else []


def append_message(db: Session, run_id: int, message: dict[str, Any]) -> None:
    run = get_run(db, run_id)
    if run is None:
        return
    msgs = run.messages()
    msgs.append(message)
    run.messages_json = json.dumps(msgs, ensure_ascii=False)
    db.commit()


def append_step(
    db: Session,
    run_id: int,
    *,
    idx: int,
    thought: str,
    action: dict[str, Any],
    result: Any,
) -> AgentStep:
    step = AgentStep(
        run_id=run_id,
        idx=idx,
        thought=thought or "",
        action_json=json.dumps(action, ensure_ascii=False),
        result_json=json.dumps(result, ensure_ascii=False),
    )
    db.add(step)
    run = get_run(db, run_id)
    if run is not None:
        run.step_count = idx + 1
    db.commit()
    db.refresh(step)
    return step


def add_usage(db: Session, run_id: int, tokens: int, cost: float) -> None:
    run = get_run(db, run_id)
    if run is None:
        return
    run.tokens_used = (run.tokens_used or 0) + int(tokens)
    run.est_cost = (run.est_cost or 0.0) + float(cost)
    db.commit()


def bump_action_steps(db: Session, run_id: int, delta: int = 1) -> None:
    """L2：只有产出新动作的脑轮才计步。"""
    run = get_run(db, run_id)
    if run is None:
        return
    run.action_steps = (run.action_steps or 0) + delta
    db.commit()


def set_compaction(db: Session, run_id: int, rec: dict) -> None:
    """M5 压缩记录落库（原文 messages_json 不动）。"""
    import json as _json

    run = get_run(db, run_id)
    if run is None:
        return
    run.compaction_json = _json.dumps(rec, ensure_ascii=False)
    db.commit()


def set_plan(db: Session, run_id: int, plan: dict) -> None:
    import json as _json

    run = get_run(db, run_id)
    if run is None:
        return
    run.plan_json = _json.dumps(plan, ensure_ascii=False)
    db.commit()


def update_max_steps(db: Session, run_id: int, value: int) -> None:
    run = get_run(db, run_id)
    if run is None:
        return
    run.max_steps = int(value)
    db.commit()


def bump_done_rejections(db: Session, run_id: int, delta: int = 1) -> None:
    run = get_run(db, run_id)
    if run is None:
        return
    run.done_rejections = (run.done_rejections or 0) + delta
    db.commit()


def norm_evidence(v: Any) -> list[str]:
    """旧形态（裸 task id int）归一为 "task:<id>"；新形态 "task:N"/"url:P" 原样。"""
    if isinstance(v, list):
        out = []
        for x in v:
            if isinstance(x, int):
                out.append(f"task:{x}")
            elif isinstance(x, str):
                out.append(x)
        return out
    return []


def map_deliverable_evidence(db: Session, run_id: int, label: str, evidence: str) -> None:
    """中途投影：label → 证据追加（"task:<id>" 或 "url:<path>"，去重）。

    run#11 回放实证：compose 等同步工具无 task_id，仅 url 证据。
    """
    run = get_run(db, run_id)
    if run is None:
        return
    m = {k: norm_evidence(v) for k, v in run.deliverables_map().items()}
    ev = m.setdefault(label, [])
    if evidence not in ev:
        ev.append(evidence)
    import json as _json

    run.deliverables_proj_json = _json.dumps(m, ensure_ascii=False)
    db.commit()


def map_deliverable_task(db: Session, run_id: int, label: str, task_id: int) -> None:
    """中途投影：label → task 证据。"""
    map_deliverable_evidence(db, run_id, label, f"task:{task_id}")


def get_ext(db: Session, run_id: int, key: str) -> int:
    import json as _json

    run = get_run(db, run_id)
    if run is None:
        return 0
    try:
        ext = _json.loads(run.extensions_json or "{}")
    except (ValueError, TypeError):
        return 0
    return int(ext.get(key) or 0)


def bump_ext(db: Session, run_id: int, key: str, delta: int = 1) -> int:
    """extensions_json 计数器（如 plan_missing_count）——不加列的轻量扩展位。"""
    import json as _json

    run = get_run(db, run_id)
    if run is None:
        return 0
    try:
        ext = _json.loads(run.extensions_json or "{}")
    except (ValueError, TypeError):
        ext = {}
    ext[key] = int(ext.get(key) or 0) + delta
    run.extensions_json = _json.dumps(ext, ensure_ascii=False)
    db.commit()
    return int(ext[key])


def bump_brain_calls(db: Session, run_id: int, delta: int = 1) -> None:
    """脑调用计数（token 计费口径，观察轮也计）。"""
    run = get_run(db, run_id)
    if run is None:
        return
    run.brain_calls = (run.brain_calls or 0) + delta
    db.commit()


def list_steps(db: Session, run_id: int) -> list[AgentStep]:
    return (
        db.query(AgentStep)
        .filter(AgentStep.run_id == run_id)
        .order_by(AgentStep.idx.asc())
        .all()
    )
