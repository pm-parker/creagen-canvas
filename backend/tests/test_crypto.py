"""渠道密钥 AES-GCM 加密（Review Focus #1 的底层保证）。"""

from backend.app.core.crypto import decrypt_secret, encrypt_secret


def test_roundtrip_and_uniqueness():
    a = encrypt_secret("sk-test")
    b = encrypt_secret("sk-test")
    assert a != b, "随机 nonce 下同一明文两次密文必须不同"
    assert decrypt_secret(a) == "sk-test"
    assert decrypt_secret(b) == "sk-test"


def test_token_format_v1():
    token = encrypt_secret("sk-abc")
    assert token.startswith("v1:"), "密文 token 必须带版本前缀以便未来轮换"


def test_tampered_token_rejected():
    import pytest

    from backend.app.core.crypto import InvalidSecretToken

    token = encrypt_secret("sk-abc")
    tampered = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    with pytest.raises(InvalidSecretToken):
        decrypt_secret(tampered)
