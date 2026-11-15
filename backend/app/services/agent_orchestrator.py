"""Agent 迭代编排器：事件驱动状态机的核心。

spec §2：每轮迭代 = 短命 arq job（CAS 抢锁 → 重读 pending 自愈 → 观察 → 脑 →
工具 → 落库 → 终态判定）；生成任务终态钩子/sweeper 唤醒；停止三重竞态缓解。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from backend.app.models import AgentRun, CanvasProject, Task, User
from backend.app.models.run_event import RunEvent
from backend.app.services import agent_brain
from backend.app.services import agent_run_store as store
from backend.app.services.agent_tools import ToolContext, execute_action, tool_manifest
from backend.app.services.task_queue import (
    enqueue_agent_step,
    publish_agent_run_event,
)

logger = logging.getLogger("canvas.agent.orchestrator")

_WAIT_STALE_MIN = 10  # waiting 且全终态超此分钟数 → sweeper re-kick
_PLAN_STALE_MIN = 15  # planning 卡死（job 崩溃）超此分钟数 → 复位并 re-kick


class AgentRunError(ValueError):
    """提交/停止的业务错误（code + message，路由层转 ApiError）。"""

    def __init__(self, code: str, message: str, hint: str = ""):
        self.code = code
        self.message = message
        self.hint = hint
        super().__init__(message)


# ---------- 提交 ----------

def _resolve_mentions(db: Session, user: User, mentions: list[dict[str, Any]]) -> dict[str, Any]:
    """mention → 上下文块 + reference 白名单 + 模型 pin。

    素材/角色/提示词展开为结构化文本块（脑可读）；素材 output_url 进白名单。
    无效引用降级为"已失效"标注，不阻塞提交。
    """
    blocks: list[str] = []
    whitelist: list[str] = []
    pinned: dict[str, Any] | None = None

    for m in mentions or []:
        mtype = m.get("type")
        if mtype == "asset":
            t = db.query(Task).filter(Task.id == m.get("task_id"), Task.user_id == user.id).first()
            if t and t.status == "completed" and t.output_url:
                # 标签与前端 token 同构（@素材:<prompt前12字>），脑可按文本位置精确映射
                label = (t.prompt or "")[:12] or str(t.id)
                blocks.append(f"[@素材:{label}] {t.output_url}")
                whitelist.append(t.output_url)
            else:
                blocks.append(f"[@素材] (id={m.get('task_id')}) 已失效或未完成，不可引用")
        elif mtype == "character":
            from backend.app.models import CharacterAsset

            c = (
                db.query(CharacterAsset)
                .filter(CharacterAsset.id == m.get("id"), CharacterAsset.user_id == user.id)
                .first()
            )
            if c:
                ref = f"（定妆照 {c.ref_image_url}）" if c.ref_image_url else ""
                blocks.append(f"[@角色:{c.name}] {c.description}{ref}")
            else:
                blocks.append(f"[@角色] (id={m.get('id')}) 不存在，不可引用")
        elif mtype == "prompt":
            from sqlalchemy import or_

            from backend.app.models import Prompt

            # 共享提示词（user_id NULL，种子库）与个人库均可引用——与 /api/prompts
            # 列表口径一致（面板能看到 → 必须能 @）
            pr = (
                db.query(Prompt)
                .filter(
                    Prompt.id == m.get("id"),
                    or_(Prompt.user_id == user.id, Prompt.user_id.is_(None)),
                )
                .first()
            )
            if pr:
                label = (pr.title or pr.content or "")[:12] or str(pr.id)
                blocks.append(f"[@提示词:{label}] {pr.content}")
            else:
                blocks.append(f"[@提示词] (id={m.get('id')}) 不存在")
        elif mtype == "model":
            pinned = {"channel_id": m.get("channel_id"), "model": m.get("model")}
    return {"blocks": blocks, "whitelist": whitelist, "pinned": pinned}


async def submit_run(
    db: Session,
    *,
    user: User,
    message: str,
    mentions: list[dict[str, Any]],
    max_steps: int = 12,
    engine: str = "v1",
) -> AgentRun:
    if not message or not message.strip():
        raise AgentRunError("MESSAGE_EMPTY", "创作请求不能为空")
    active = store.get_active_run(db, user.id)
    if active is not None:
        raise AgentRunError(
            "ACTIVE_RUN_EXISTS",
            "已有进行中的 Agent 创作",
            f"请先完成或停止当前 run（id={active.id}）",
        )

    resolved = _resolve_mentions(db, user, mentions)
    initial = message.strip()
    if resolved["blocks"]:
        initial += "\n\n用户引用：\n" + "\n".join(resolved["blocks"])

    proj = CanvasProject(user_id=user.id, title=f"Agent 创作·{message.strip()[:12]}")
    db.add(proj)
    db.commit()

    run = store.create_run(
        db,
        user_id=user.id,
        project_id=proj.id,
        initial_message=initial,
        mentions=mentions,
        pinned_model=resolved["pinned"],
        max_steps=max_steps,
        engine=engine,
    )
    if resolved["whitelist"]:
        store.extend_reference_whitelist(db, run.id, resolved["whitelist"])

    if getattr(run, "engine", "v1") == "v2":
        from backend.app.services import agent_events

        agent_events.emit(
            db, run.id, "run_created", {"message": initial, "mentions": mentions or []}
        )
    publish_agent_run_event(run.id, "run_created", run.to_dict())
    await enqueue_agent_step(run.id)
    return run


# ---------- 迭代 ----------

def _observation_for_task(db: Session, run: AgentRun, task_id: int) -> dict[str, Any]:
    t = db.query(Task).filter(Task.id == task_id).first()
    if t is None:
        return {"task_id": task_id, "status": "missing"}
    obs: dict[str, Any] = {
        "task_id": t.id,
        "type": t.type,
        "status": t.status,
    }
    if t.output_url:
        obs["output_url"] = t.output_url
    if t.error_msg:
        obs["error"] = t.error_msg[:300]
    # node 映射：canvas_target.node_client_id → 节点 id（脑连线要用）
    from backend.app.models import CanvasNode

    target = (t.params_dict() or {}).get("canvas_target") or {}
    cid = target.get("node_client_id")
    if cid:
        node = (
            db.query(CanvasNode)
            .filter(
                CanvasNode.project_id == run.project_id,
                CanvasNode.metadata_json.like(f"%{cid}%"),
            )
            .first()
        )
        if node:
            obs["node_id"] = node.id
    return obs


def _all_pending_terminal(db: Session, pending: list[dict[str, Any]]) -> bool:
    for p in pending:
        if p.get("type") != "task":
            continue
        t = db.query(Task.status).filter(Task.id == p["id"]).first()
        if t and t[0] in ("queued", "generating"):
            return False
    return True


async def kick_run(db: Session, run_id: int) -> dict[str, Any]:
    """一轮迭代：CAS 抢锁后线性执行；任何退出路径都保证状态一致。

    v2（engine="v2"）走事件溯源路径（M0：事件落流 + L2 记账），v1 保持原语义。
    """
    run_probe = store.get_run(db, run_id)
    if run_probe is not None and getattr(run_probe, "engine", "v1") == "v2":
        return await _kick_run_v2(db, run_id)
    if not store.cas_begin_iteration(db, run_id):
        return {"skipped": True}
    run = store.get_run(db, run_id)
    if run is None:
        return {"skipped": True}

    # 自愈：pending 未全终态 → 回 waiting（不信任内存/事件）
    pending = store.get_pending(db, run_id)
    if pending and not _all_pending_terminal(db, pending):
        store.set_status(db, run_id, "waiting")
        return {"status": "waiting", "self_heal": True}

    # 步数触顶（先于脑调用，防烧钱）
    if run.step_count >= (run.max_steps or 12):
        store.finish_run(db, run_id, "stopped", error="MAX_STEPS")
        publish_agent_run_event(run_id, "run_stopped", {"reason": "MAX_STEPS"})
        return {"status": "stopped", "reason": "MAX_STEPS"}

    # 观察：上一轮 pending 的任务终态 → 注入消息 + 产物入白名单
    messages = store.get_messages(db, run_id)
    if pending:
        obs = [_observation_for_task(db, run, p["id"]) for p in pending if p.get("type") == "task"]
        new_urls = [o["output_url"] for o in obs if o.get("output_url")]
        if new_urls:
            store.extend_reference_whitelist(db, run_id, new_urls)
        obs_text = "任务结果：\n" + json.dumps(obs, ensure_ascii=False)
        store.append_message(db, run_id, {"role": "user", "content": obs_text})
        messages = store.get_messages(db, run_id)
        publish_agent_run_event(run_id, "task_terminal", {"observations": obs})

    # 脑调用（协议失败修复重试一次）
    brain_result = None
    for attempt in range(2):
        try:
            brain_result = await agent_brain.call_brain(
                db, user_id=run.user_id, messages=messages, tool_manifest=tool_manifest()
            )
            break
        except agent_brain.BrainProtocolError as e:
            if attempt == 0:
                store.append_message(
                    db,
                    run_id,
                    {
                        "role": "user",
                        "content": (
                            f"你上一次输出不是合法的动作 JSON（错误：{e.message}）。"
                            "请重新输出唯一一个 JSON 动作对象，不要包含其他文字。"
                        ),
                    },
                )
                messages = store.get_messages(db, run_id)
            else:
                store.finish_run(db, run_id, "failed", error="PROTOCOL_ERROR")
                publish_agent_run_event(run_id, "run_failed", {"reason": "PROTOCOL_ERROR"})
                return {"status": "failed", "reason": "PROTOCOL_ERROR"}
        except agent_brain.BrainChannelError as e:
            store.finish_run(db, run_id, "failed", error=e.code)
            publish_agent_run_event(run_id, "run_failed", {"reason": e.code, "message": e.message})
            return {"status": "failed", "reason": e.code}

    if brain_result is None:  # 理论不可达（双保险）
        store.finish_run(db, run_id, "failed", error="BRAIN_NO_RESULT")
        return {"status": "failed", "reason": "BRAIN_NO_RESULT"}

    # 脑渠道记录（首轮锁定；抽屉展示）
    if brain_result.get("channel_id"):
        store.set_brain_channel(db, run_id, int(brain_result["channel_id"]))

    # 记账
    usage = brain_result.get("usage") or {}
    if usage.get("prompt_tokens") or usage.get("completion_tokens"):
        store.add_usage(
            db, run_id,
            int(usage.get("prompt_tokens") or 0) + int(usage.get("completion_tokens") or 0),
            0.0,
        )

    ctx = ToolContext(
        db=db,
        user=db.query(User).filter(User.id == run.user_id).first(),
        run_id=run_id,
        project_id=run.project_id,
        reference_whitelist=store.get_reference_whitelist(db, run_id),
        pinned_model=(json.loads(run.pinned_model_json) if run.pinned_model_json else None),
    )

    if brain_result.get("done"):
        done_text = json.dumps(brain_result, ensure_ascii=False)
        store.append_message(db, run_id, {"role": "assistant", "content": done_text})
        store.append_step(
            db,
            run_id,
            idx=run.step_count,
            thought=brain_result.get("thought") or "",
            action={"done": True, "summary": brain_result.get("summary") or ""},
            result={"done": True},
        )
        store.finish_run(db, run_id, "completed")
        publish_agent_run_event(run_id, "run_completed", {"summary": brain_result.get("summary")})
        return {"status": "completed"}

    # 执行动作
    results: list[dict[str, Any]] = []
    new_pending: list[dict[str, Any]] = []
    for action in brain_result.get("actions") or []:
        # 停止竞态缓解：每个动作前复查状态
        if store.get_run(db, run_id).status != "planning":
            return {"status": "stopped", "reason": "STOPPED_MID_ITERATION"}
        result = await execute_action(ctx, action)
        results.append({"tool": action.get("tool"), **result})
        if result.get("ok") and result.get("task_id"):
            new_pending.append({"type": "task", "id": result["task_id"]})

    store.append_message(
        db, run_id, {"role": "assistant", "content": json.dumps(brain_result, ensure_ascii=False)}
    )
    store.append_message(
        db,
        run_id,
        {"role": "user", "content": "工具执行结果：\n" + json.dumps(results, ensure_ascii=False)},
    )
    store.append_step(
        db,
        run_id,
        idx=run.step_count,
        thought=brain_result.get("thought") or "",
        action={"actions": brain_result.get("actions") or []},
        result=results,
    )
    publish_agent_run_event(
        run_id, "step", {"idx": run.step_count, "results": results}
    )

    # 终态判定
    if new_pending:
        store.set_pending(db, run_id, new_pending)
        store.set_status(db, run_id, "waiting")
        return {"status": "waiting", "pending": new_pending}
    # 无 pending 且未 done（纯同步工具轮）→ 自动 re-kick 下一轮
    store.set_pending(db, run_id, [])
    store.set_status(db, run_id, "waiting")
    await enqueue_agent_step(run_id)
    return {"status": "waiting", "self_kick": True}


# ---------- 终态钩子 / 停止 / sweeper ----------

async def advance_on_task_terminal(db: Session, task: Task) -> None:
    """worker 终态钩子（永不抛出，不影响任务主流程）：task 带 agent_run_id 即 kick。"""
    run_id = (task.params_dict() or {}).get("agent_run_id")
    if not run_id:
        return
    publish_agent_run_event(int(run_id), "task_done", {"task_id": task.id, "status": task.status})
    await enqueue_agent_step(int(run_id))


async def stop_run(db: Session, *, user: User, run_id: int) -> bool:
    run = store.get_run(db, run_id)
    if run is None or run.user_id != user.id:
        raise AgentRunError("RUN_NOT_FOUND", "运行不存在")
    if run.status in ("completed", "failed", "stopped"):
        return True  # 幂等
    store.set_status(db, run_id, "stopped")

    # 取消 pending 任务 + 二次清扫（停止竞态缓解：晚派发的任务也被收走）
    # 注意：不能用 LIKE 前缀匹配（"agent_run_id": 7 会命中 71/77），
    # 宽匹配后必须在 Python 侧精确比对 params_dict
    from backend.app.services.task_store import mark_cancel_requested

    for p in store.get_pending(db, run_id):
        if p.get("type") == "task":
            mark_cancel_requested(db, p["id"])
    candidates = (
        db.query(Task)
        .filter(Task.params.like('%"agent_run_id"%'), Task.status.in_(("queued", "generating")))
        .all()
    )
    for t in candidates:
        if (t.params_dict() or {}).get("agent_run_id") == run_id:
            mark_cancel_requested(db, t.id)

    publish_agent_run_event(run_id, "run_stopped", {"reason": "USER_STOP"})
    return True


def _db_now(db: Session) -> datetime:
    """DB 服务器墙钟——与 updated_at 列写入同基准。

    PG：列由 func.now() 按会话 TimeZone 折算墙钟写入（naive）；驱动把 now()
    返回为 aware UTC——必须取 now()::timestamp（会话墙钟 naive）同基准，
    否则 aware-naive 相减 TypeError（2026-10-09 二层实证）。
    SQLite：CURRENT_TIMESTAMP 字符串（UTC），与其写列行为同源。
    """
    from sqlalchemy import text as _text

    try:
        v = db.execute(_text("SELECT now()::timestamp")).scalar()
        if v is not None:
            return v.replace(tzinfo=None) if getattr(v, "tzinfo", None) else v
    except Exception:  # noqa: BLE001 — SQLite 无 :: 语法，走回落
        pass
    from sqlalchemy import func as _sa_func

    v = db.execute(_sa_func.now()).scalar()
    if isinstance(v, str):
        from datetime import datetime as _dt

        return _dt.fromisoformat(v.replace("Z", "+00:00").split(".")[0].split("+")[0])
    return v.replace(tzinfo=None) if getattr(v, "tzinfo", None) else v


async def resume_run(db: Session, *, user: User, run_id: int) -> AgentRun:
    """续跑（设计 §8）：incomplete/stopped 的 v2 run → 子 run 继承剩余计划+上下文。

    谱系 parent_run_id 可追溯；与 create 共用单活跃护栏（§14 审查修复）。
    """
    run = store.get_run(db, run_id)
    if run is None or run.user_id != user.id:
        raise AgentRunError("RUN_NOT_FOUND", "运行不存在")
    if getattr(run, "engine", "v1") != "v2":
        raise AgentRunError("RESUME_V1_UNSUPPORTED", "仅新版运行支持续跑")
    if run.status not in ("incomplete", "stopped"):
        raise AgentRunError("NOT_RESUMABLE", "仅未完成/已停止的运行可续跑")
    active = store.get_active_run(db, user.id)
    if active is not None and active.id != run.id:
        raise AgentRunError("ACTIVE_RUN_EXISTS", "已有进行中的创作，请先等它结束或停止")

    remaining = _remaining_deliverables(db, run)
    if not remaining:
        # 投影真相：全部已交付（如 done 被拒但产物齐全）→ 父 run 直接正名 completed
        from backend.app.services import agent_events

        agent_events.emit(
            db, run.id, "run_terminal",
            {"kind": "completed", "summary": "续跑核验：交付物已齐全", "source": "resume_check"},
        )
        store.finish_run(db, run.id, "completed")
        raise AgentRunError("NOTHING_TO_RESUME", "交付物已全部完成，无需续跑")
    old_plan = run.plan() or {"deliverables": []}
    kind_by_label = {
        d.get("label"): d.get("kind", "video") for d in old_plan.get("deliverables") or []
    }
    child_plan = {
        "deliverables": [
            {"kind": kind_by_label.get(label, "video"), "label": label} for label in remaining
        ],
        "est_steps": max(1, len(remaining) * 2),
    }
    grant = min(len(remaining) * 2 + 4, 60)

    from backend.app.services import agent_events

    child = store.create_run(
        db,
        user_id=user.id,
        project_id=run.project_id,
        initial_message=f"续跑 #{run.id}：完成剩余交付物（{('、'.join(remaining)) or '无'}）",
        pinned_model=(json.loads(run.pinned_model_json) if run.pinned_model_json else None),
        engine="v2",
        max_steps=grant,
    )
    child.parent_run_id = run.id
    child.plan_json = json.dumps(child_plan, ensure_ascii=False)
    child.messages_json = run.messages_json  # 脑上下文继承
    child.reference_whitelist_json = run.reference_whitelist_json
    # 【run#14 实证】谱系证据转移：祖先 map 继承 + compose-kind 标签自动挂祖先成片 url
    # （修复前生成的 run map 无 compose 条目，产物只在事件里——不转移则脑被迫重做）
    from backend.app.services.agent_run_store import norm_evidence

    ev_map: dict[str, list[str]] = {}
    lineage_rows = db.query(AgentRun).filter(AgentRun.id.in_(_lineage_ids(db, run))).all()
    for lr in lineage_rows:
        for label, evs in lr.deliverables_map().items():
            if label in remaining:
                ev_map.setdefault(label, [])
                ev_map[label].extend(x for x in norm_evidence(evs) if x not in ev_map[label])
    compose_urls: set[str] = set()
    for ev in (
        db.query(RunEvent)
        .filter(RunEvent.run_id.in_(_lineage_ids(db, run)), RunEvent.type == "tool_result")
        .all()
    ):
        p = ev.payload()
        if p.get("tool") == "compose_videos" and p.get("ok") and isinstance(p.get("url"), str):
            compose_urls.add(p["url"])
    for d in child_plan["deliverables"]:
        if d.get("kind") == "compose":
            for u in compose_urls:
                ev_map.setdefault(d["label"], [])
                if f"url:{u}" not in ev_map[d["label"]]:
                    ev_map[d["label"]].append(f"url:{u}")
    child.deliverables_proj_json = json.dumps(ev_map, ensure_ascii=False)
    db.commit()

    agent_events.emit(
        db, child.id, "run_created",
        {"trigger": "resume", "parent_run_id": run.id, "remaining": remaining},
    )
    agent_events.emit(
        db, child.id, "budget_granted", {"to": grant, "reason": "resume"}
    )
    await enqueue_agent_step(child.id)
    return child


def sweep_stale_runs(db: Session) -> list[int]:
    """兜底扫描：queued/waiting 全终态超时 / planning 卡死 → re-kick（CAS 保证安全）。

    queued 必须扫：提交时入队失败（队列抖动）遗留 queued 孤儿，不扫则永无唤醒。
    时钟必须与 updated_at 同源（DB now）——2026-10-09 生产实证：PG 配 Asia/Shanghai
    而 python utcnow 比对差 8 小时→差值恒负→sweeper 永不触发（SQLite 单测 UTC 同源掩盖）。"""
    now = _db_now(db)  # DB 服务器时间：与列写入同源，时区无关
    kicked: list[int] = []
    runs = (
        db.query(AgentRun)
        .filter(AgentRun.status.in_(("queued", "waiting", "planning")))
        .all()
    )
    for run in runs:
        updated = run.updated_at or now
        stale_min = (now - updated).total_seconds() / 60
        if run.status == "planning":
            if stale_min >= _PLAN_STALE_MIN:
                run.status = "waiting"  # 卡死复位（迭代 job 崩溃遗留）
                db.commit()
                kicked.append(run.id)
        elif stale_min >= _WAIT_STALE_MIN and _all_pending_terminal(db, run.pending()):
            kicked.append(run.id)
    return kicked


def _validate_done(db: Session, run: AgentRun, done_matches: Any) -> dict[str, Any]:
    """终局校验（设计 §5.3）：每个交付物至少映射一个【本 run 的】已完成任务。

    归属判定用 params.agent_run_id 的 Python 侧精确比对——LIKE 前缀会 71/7 串号
    （7/71 教训，工作记忆契约纪律）。
    """
    plan = run.plan()
    if plan is None:
        return {"ok": True, "missing": [], "matches": {}}
    labels = [d.get("label") for d in plan.get("deliverables") or [] if d.get("label")]
    evidence: dict[str, list[str]] = {}
    if isinstance(done_matches, list):
        for m in done_matches:
            if not isinstance(m, dict):
                continue
            label = m.get("label")
            ids = m.get("task_ids")
            if isinstance(label, str) and isinstance(ids, list):
                evidence[label] = [f"task:{t}" for t in ids if isinstance(t, int)]
    # 【生产冒烟实证修复 2026-10-10】系统中途投影即真相（task_submitted/同步工具事件 + 归属校验），
    # 脑复述的 done_matches 只是补充证据——强迫脑复述 task_id 只会产生假性拒收
    from backend.app.services.agent_run_store import norm_evidence

    lineage = _lineage_ids(db, run)
    lineage_runs = (
        db.query(AgentRun).filter(AgentRun.id.in_(lineage)).all() if len(lineage) > 1 else [run]
    )
    for lr in lineage_runs:
        for label, ev in lr.deliverables_map().items():
            norm = norm_evidence(ev)
            if norm:
                merged = evidence.setdefault(label, [])
                merged.extend(x for x in norm if x not in merged)
    url_ok = _owned_urls(db, run)
    missing: list[str] = []
    for label in labels:
        satisfied = False
        for ev in evidence.get(label) or []:
            if ev.startswith("task:"):
                try:
                    tid = int(ev[5:])
                except ValueError:
                    continue
                t = db.query(Task).filter(Task.id == tid).first()
                if t is None:
                    continue
                owned = (t.params_dict() or {}).get("agent_run_id") in lineage
                if owned and t.status == "completed" and t.output_url:
                    satisfied = True
                    break
            elif ev.startswith("url:") and ev[4:] in url_ok:
                satisfied = True
                break
        if not satisfied:
            missing.append(label)
    return {"ok": not missing, "missing": missing, "matches": evidence}


def _lineage_ids(db: Session, run: AgentRun) -> list[int]:
    """run 谱系 id 链（含自身；沿 parent_run_id 上溯，防环，深度上限 10）。"""
    ids: list[int] = []
    seen: set[int] = set()
    cur: AgentRun | None = run
    while cur is not None and len(ids) < 10:
        rid = int(cur.id)
        if rid in seen:
            break
        seen.add(rid)
        ids.append(rid)
        if cur.parent_run_id:
            cur = db.query(AgentRun).filter(AgentRun.id == cur.parent_run_id).first()
        else:
            cur = None
    return ids


def _owned_urls(db: Session, run: AgentRun) -> set[str]:
    """谱系 tool_result 事件中成功产出的 url 集合（同步工具交付的归属证据）。

    run#13 实证：resume 子 run 不认父产物 → 脑看着上下文"已合成"却过不了校验。
    """
    from backend.app.models.run_event import RunEvent

    urls: set[str] = set()
    evs = (
        db.query(RunEvent)
        .filter(RunEvent.run_id.in_(_lineage_ids(db, run)), RunEvent.type == "tool_result")
        .all()
    )
    for ev in evs:
        p = ev.payload()
        if p.get("ok") and isinstance(p.get("url"), str):
            urls.add(p["url"])
    return urls


# ---- M5 脑上下文无损压缩（技术设计 §7，pi compaction 同构）----
CTX_SOFT_CHARS = 60_000  # 投影超过即触发压缩（chars 为 token 保守代理，3.3:1）
CTX_KEEP_CHARS = 20_000  # 压缩后保留的近期窗口


def _brain_context(db: Session, run: AgentRun) -> list[dict[str, Any]]:  # noqa: ARG001
    """脑调用投影：压缩摘要 + 保留窗口（messages_json 原文永不改——无损）。"""
    msgs = run.messages()
    rec = run.compaction()
    if not rec or not rec.get("summary"):
        return msgs
    head = {
        "role": "user",
        "content": f"[此前过程摘要（无损压缩，产物 url 均有效）]\n{rec['summary']}",
    }
    kept = msgs[int(rec.get("first_kept") or 0):]
    return [head, *kept]


async def _maybe_compact(db: Session, run: AgentRun) -> bool:
    """超阈值→LLM 摘要→落 compaction 记录+事件。失败放弃（下轮再试）。"""
    msgs = run.messages()
    rec = run.compaction()
    start = int(rec.get("first_kept") or 0) if rec else 0
    proj = _brain_context(db, run)
    if sum(len(m.get("content") or "") for m in proj) < CTX_SOFT_CHARS:
        return False
    # 自尾向头累计保留窗口 → 切点
    acc, cut = 0, len(msgs)
    for i in range(len(msgs) - 1, start - 1, -1):
        acc += len(msgs[i].get("content") or "")
        if acc > CTX_KEEP_CHARS:
            cut = i + 1
            break
        cut = i
    if cut <= start:
        return False  # 可压缩区间为空
    prev_summary = rec.get("summary") if rec else None
    span_text = ""
    if prev_summary:
        span_text += f"[上一版摘要]\n{prev_summary}\n\n[此后新增过程]\n"
    span_text += "\n".join(
        f"{m.get('role')}: {m.get('content')}" for m in msgs[start:cut]
    )
    summary = await agent_brain.summarize_for_compaction(
        db, user_id=run.user_id, text=span_text
    )
    if not summary:
        return False
    from backend.app.services import agent_events

    store.set_compaction(
        db, run.id,
        {"summary": summary, "first_kept": cut, "chars_before": acc},
    )
    agent_events.emit(
        db, run.id, "compaction",
        {"first_kept": cut, "span_msgs": cut - start, "chars_kept": acc},
    )
    return True


def _remaining_deliverables(db: Session, run: AgentRun) -> list[str]:
    """按中途映射计算未交付清单（L3 扩额依据 / resume 继承集）。"""
    plan = run.plan()
    if plan is None:
        return []
    matches = {k: v for k, v in run.deliverables_map().items() if v}
    verdict = _validate_done(db, run, [{"label": k, "task_ids": v} for k, v in matches.items()])
    return verdict["missing"]


# ---------- v2 事件溯源路径（M0：事件落流 + L2 记账；L1/L3 后续分期） ----------


async def _kick_run_v2(db: Session, run_id: int) -> dict[str, Any]:
    """v2 迭代：与 v1 同语义骨架，差异仅三处——
    ① 全程 emit 事件（日志即真相）
    ② L2 记账：观察轮不 bump action_steps，脑轮 bump brain_calls
    ③ MAX_STEPS 护栏读 action_steps（预算是护栏不是控制流，M0 保持硬停+通知）
    """
    from backend.app.services import agent_events

    if not store.cas_begin_iteration(db, run_id):
        return {"skipped": True}
    run = store.get_run(db, run_id)
    if run is None:
        return {"skipped": True}

    pending = store.get_pending(db, run_id)
    if pending and not _all_pending_terminal(db, pending):
        store.set_status(db, run_id, "waiting")
        return {"status": "waiting", "self_heal": True}

    # ---- L3 护栏（设计 §4.3）：预算是护栏不是控制流——触顶先扩额，扩不动才 incomplete ----
    if run.action_steps >= (run.max_steps or 12):
        from backend.app.services.agent_tools import HARD_STEP_CAP

        remaining = _remaining_deliverables(db, run)
        ext_count = store.get_ext(db, run_id, "auto_extend_count")
        if run.max_steps >= HARD_STEP_CAP or ext_count >= 3:
            reason = "HARD_CAP" if run.max_steps >= HARD_STEP_CAP else "EXTENSION_LIMIT"
            agent_events.emit(
                db,
                run_id,
                "run_terminal",
                {"kind": "incomplete", "reason": reason, "remaining": remaining},
            )
            store.finish_run(db, run_id, "incomplete", error=reason)
            publish_agent_run_event(run_id, "run_stopped", {"reason": reason})
            return {"status": "incomplete", "reason": reason, "remaining": remaining}
        grant = max(8, len(remaining) * 2)
        new_max = min((run.max_steps or 12) + grant, HARD_STEP_CAP)
        store.bump_ext(db, run_id, "auto_extend_count")
        store.update_max_steps(db, run_id, new_max)
        agent_events.emit(
            db, run_id, "budget_granted",
            {"from": run.max_steps, "to": new_max, "reason": "auto"},
        )
        from backend.app.services.agent_notify import notify_budget

        notify_budget(db, run_id, new_max)
        run = store.get_run(db, run_id)  # 刷新后继续本轮迭代（不处决）
        if run is None:
            return {"skipped": True}

    messages = store.get_messages(db, run_id)
    if pending:
        obs = [_observation_for_task(db, run, p["id"]) for p in pending if p.get("type") == "task"]
        new_urls = [o["output_url"] for o in obs if o.get("output_url")]
        if new_urls:
            store.extend_reference_whitelist(db, run_id, new_urls)
        obs_text = "任务结果：\n" + json.dumps(obs, ensure_ascii=False)
        store.append_message(db, run_id, {"role": "user", "content": obs_text})
        messages = store.get_messages(db, run_id)
        agent_events.emit(db, run_id, "task_observed", {"observations": obs})
        publish_agent_run_event(run_id, "task_terminal", {"observations": obs})

    # M5：超阈值先压缩（失败降级继续未压缩）；脑调用只看投影
    await _maybe_compact(db, run)
    run = store.get_run(db, run_id) or run
    messages = _brain_context(db, run)

    store.bump_brain_calls(db, run_id)
    brain_result = None
    for attempt in range(2):
        try:
            brain_result = await agent_brain.call_brain(
                db, user_id=run.user_id, messages=messages, tool_manifest=tool_manifest()
            )
            break
        except agent_brain.BrainProtocolError as e:
            if attempt == 0:
                store.append_message(
                    db,
                    run_id,
                    {
                        "role": "user",
                        "content": (
                            f"你上一次输出不是合法的动作 JSON（错误：{e.message}）。"
                            "请重新输出唯一一个 JSON 动作对象，不要包含其他文字。"
                        ),
                    },
                )
                messages = store.get_messages(db, run_id)
            else:
                store.finish_run(db, run_id, "failed", error="PROTOCOL_ERROR")
                publish_agent_run_event(run_id, "run_failed", {"reason": "PROTOCOL_ERROR"})
                return {"status": "failed", "reason": "PROTOCOL_ERROR"}
        except agent_brain.BrainChannelError as e:
            if e.code == "BRAIN_CONTEXT_OVERFLOW":
                # M5：确定性错误——退避无意义；终态可续（resume 继承上下文）
                agent_events.emit(
                    db, run_id, "run_terminal",
                    {"kind": "failed", "reason": "BRAIN_CONTEXT_OVERFLOW"},
                )
                store.finish_run(db, run_id, "failed", error="BRAIN_CONTEXT_OVERFLOW")
                publish_agent_run_event(run_id, "run_failed", {"reason": e.code})
                return {"status": "failed", "reason": e.code}
            # 瞬时类（超时/不可达）：退避重试 ≤3 次不处决（OpenClaw restart-recovery 同构，
            # 2026-10-10 run#11 生产实证：本地 27B 首轮可超 120s）
            if e.code in ("BRAIN_TIMEOUT", "BRAIN_UNREACHABLE"):
                retries = store.get_ext(db, run_id, "brain_transient")
                if retries < 3:
                    store.bump_ext(db, run_id, "brain_transient")
                    store.append_message(
                        db,
                        run_id,
                        {
                            "role": "user",
                            "content": (
                                f"（系统：脑渠道暂时不可用[{e.code}]，"
                                f"第 {retries + 1} 次自动重试，60 秒后继续）"
                            ),
                        },
                    )
                    store.set_pending(db, run_id, [])
                    store.set_status(db, run_id, "waiting")
                    await enqueue_agent_step(run_id, delay=60)
                    return {"status": "waiting", "brain_retry": retries + 1}
            store.finish_run(db, run_id, "failed", error=e.code)
            publish_agent_run_event(run_id, "run_failed", {"reason": e.code, "message": e.message})
            return {"status": "failed", "reason": e.code}

    if brain_result is None:
        store.finish_run(db, run_id, "failed", error="BRAIN_NO_RESULT")
        return {"status": "failed", "reason": "BRAIN_NO_RESULT"}

    if brain_result.get("channel_id"):
        store.set_brain_channel(db, run_id, int(brain_result["channel_id"]))

    usage = brain_result.get("usage") or {}
    if usage.get("prompt_tokens") or usage.get("completion_tokens"):
        store.add_usage(
            db, run_id,
            int(usage.get("prompt_tokens") or 0) + int(usage.get("completion_tokens") or 0),
            0.0,
        )

    ctx = ToolContext(
        db=db,
        user=db.query(User).filter(User.id == run.user_id).first(),
        run_id=run_id,
        project_id=run.project_id,
        reference_whitelist=store.get_reference_whitelist(db, run_id),
        pinned_model=(json.loads(run.pinned_model_json) if run.pinned_model_json else None),
    )

    # ---- L1 计划强制（设计 §5.2）：首轮无 plan 且动作里没有 submit_plan → 注入要求 ----
    actions = brain_result.get("actions") or []
    if not brain_result.get("done") and run.plan() is None and not any(
        a.get("tool") == "submit_plan" for a in actions if isinstance(a, dict)
    ):
        count = store.bump_ext(db, run_id, "plan_missing_count")
        if count >= 2:
            agent_events.emit(
                db, run_id, "run_terminal", {"kind": "failed", "reason": "PLAN_MISSING"}
            )
            store.finish_run(db, run_id, "failed", error="PLAN_MISSING")
            publish_agent_run_event(run_id, "run_failed", {"reason": "PLAN_MISSING"})
            return {"status": "failed", "reason": "PLAN_MISSING"}
        store.append_message(
            db,
            run_id,
            {
                "role": "user",
                "content": (
                    "你的第一个动作必须是 submit_plan：给出交付物清单（kind+label）与 est_steps，"
                    "系统按计划协商步数预算。请现在提交计划。"
                ),
            },
        )
        store.set_pending(db, run_id, [])
        store.set_status(db, run_id, "waiting")
        await enqueue_agent_step(run_id)
        return {"status": "waiting", "plan_required": True}

    if brain_result.get("done"):
        verdict = _validate_done(db, run, brain_result.get("done_matches"))
        if not verdict["ok"]:
            # 【§14 审查修复】拒收 = 有界重试，绝不留僵尸 waiting；
            # done_rejections 计的是先前拒收数，>=2 时本次为第 3 次 → incomplete
            if run.done_rejections >= 2:
                agent_events.emit(
                    db, run_id, "run_terminal",
                    {"kind": "incomplete", "remaining": verdict["missing"],
                     "reason": "DONE_INCOMPLETE"},
                )
                store.finish_run(db, run_id, "incomplete", error="DONE_INCOMPLETE")
                publish_agent_run_event(run_id, "run_stopped", {"reason": "DONE_INCOMPLETE"})
                return {"status": "incomplete", "missing": verdict["missing"]}
            store.bump_done_rejections(db, run_id)
            store.append_message(
                db,
                run_id,
                {"role": "assistant", "content": json.dumps(brain_result, ensure_ascii=False)},
            )
            store.append_message(
                db,
                run_id,
                {
                    "role": "user",
                    "content": (
                        "done 被拒：以下交付物缺少本 run 已完成任务的产物映射 —— "
                        + "、".join(verdict["missing"])
                        + "。请补齐（done_matches 里给每个 label 至少一个本 run task_id），"
                          "或用 plan_append 调整计划后再交付。"
                    ),
                },
            )
            agent_events.emit(
                db, run_id, "assistant_turn",
                {"thought": "done 被拒", "done_rejected": True, "missing": verdict["missing"]},
            )
            store.set_pending(db, run_id, [])
            store.set_status(db, run_id, "waiting")
            await enqueue_agent_step(run_id)
            return {"status": "waiting", "done_rejected": True}
        done_text = json.dumps(brain_result, ensure_ascii=False)
        store.append_message(db, run_id, {"role": "assistant", "content": done_text})
        store.append_step(
            db,
            run_id,
            idx=run.action_steps,
            thought=brain_result.get("thought") or "",
            action={"done": True, "summary": brain_result.get("summary") or ""},
            result={"done": True},
        )
        agent_events.emit(
            db,
            run_id,
            "assistant_turn",
            {
                "thought": brain_result.get("thought") or "",
                "done": True,
                "usage": {
                    "tokens": (usage.get("prompt_tokens") or 0)
                    + (usage.get("completion_tokens") or 0)
                },
            },
        )
        agent_events.emit(
            db, run_id, "run_terminal",
            {"kind": "completed", "summary": brain_result.get("summary") or "",
             "done_matches": verdict["matches"]},
        )
        store.finish_run(db, run_id, "completed")
        publish_agent_run_event(run_id, "run_completed", {"summary": brain_result.get("summary")})
        return {"status": "completed"}

    results: list[dict[str, Any]] = []
    new_pending: list[dict[str, Any]] = []
    for action in brain_result.get("actions") or []:
        if store.get_run(db, run_id).status != "planning":
            return {"status": "stopped", "reason": "STOPPED_MID_ITERATION"}
        result = await execute_action(ctx, action)
        results.append({"tool": action.get("tool"), **result})
        payload = {
            k: v for k, v in result.items() if k in ("task_id", "node_id", "url", "error")
        }
        agent_events.emit(
            db,
            run_id,
            "tool_result",
            {"tool": action.get("tool"), "ok": bool(result.get("ok")), **payload},
        )
        if result.get("ok") and result.get("task_id"):
            new_pending.append({"type": "task", "id": result["task_id"]})

    store.append_message(
        db, run_id, {"role": "assistant", "content": json.dumps(brain_result, ensure_ascii=False)}
    )
    store.append_message(
        db,
        run_id,
        {"role": "user", "content": "工具执行结果：\n" + json.dumps(results, ensure_ascii=False)},
    )
    store.append_step(
        db,
        run_id,
        idx=run.action_steps,
        thought=brain_result.get("thought") or "",
        action={"actions": brain_result.get("actions") or []},
        result=results,
    )
    if brain_result.get("actions"):
        store.bump_action_steps(db, run_id)  # L2：本轮有产出动作才计步
    agent_events.emit(
        db,
        run_id,
        "assistant_turn",
        {
            "thought": brain_result.get("thought") or "",
            "actions": brain_result.get("actions") or [],
            "usage": {
                "tokens": (usage.get("prompt_tokens") or 0)
                + (usage.get("completion_tokens") or 0)
            },
        },
    )
    publish_agent_run_event(
        run_id, "step", {"idx": run.action_steps, "results": results}
    )

    if new_pending:
        store.set_pending(db, run_id, new_pending)
        store.set_status(db, run_id, "waiting")
        return {"status": "waiting", "pending": new_pending}
    store.set_pending(db, run_id, [])
    store.set_status(db, run_id, "waiting")
    await enqueue_agent_step(run_id)
    return {"status": "waiting", "self_kick": True}
