"""Provider 抽象基类与注册表（spec A3 四域接口）。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from backend.app.models.provider import PROVIDER_DOMAINS, ProviderChannel


class ProviderError(RuntimeError):
    def __init__(self, code: str, message: str, hint: str = ""):
        self.code = code
        self.message = message
        self.hint = hint
        super().__init__(message)


class ProviderBase(ABC):
    # 协作取消协议（2026-10-08）：worker 注入 DB 查询闭包；provider 轮询循环
    # 每轮自查——shield 孤儿协程可提前退出，不再白轮到总超时。
    cancel_check: Any = None

    def _check_cancelled(self) -> bool:
        cb = getattr(self, "cancel_check", None)
        try:
            return bool(cb and cb())
        except Exception:  # noqa: BLE001 — 检查失败按未取消处理
            return False

    """四域 Provider 统一接口。

    run() 在 arq worker 内执行，返回：
      {"status": "completed", "output_url": str}
      {"status": "failed", "error": str}
    """

    domain: str
    key_plain: str

    def __init__(self, model: str, channel: ProviderChannel | None) -> None:
        self.model = model
        self.channel = channel
        self.key_plain = ""

    @abstractmethod
    async def test_connection(self) -> bool: ...

    @abstractmethod
    async def list_models(self) -> list[str]: ...

    @abstractmethod
    async def run(
        self, task: Any, on_event: Callable[[str, dict], None] | None = None
    ) -> dict[str, Any]:
        """执行生成。on_event(phase, payload) 用于过程上报。"""


class ProviderRegistry:
    """域 → provider 实例解析。

    W1 仅内置 FakeProvider（ENABLE_FAKE_PROVIDER=true 时无渠道也可走通闭环）；
    W3 起按渠道 key 解析真实实现（openai_compat 等）。
    """

    def __init__(self) -> None:
        self._by_domain: dict[str, type[ProviderBase]] = {}

    def register(self, domain: str, provider_cls: type[ProviderBase]) -> None:
        if domain not in PROVIDER_DOMAINS:
            raise ValueError(f"未知 provider 域: {domain}")
        self._by_domain[domain] = provider_cls

    def resolve(
        self,
        domain: str,
        model: str,
        channel: ProviderChannel | None,
        *,
        allow_fake: bool = False,
    ) -> ProviderBase:
        if domain not in PROVIDER_DOMAINS:
            raise ProviderError("TASK_TYPE_INVALID", f"不支持的域: {domain}")
        if channel is None:
            if allow_fake and domain in self._by_domain:
                return self._by_domain[domain](model=model, channel=None)
            raise ProviderError(
                "PROVIDER_NOT_CONFIGURED",
                f"尚未配置可用的 {domain} 渠道",
                "请在「设置 → 模型渠道」添加渠道后重试",
            )
        cls = self._by_domain.get(domain)
        if cls is None:
            raise ProviderError(
                "PROVIDER_NOT_IMPLEMENTED",
                f"{domain} 域渠道实现尚未就绪",
                "该渠道类型将在后续版本支持",
            )
        return cls(model=model, channel=channel)


registry = ProviderRegistry()
