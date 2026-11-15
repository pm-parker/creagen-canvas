"""失败分类器契约：生产 7 天失败归因逐条回放（0.9.1 盘点 B1）。"""

import pytest

from backend.app.services.failure_classifier import (
    DETERMINISTIC,
    TRANSIENT,
    classify_failure,
    retry_hint,
)


@pytest.mark.parametrize(
    "error",
    [
        "首帧图获取失败: FileNotFoundError",
        "图生视频需要配置 PUBLIC_BASE_URL（GPU 机器需能访问本服务）",
        "当前已达到 Token Plan 用量上限。为避免调用中断，请升级 Token",
        "Model does not exist. Please check it carefully.",
        "invalid params, incorrect model param in",
        "OpenAI 兼容渠道不支持 video",
    ],
)
def test_production_deterministic_failures(error):
    assert classify_failure(error) == DETERMINISTIC


@pytest.mark.parametrize(
    "error",
    [
        "渠道响应超时",
        "OpenAI 兼容渠道 Error: 429 too many requests",
        "本地 H3 服务错误 HTTP 500",
        "未返回产物链接",
        "渠道返回 Fail",
        "渠道连接失败",
    ],
)
def test_production_transient_failures(error):
    assert classify_failure(error) == TRANSIENT


def test_unknown_error_is_deterministic_no_free_retry():
    assert classify_failure("神秘错误 XYZ") == DETERMINISTIC
    assert classify_failure("") == DETERMINISTIC
    assert classify_failure(None) == DETERMINISTIC


def test_retry_hint_quota_and_file():
    quota_hint = retry_hint("当前已达到 Token Plan 用量上限")
    assert quota_hint is not None and "配额" in quota_hint
    tail_hint = retry_hint("首帧图获取失败: FileNotFoundError")
    assert tail_hint is not None and "尾帧" in tail_hint
    assert retry_hint("渠道响应超时") is None
