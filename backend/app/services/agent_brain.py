"""Agent 脑服务：JSON 动作协议 + OpenAI 兼容渠道直调 + 预算治理。

spec §3：单一 JSON 通路（不做原生 tool-calls）；解析容错（think 剥离/围栏/大括号
截取）+ 修复重试由编排器驱动；无渠道/预算熔断转为结构化 BrainChannelError。
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx
from sqlalchemy.orm import Session

from backend.app.services.llm_budget import BudgetExceeded, budget_check, budget_consume

_BRAIN_TIMEOUT = httpx.Timeout(240.0, connect=10.0)  # 本地 27B 长提示实测可超 120s
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


class BrainError(Exception):
    def __init__(self, code: str, message: str, hint: str = ""):
        self.code = code
        self.message = message
        self.hint = hint
        super().__init__(message)


class BrainProtocolError(BrainError):
    """动作 JSON 解析失败（编排器捕获后做一次修复重试）。"""

    def __init__(self, message: str, raw: str = ""):
        self.raw = raw
        super().__init__("PROTOCOL_ERROR", message, "模型输出不是合法的动作 JSON")


class BrainChannelError(BrainError):
    """渠道不可用 / 预算熔断 / 上游错误。"""


def strip_think(text: str) -> str:
    """剥离推理模型的 <think> 段（与 openai_compat._display_text 同语义）。"""
    if "</think>" in text:
        return text.split("</think>", 1)[1].strip()
    if text.lstrip().startswith("<think"):
        # 未闭合（流被截断）：丢弃全部
        return ""
    return _THINK_RE.sub("", text).strip()


def _extract_json_object(text: str) -> str:
    """围栏 / 散文包裹下截取首个平衡的 {...}。"""
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        return fence.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        candidate = text[start : end + 1]
        json.loads(candidate)  # 平衡性冒烟；不平衡再试逐层截取
        return candidate
    # 首尾大括号不平衡：从首个 { 起找首个平衡闭合
    depth = 0
    for i, ch in enumerate(text):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[text.find("{") : i + 1]
    raise BrainProtocolError("未找到平衡的 JSON 对象", text[:200])


def parse_action(text: str) -> dict[str, Any]:
    """模型输出 → {thought, actions:[{tool,args}]} 或 {thought, done, summary}。"""
    cleaned = strip_think((text or "").strip())
    if not cleaned:
        raise BrainProtocolError("模型输出为空", text or "")
    try:
        payload = json.loads(_extract_json_object(cleaned))
    except (json.JSONDecodeError, BrainProtocolError) as e:
        raise BrainProtocolError(f"动作 JSON 解析失败: {e}", text[:200]) from e
    if not isinstance(payload, dict):
        raise BrainProtocolError("动作必须是 JSON 对象", text[:200])
    thought = str(payload.get("thought") or "")
    if payload.get("done"):
        done_matches = payload.get("done_matches")
        return {
            "thought": thought,
            "done": True,
            "summary": str(payload.get("summary") or ""),
            # L1 完成合约（设计 §5.3）：终局校验的数据源，协议层只透传不解读
            "done_matches": done_matches if isinstance(done_matches, list) else None,
        }
    actions = payload.get("actions")
    if not isinstance(actions, list):
        raise BrainProtocolError("actions 必须是数组（或声明 done）", text[:200])
    for a in actions:
        if not isinstance(a, dict) or not isinstance(a.get("tool"), str) or not a["tool"]:
            raise BrainProtocolError("action 项必须含 tool 字符串", text[:200])
    return {"thought": thought, "actions": actions}


def build_system_prompt(tool_manifest: list[dict[str, Any]]) -> str:
    """工具清单自动生成（注册表驱动，MCP/skill 演进缝）。"""
    tools_desc = "\n".join(
        "- {}: {} 参数: {}".format(
            t["name"], t["description"], json.dumps(t.get("params", {}), ensure_ascii=False)
        )
        for t in tool_manifest
    )
    return (
        "你是 CreaGen Canvas 的创作 Agent，在无限画布上为用户完成多步创作（图/视频/音频/文字卡），"
        "通过逐步调用工具完成，产物会自动落为画布节点。\n"
        "可用工具：\n" + tools_desc + "\n"
        "每轮严格输出一个 JSON 对象（不要输出任何其他文字）：\n"
        '{"thought": "简短中文思考", "actions": [{"tool": "工具名", "args": {...}}]}\n'
        "或任务完成时：\n"
        '{"thought": "总结思考", "done": true, "summary": "创作成果中文摘要"}\n'
        "纪律：视频生成一次只派发一个（等上一轮任务完成再派下一个）；"
        "单个视频时长 4-15 秒（本地渠道上限），更长成片拆多镜头；"
        "多场次创作时每场先建一张「第N场·标题」文字卡作场标签，该场的关键帧与视频节点连线到标签附近保持成组；"
        "角色一致性：用 list_characters 查角色库 → create_character_node 放角色卡 "
        "→ 连线到各场节点保持外观；"
        "所有场次视频完成后，主动调用 compose_videos 按场序合成成片交付；"
        "可引用的参考图/首帧 URL 只能来自用户 @ 素材或本 run 产物；"
        "任务未完成前不要宣布 done；失败的任务会以错误观察返回给你，可调整参数重试一次，"
        "连续失败应换思路或结束并说明。"
    )


def _resolve_brain_provider(db: Session, user_id: int):  # noqa: ANN202 — (provider, channel)
    from backend.app.core.errors import ProviderNotConfiguredError
    from backend.app.services.provider_service import resolve_provider_for_task

    try:
        return resolve_provider_for_task(db, user_id, "llm", "")
    except ProviderNotConfiguredError as e:
        raise BrainChannelError(
            "LLM_CHANNEL_REQUIRED", "未配置可用的 LLM 渠道", "请在「设置 → 模型渠道」添加 LLM 渠道"
        ) from e


async def call_brain(
    db: Session,
    *,
    user_id: int,
    messages: list[dict[str, Any]],
    tool_manifest: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """一轮脑调用：预算检查 → chat/completions → 解析 → 记账。

    返回 parse_action 结果 + usage。FakeProvider（开发/演示）合成 done 响应保流程。
    """
    try:
        budget_check(db, f"llm:{user_id}")
    except BudgetExceeded as e:
        raise BrainChannelError("BUDGET_EXCEEDED", str(e), "当日 LLM 预算已耗尽") from e

    provider, _channel = _resolve_brain_provider(db, user_id)

    from backend.app.providers.fake import FakeProvider
    from backend.app.providers.openai_compat import OpenAICompatProvider

    if not isinstance(provider, OpenAICompatProvider) and not isinstance(provider, FakeProvider):
        raise BrainChannelError("BRAIN_PROVIDER_UNSUPPORTED", "该渠道类型不支持作为脑模型")

    if isinstance(provider, FakeProvider):
        return {
            "thought": "fake 渠道：直接完成",
            "done": True,
            "summary": "FakeProvider 合成响应",
            "usage": None,
            "channel_id": (getattr(_channel, "id", None) if _channel is not None else None),
        }

    system = build_system_prompt(tool_manifest or [])
    body = {
        "model": provider.model,
        "messages": [{"role": "system", "content": system}, *messages],
        "temperature": 0.3,
    }
    headers = {"Authorization": f"Bearer {provider.key_plain}"} if provider.key_plain else {}
    async with httpx.AsyncClient(timeout=_BRAIN_TIMEOUT) as client:
        try:
            resp = await client.post(
                f"{provider.base_url}/chat/completions", headers=headers, json=body
            )
        except httpx.TimeoutException as e:
            # 生产实证（2026-10-10 run#11）：裸 ReadTimeout 逃逸 → 卡 planning 等 sweeper
            raise BrainChannelError("BRAIN_TIMEOUT", f"脑渠道超时: {e}") from e
        except httpx.RequestError as e:
            raise BrainChannelError("BRAIN_UNREACHABLE", f"脑渠道不可达: {e}") from e
    if resp.status_code >= 400:
        err_body = str(resp.text or "")
        if resp.status_code == 400 and any(
            k in err_body.lower() for k in ("context", "token", "too long", "长度", "上下文")
        ):
            # M5：上下文溢出是确定性错误——退避重试无意义，编排器直接终态可续
            raise BrainChannelError(
                "BRAIN_CONTEXT_OVERFLOW", "脑上下文超限，需压缩", err_body[:200]
            )
        raise BrainChannelError(
            "BRAIN_UPSTREAM_ERROR", f"脑渠道返回 {resp.status_code}", err_body[:200]
        )
    data = resp.json()
    content = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    action = parse_action(content)  # 解析失败抛 BrainProtocolError（编排器修复重试）

    usage = data.get("usage") or {}
    pt = int(usage.get("prompt_tokens") or 0)
    ct = int(usage.get("completion_tokens") or 0)
    if pt or ct:
        budget_consume(db, f"llm:{user_id}", pt, ct)
        usage_out = {"prompt_tokens": pt, "completion_tokens": ct}
    else:
        usage_out = None
    return {
        **action,
        "usage": usage_out,
        "channel_id": (getattr(_channel, "id", None) if _channel is not None else None),
    }


async def summarize_for_compaction(db: Session, *, user_id: int, text: str) -> str | None:
    """M5 压缩摘要：raw chat 调用（不走动作协议）。任何失败返回 None（放弃本轮压缩）。"""
    provider, _channel = _resolve_brain_provider(db, user_id)
    from backend.app.providers.fake import FakeProvider
    from backend.app.providers.openai_compat import OpenAICompatProvider

    if not isinstance(provider, OpenAICompatProvider) and not isinstance(provider, FakeProvider):
        return None
    system = (
        "你是创作过程的档案员。把下面的 Agent 运行历史压缩成结构化中文摘要，必须保留："
        "①用户任务原文要点 ②计划与各交付物状态（含已完成产物的 url）③进行中/待办 "
        "④关键参考图 url 与角色锚定 ⑤重要教训与约束。直接输出摘要正文，不要客套。"
    )
    headers = {"Authorization": f"Bearer {provider.key_plain}"} if provider.key_plain else {}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
            resp = await client.post(
                f"{provider.base_url}/chat/completions",
                headers=headers,
                json={
                    "model": provider.model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": text},
                    ],
                    "temperature": 0.1,
                },
            )
        if resp.status_code >= 400:
            return None
        content = (resp.json().get("choices") or [{}])[0].get("message", {}).get("content") or ""
        return content.strip() or None
    except Exception:  # noqa: BLE001 — 摘要失败=放弃本轮压缩（设计 §7 降级路径）
        return None
