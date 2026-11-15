"""Provider 包入口：注册内置实现。"""

from backend.app.providers.fake import register_builtin
from backend.app.providers.registry import ProviderBase, ProviderError, registry

register_builtin(registry)

__all__ = ["ProviderBase", "ProviderError", "registry"]
