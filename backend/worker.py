"""arq worker：generate / maintenance 两队列的任务执行进程。

derived from creagen prod app/worker.py @ 2026-09-27（W1 精简版）。
启动：arq backend.worker.WorkerSettings
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from typing import Any

from arq import cron
from redis import Redis

from backend.app.core.config import get_settings
from backend.app.core.db import get_session_factory
from backend.app.providers import registry
from backend.app.services.agent_orchestrator import sweep_stale_runs
from backend.app.services.model_pricing import resolve_cost, resolve_model_pricing
from backend.app.services.task_lifecycle import run_task_lifecycle
from backend.app.services.task_queue import (
    QUEUE_GENERATE,
    get_sync_redis,
    publish_task_event,
    redis_settings_from_url,
)
from backend.app.services.task_store import get_task_or_none

logger = logging.getLogger("canvas.worker")

_coroutine_count: int = 0


async def run_generation_task(
    ctx: dict[str, Any], task_id: int, _gen_override: Any = None
) -> dict[str, Any]:
    """arq 入口：查任务 → 解析 provider → 生命周期包装执行。

    _gen_override 仅测试注入（跳过 provider 解析直接给生成函数）。
    """
    from backend.app.core.config import get_settings as _gs

    db = get_session_factory()()
    try:
        task = get_task_or_none(db, task_id)
        if task is None:
            logger.error("Task %s not found", task_id)
            return {"status": "failed", "error": "task not found"}

        from backend.app.core.config import get_settings as _gs
        from backend.app.core.errors import ProviderNotConfiguredError
        from backend.app.services.provider_service import resolve_provider_for_task

        task_params = task.params_dict()
        channel_id = task_params.get("channel_id")
        # 配置价优先，内置建议价回落（spec §5.2）；含 channel_id 才能同名模型按渠道精确计价
        estimated_cost = resolve_cost(
            db, task.type, task.model, task_params, channel_id=channel_id, prompt=task.prompt
        )
        if task.type == "llm":
            from backend.app.services.llm_budget import budget_check, budget_consume

            budget_check(db, f"llm:{task.user_id}")
            budget_consume(db, f"llm:{task.user_id}", len(task.prompt) * 2, 0, calls=1)
        try:
            provider, _channel = resolve_provider_for_task(
                db, task.user_id, task.type, task.model, channel_id=channel_id
            )
            # 局部重绘：渠道必须显式开启 edit 能力（开放架构——支持 /images/edits 的渠道自行勾选）。
            # 注意：此处位于 task_lifecycle 之前，失败必须直接落库（抛异常会让任务卡 queued）。
            if task.type == "image" and task_params.get("mode") == "edit":
                caps = _channel._capabilities_list() if _channel is not None else []
                if "edit" not in caps and "edit_ref" not in caps:
                    msg = (
                        "当前渠道未开启图像编辑能力。请在「模型渠道」编辑该渠道并勾选"
                        "「支持图像编辑」或「支持参考图编辑」，或更换支持的渠道。"
                    )
                    task.status = "failed"
                    task.error_msg = msg
                    db.commit()
                    # 直接落库路径也必须发布终态事件：否则 SSE 订阅者永远收不到，
                    # 前端节点卡"生成中"（教训⑨配套）
                    publish_task_event(task_id, "failed", {"error": msg})
                    logger.warning("task %s edit capability missing", task_id)
                    return {"status": "failed", "error": msg}
        except ProviderNotConfiguredError:
            if _gs().enable_fake_provider:
                provider = registry.resolve(task.type, task.model, None, allow_fake=True)
            else:
                raise

        def on_event(event_type: str, data: dict[str, Any]) -> None:
            publish_task_event(task_id, event_type, data)
            if event_type == "delta":
                # LLM 流式快照（累计文本）：中途订阅的 SSE 先重放再续流（幂等语义）
                with contextlib.suppress(Exception):  # 快照失败不影响流式
                    get_sync_redis().set(
                        f"canvas:task_text:{task_id}", str(data.get("text", "")), ex=3600
                    )

        def generate_fn(t: Any) -> Any:
            return provider.run(t, on_event=on_event)

        if _gen_override is not None:
            generate_fn = _gen_override

        async def attach_llm_exact_cost(t: Any) -> Any:
            """LLM per_token_1m 配置：完成时按真实 usage 精确计价（spec §5.2）。

            未配置模型不写 cost_amount 键——维持现金成本记 0 现状（兼容承诺）。
            放在 override 赋值之后：测试注入路径同样被覆盖。
            """
            result = await generate_fn(t)
            if (
                task.type == "llm"
                and isinstance(result, dict)
                and result.get("status") == "completed"
                and resolve_model_pricing(
                    db, task.model, channel_id=channel_id, domain="llm"
                ) is not None
            ):
                # 五遍复检 G3：流式路径无 usage——按 len(prompt)*2 估算 input 侧，
                # 而非恒 ¥0（与 budget_consume 同一估算口径）
                usage = result.get("usage") or {
                    "prompt_tokens": len(task.prompt) * 2,
                    "completion_tokens": 0,
                }
                result["cost_amount"] = resolve_cost(
                    db,
                    "llm",
                    task.model,
                    task_params,
                    channel_id=channel_id,
                    usage=usage,
                )
            return result

        async def generate_with_retry(t: Any) -> dict[str, Any]:
            """高并发排队场景（如多角度并行打本地 ComfyUI 串行队列）：瞬时失败
            （超时/429/连接闪断）自动重试一次——确定性失败不烧预算直接终局。"""
            res = await attach_llm_exact_cost(t)
            if res.get("status") != "failed":
                return res
            from backend.app.services.failure_classifier import DETERMINISTIC, classify_failure

            if classify_failure(str(res.get("error") or "")) == DETERMINISTIC:
                return res
            # 2026-10-08 事故修正：视频超时不再重试——本地 GPU 过载时排队+生成
            # 时间爆表，重试只会再挤一次队列（arq queued 24→33 实证放大效应）。
            _err_txt = str(res.get("error") or "")
            if task.type == "video" and ("超时" in _err_txt or "timeout" in _err_txt.lower()):
                logger.warning(
                    "task %s video timeout, skip retry (overload amplifier): %s",
                    task_id, str(res.get("error"))[:100],
                )
                return res
            import asyncio as _aio

            logger.warning(
                "task %s transient failure, auto retry once: %s",
                task_id, str(res.get("error"))[:120],
            )
            await _aio.sleep(5)
            return await attach_llm_exact_cost(t)

        # 视频渠道闸门：同渠道并发 ≤2，超出的在闸门前排队（不提交上游），
        # 前序释放才真正提交（2026-10-08 过载事故修复）。测试/直调无 redis 直通。
        from backend.app.services.video_gate import VIDEO_GATE_LIMIT

        # 协作取消注入：provider 轮询循环自查（shield 孤儿提前退出）
        try:
            from backend.app.services.task_store import is_cancel_requested

            provider.cancel_check = lambda: is_cancel_requested(db, task_id)
        except Exception:  # noqa: BLE001 — 注入失败不影响主流程
            pass
        _gate_redis = (ctx or {}).get("redis") if _gen_override is None else None
        _gate_ch = _channel.id if _channel is not None else task.model
        # 渠道差异化限数：本地单卡 provider 声明 gate_limit=1（串行最高吞吐），
        # 其余渠道用默认 2（远端多卡有余量）
        _gate_limit = getattr(provider, "gate_limit", VIDEO_GATE_LIMIT)
        _gate_held = False
        if _gate_redis is not None and task.type == "video":
            from backend.app.services.video_gate import acquire_video_gate

            if not await acquire_video_gate(_gate_redis, _gate_ch, limit=_gate_limit):
                msg = "视频排队超时（渠道并发闸门），请稍后重试或错峰提交"
                task.status = "failed"
                task.error_msg = msg
                db.commit()
                publish_task_event(task_id, "failed", {"error": msg})
                return {"status": "failed", "error": msg}
            _gate_held = True
        try:
            result = await run_task_lifecycle(
                db, task_id, generate_with_retry,
                user_id=task.user_id, estimated_cost=estimated_cost,
            )
        finally:
            if _gate_held:
                from backend.app.services.video_gate import release_video_gate

                with contextlib.suppress(Exception):  # 释放失败不掩盖主结果
                    await release_video_gate(_gate_redis, _gate_ch)
        # M2: 分镜段任务终态钩子——推进编排（永不影响任务主流程）
        try:
            from backend.app.services import storyboard_orchestrator

            await storyboard_orchestrator.advance_on_task_terminal(db, task, result)
        except Exception:  # noqa: BLE001
            logger.exception("storyboard advance failed for task %s", task_id)
        # M3: 首页 Agent run 终态钩子——task 带 agent_run_id 即 kick 下一轮迭代
        try:
            from backend.app.services import agent_orchestrator

            await agent_orchestrator.advance_on_task_terminal(db, task)
        except Exception:  # noqa: BLE001
            logger.exception("agent advance failed for task %s", task_id)
        return result
    finally:
        db.close()


async def run_agent_step(ctx: dict[str, Any], run_id: int) -> dict[str, Any]:
    """arq 入口：Agent run 单轮迭代（脑调用+工具派发；CAS 幂等可重复入队）。"""
    from backend.app.services import agent_orchestrator

    db = get_session_factory()()
    try:
        return await agent_orchestrator.kick_run(db, run_id)
    except Exception:  # noqa: BLE001 — 单轮失败不坠 worker；sweeper 兜底
        logger.exception("agent step failed for run %s", run_id)
        try:
            from backend.app.services import agent_run_store

            row = agent_run_store.get_run(db, run_id)
            # 复活守卫：仅 planning 复位 waiting——终态（completed 等）绝不回退
            if row is not None and row.status == "planning":
                agent_run_store.set_status(db, run_id, "waiting")  # sweeper 可再推
        except Exception:  # noqa: BLE001
            pass
        return {"status": "error"}
    finally:
        db.close()


async def run_storyboard_kick(ctx: dict[str, Any], storyboard_id: int) -> dict[str, int]:
    """arq 入口：分镜编排推进（按容量派发下一段；幂等可重复入队）。"""
    from backend.app.services import storyboard_orchestrator

    db = get_session_factory()()
    try:
        return await storyboard_orchestrator.kick_storyboard(db, storyboard_id)
    except Exception:  # noqa: BLE001 — 单次推进失败不坠 worker
        logger.exception("storyboard kick failed for %s", storyboard_id)
        return {"dispatched": 0}
    finally:
        db.close()


async def run_storyboard_compose(
    ctx: dict[str, Any], storyboard_id: int, bgm_url: str | None = None
) -> dict[str, Any]:
    """arq 入口：成片合成（ffmpeg 重活在服务内 to_thread；永不抛出）。"""
    from backend.app.services import storyboard_orchestrator

    db = get_session_factory()()
    try:
        return await storyboard_orchestrator.compose_storyboard(
            db, storyboard_id, bgm_url=bgm_url
        )
    except Exception:  # noqa: BLE001 — compose 服务已内兜底，双保险
        logger.exception("storyboard compose crashed for %s", storyboard_id)
        return {"ok": False, "error": "internal"}
    finally:
        db.close()


async def _maintenance_cron(ctx: dict[str, Any]) -> None:
    """cron 包装（生产实证修复 2026-10-10）：本版 arq cron 不支持 kwargs，
    直接挂 run_maintenance_job 会以 job_name=None 空转——周期 sweeper 形同虚设。"""
    await run_maintenance_job(ctx, job_name="cleanup_stale_tasks")


async def run_maintenance_job(
    ctx: dict[str, Any], job_name: str | None = None, payload: dict | None = None
) -> None:
    if job_name == "cleanup_stale_tasks":
        await _cleanup_stale_tasks()
        await _sweep_stale_agent_runs()
        await _watch_comfyui()
    else:
        logger.info("maintenance job %s: %s", job_name, payload)


async def _watch_comfyui() -> None:
    """ComfyUI 队列卡死看护（env 配置，空=关闭）——interrupt 收尸替代人工 8198 操作。"""
    from backend.app.services import comfy_watch

    try:
        r = await comfy_watch.watch_comfyui_queues()
        if r.get("interrupted"):
            logger.warning("comfy watch recovered %s stuck job(s)", r["interrupted"])
    except Exception:  # noqa: BLE001 — 看护失败不坠维护主流程
        logger.exception("comfy watch crashed")


async def _sweep_stale_agent_runs() -> None:
    """Agent run 兜底：waiting 全终态超时 / planning 卡死 → re-kick（spec §2）。"""
    from backend.app.services import agent_orchestrator
    from backend.app.services.task_queue import enqueue_agent_step

    db = get_session_factory()()
    try:
        for run_id in agent_orchestrator.sweep_stale_runs(db):
            try:
                await enqueue_agent_step(run_id)
            except Exception:  # noqa: BLE001
                logger.warning("agent sweep enqueue %s failed", run_id)
    finally:
        db.close()


async def _cleanup_stale_tasks() -> None:
    """generating 超过 30 分钟的任务标记失败（worker 崩溃/重启遗留的死任务）。"""
    from datetime import datetime, timedelta

    from backend.app.models import Task
    from backend.app.models.storyboard import Storyboard
    from backend.app.services.storyboard_orchestrator import _meta

    db = get_session_factory()()

    # compose_status=running 卡死清理：worker 重启会中断合成进程，无 job 收尾
    try:
        cutoff = datetime.utcnow() - timedelta(minutes=10)
        stuck = (
            db.query(Storyboard)
            .filter(
                Storyboard.metadata_json.contains('"compose_status": "running"'),
                Storyboard.updated_at < cutoff,
            )
            .all()
        )
        for sb in stuck:
            if _meta(sb).get("compose_status") != "running":
                continue
            meta = _meta(sb)
            meta["compose_status"] = "failed"
            meta["compose_error"] = "合成中断（服务重启），请重新合成"
            sb.metadata_json = __import__("json").dumps(meta, ensure_ascii=False)
            db.commit()
            from backend.app.services.task_queue import publish_storyboard_event

            publish_storyboard_event(
                sb.id, "compose_failed", {"compose_status": "failed", "error": "stale"}
            )
            logger.warning("storyboard %s stale compose cleaned", sb.id)
    except Exception:  # noqa: BLE001 — 维护任务不因单次失败退出
        db.rollback()
        logger.exception("stale compose cleanup failed")

    # 0.9.4 关键帧接力链卡死自愈（worker 重启中断链）
    try:
        cutoff_kf = datetime.utcnow() - timedelta(minutes=10)
        stuck_chain = (
            db.query(Storyboard)
            .filter(
                Storyboard.metadata_json.contains('"keyframe_chain": true'),
                Storyboard.updated_at < cutoff_kf,
            )
            .all()
        )
        from backend.app.services.storyboard_orchestrator import clear_stale_keyframe_chain

        for sb in stuck_chain:
            if clear_stale_keyframe_chain(sb):
                db.commit()
                logger.warning("storyboard %s stale keyframe chain cleared", sb.id)
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("stale keyframe chain cleanup failed")
    try:
        # 审计批次二（P2）：远端素材暂存清理（合成可重新下载，>7 天即删）
        from backend.app.services.media_service import cleanup_media_downloads

        removed_dl = cleanup_media_downloads()
        if removed_dl:
            logger.info("cleanup_media_downloads: removed %d stale file(s)", removed_dl)
    except Exception:  # noqa: BLE001
        logger.exception("media downloads cleanup failed")
    try:
        from backend.app.core.timeouts import CLEANUP_STALE_MIN

        cutoff = datetime.utcnow() - timedelta(minutes=CLEANUP_STALE_MIN)
        stale = (
            db.query(Task)
            .filter(
                Task.status == "generating",
                Task.started_at < cutoff,
            )
            .all()
        )
        if stale:
            reaped = 0
            for t in stale:
                # 终态写保护：条件 UPDATE 防竞态（撤销中恰逢完成的任务不被覆盖）
                n = (
                    db.query(Task)
                    .filter(Task.id == t.id, Task.status == "generating")
                    .update(
                        {
                            "status": "failed",
                            "error_msg": "生成超时：任务可能因服务重启中断，请重试",
                        },
                        synchronize_session=False,
                    )
                )
                db.commit()
                if n == 0:
                    continue  # 已被并发写终态（如恰好完成）——尊重结果
                reaped += 1
                _reap_msg = "生成超时：任务可能因服务重启中断，请重试"
                publish_task_event(t.id, "failed", {"error": _reap_msg})
                # 僵尸根治：远程撤销上游 job（收尸场景允许 interrupt——
                # generating>30min 时 GPU 上全是孤儿任务）
                try:
                    from backend.app.services.provider_service import resolve_provider_for_task

                    provider, _ch = resolve_provider_for_task(
                        db, t.user_id, t.type, t.model,
                        channel_id=(t.params_dict() or {}).get("channel_id"),
                    )
                    cancel_fn = getattr(provider, "cancel_remote_job", None)
                    if cancel_fn is not None:
                        await cancel_fn(t.id, interrupt=True)
                except Exception:  # noqa: BLE001 — 撤销失败不影响收尸（下轮重试）
                    logger.warning("reap remote cancel failed for task %s", t.id, exc_info=True)
            logger.info("cleanup_stale_tasks: marked %d stale task(s) failed", reaped)
    except Exception:  # noqa: BLE001 — 维护任务不因单次失败退出
        db.rollback()
        logger.exception("cleanup_stale_tasks failed")
    finally:
        db.close()


async def worker_startup(ctx: dict[str, Any]) -> None:
    client: Redis = Redis.from_url(get_settings().redis_url, decode_responses=True)
    ctx["redis"] = client
    ctx["hb_task"] = asyncio.create_task(_heartbeat_loop(ctx))
    logger.info("worker started, queues=%s", ctx.get("queues", []))
    # L3/B4：boot 扫描 = 调用一次 sweeper（DB 即 manifest，不写第二套判定）
    try:
        session = get_session_factory()()
        try:
            kicked = sweep_stale_runs(session)
        finally:
            session.close()
        if kicked:
            logger.info("boot sweep re-kicked runs: %s", kicked)
    except Exception as exc:  # noqa: BLE001 — 启动扫描失败不阻断 worker
        logger.warning("boot sweep failed: %s", exc)


async def worker_shutdown(ctx: dict[str, Any]) -> None:
    hb = ctx.get("hb_task")
    if hb:
        hb.cancel()
    redis: Redis | None = ctx.get("redis")
    if redis:
        redis.close()
    logger.info("worker stopped")


async def _heartbeat_loop(ctx: dict[str, Any]) -> None:
    redis: Redis = ctx["redis"]
    while True:
        try:
            redis.set("canvas:worker:generate:heartbeat", "1", ex=30)
        except Exception:  # noqa: BLE001
            logger.warning("heartbeat failed")
        await asyncio.sleep(10)


def _load_timeouts() -> int:
    from backend.app.core.timeouts import ARQ_JOB_TIMEOUT_S

    return ARQ_JOB_TIMEOUT_S


class WorkerSettings:
    functions = [
        run_generation_task,
        run_storyboard_kick,
        run_storyboard_compose,
        run_agent_step,
        run_maintenance_job,
    ]
    cron_jobs = [
        cron(
            _maintenance_cron,
            name="cleanup_stale_tasks",
            minute=None,
            second=0,
            unique=True,
            timeout=60,
        ),
    ]
    on_startup = worker_startup
    on_shutdown = worker_shutdown
    redis_settings = redis_settings_from_url(get_settings().redis_url)
    max_jobs = 4
    job_timeout = _load_timeouts()  # 单点声明见 core/timeouts.py（2026-10-08）
    queue_name = os.environ.get("WORKER_QUEUE", QUEUE_GENERATE)  # P2-14: video 独立 worker
    health_check_interval = 30
    keep_result = 3600
