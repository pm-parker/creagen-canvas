"""生成任务失败分类（0.9.1 可靠性冲刺）：确定性失败不重试，瞬时失败才值得烧钱。

判据来自生产 7 天失败归因（2026-10-03 盘点）：FileNotFoundError/配额上限/参数与能力
错误属确定性（重试必败）；超时/429/5xx/未返回产物属瞬时（重试有收益）。
未知错误按确定性处理——宁可不自动重试，也不烧用户的钱。
"""

from __future__ import annotations

import re

DETERMINISTIC = "deterministic"
TRANSIENT = "transient"

_DETERMINISTIC_PATTERNS = (
    r"FileNotFoundError",
    r"文件不存在",
    r"首帧图获取失败",
    r"Token Plan",  # MiniMax 配额
    r"用量上限",
    r"额度",
    r"quota",
    r"usage limit",
    r"Model does not exist",
    r"不存在.*模型|模型.*不存在",
    r"invalid params",
    r"PUBLIC_BASE_URL",
    r"图像编辑能力",
    r"未开启",
    r"TASK_TYPE_INVALID",
    r"不支持\s*video|不支持.*任务类型",
    r"已达到.*上限",
)

_TRANSIENT_PATTERNS = (
    r"timeout",
    r"超时",
    r"429",
    r"rate limit",
    r"HTTP 500",
    r"HTTP 502",
    r"HTTP 503",
    r"未返回产物链接",
    r"连接失败",
    r"unreachable",
    r"渠道返回 Fail",  # Minimax 偶发 Fail，重试有收益
)


def classify_failure(error: str | None) -> str:
    """分类生成失败：DETERMINISTIC（不重试）/ TRANSIENT（可重试）。

    未知错误归 DETERMINISTIC——自动重试烧的是真钱，误判方向的代价不对称。
    """
    text = (error or "").strip()
    if not text:
        return DETERMINISTIC
    low = text.lower()
    for p in _DETERMINISTIC_PATTERNS:
        if re.search(p, text, re.I) or re.search(p, low):
            return DETERMINISTIC
    for p in _TRANSIENT_PATTERNS:
        if re.search(p, text, re.I):
            return TRANSIENT
    return DETERMINISTIC


def retry_hint(error: str | None) -> str | None:
    """确定性失败的用户可读提示（追加到错误信息）；瞬时失败返回 None。"""
    if classify_failure(error) != DETERMINISTIC:
        return None
    text = error or ""
    if re.search(r"Token Plan|用量上限|额度|quota|usage limit", text, re.I):
        return "渠道配额已用尽，请升级套餐或更换渠道后重试（自动重试已跳过）"
    if re.search(r"FileNotFoundError|文件不存在|首帧图获取失败", text, re.I):
        return "引用的首帧/尾帧文件已被清理，请重出上一段或清除此段的定帧后重试（自动重试已跳过）"
    if re.search(r"Model does not exist|模型.*不存在|invalid params", text, re.I):
        return "渠道/模型配置不匹配，请检查模型渠道设置（自动重试已跳过）"
    if re.search(r"PUBLIC_BASE_URL", text):
        return "服务端未配置 PUBLIC_BASE_URL，图生视频需要 GPU 机器可访问本服务"
    return "该错误为确定性失败，自动重试已跳过，请排查配置后手动重试"
