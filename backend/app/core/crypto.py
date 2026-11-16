"""渠道密钥 AES-GCM 加密。

ENCRYPTION_KEY（>=32 字符且非占位值，见 config.py 的启动校验）经 SHA-256 派生 32 字节 AES-256 密钥。
token 格式：`v1:<urlsafe-b64(2B version || 12B nonce || ciphertext)>`，版本前缀支持未来轮换。
"""

import base64
import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from backend.app.core.config import get_settings

_VERSION = b"v1"


class InvalidSecretToken(ValueError):
    """密文 token 格式错误、被篡改或无法解密。"""


def _aesgcm() -> AESGCM:
    key = hashlib.sha256(get_settings().encryption_key.encode()).digest()
    return AESGCM(key)


def encrypt_secret(plain: str) -> str:
    nonce = os.urandom(12)
    payload = _VERSION + nonce + _aesgcm().encrypt(nonce, plain.encode(), _VERSION)
    return "v1:" + base64.urlsafe_b64encode(payload).decode()


def decrypt_secret(token: str) -> str:
    if not token.startswith("v1:"):
        raise InvalidSecretToken("token 缺少版本前缀")
    try:
        raw = base64.urlsafe_b64decode(token[3:].encode())
        version, nonce, ciphertext = raw[:2], raw[2:14], raw[14:]
        if version != _VERSION:
            raise InvalidSecretToken(f"未知版本: {version!r}")
        return _aesgcm().decrypt(nonce, ciphertext, version).decode()
    except InvalidSecretToken:
        raise
    except Exception as e:  # noqa: BLE001 — 任何解密失败统一视为 token 无效
        raise InvalidSecretToken("token 解密失败") from e
