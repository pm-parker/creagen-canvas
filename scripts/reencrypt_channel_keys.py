"""ENCRYPTION_KEY 轮换：把库里已加密的渠道密钥从旧密钥重加密到新密钥。

用法（二选一）::

    python -m scripts.reencrypt_channel_keys --old-key <旧值> --new-key <新值> --dry-run
    python scripts/reencrypt_channel_keys.py --old-key <旧值> --new-key <新值>

密钥来源：--old-key / --new-key，缺省回落环境变量 OLD_ENCRYPTION_KEY / ENCRYPTION_KEY。
数据库连接：环境变量 DATABASE_URL，缺省回落应用 Settings（.env）。

行为（真实运行在【单一事务】内完成，任一行无法解密即整体回滚）：
- 已能用新密钥解密的行：跳过（幂等，可安全重跑）；
- 能用旧密钥解密的行：解密后用新密钥重加密并更新；
- 两边都解不开的行：正式运行中止并回滚全部改动，非零退出（dry-run 则计数并提示）。
--dry-run 只统计（to_reencrypt / already_new / undecryptable / empty），不写库。
任何输出都不包含密钥或渠道明文 key。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
import sys
from collections.abc import Iterable, Sequence
from typing import Any

# 允许 `python scripts/reencrypt_channel_keys.py`（sys.path[0]=scripts/）直接运行
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: E402
from sqlalchemy import create_engine, select, update  # noqa: E402
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402

from backend.app.core.config import validate_secret_strength  # noqa: E402
from backend.app.core.crypto import _VERSION as TOKEN_VERSION  # noqa: E402
from backend.app.models.provider import ProviderChannel  # noqa: E402


def _build_cipher(secret: str) -> AESGCM:
    """与 backend/app/core/crypto.py 完全一致的密钥派生（SHA-256 -> AES-256-GCM）。"""
    return AESGCM(hashlib.sha256(secret.encode()).digest())


def _try_decrypt(token: str, cipher: AESGCM) -> str | None:
    """尝试解密；失败（格式错/密钥不对/被篡改）返回 None。绝不抛出、绝不打印。"""
    if not token.startswith("v1:"):
        return None
    try:
        raw = base64.urlsafe_b64decode(token[3:].encode())
        version, nonce, ciphertext = raw[:2], raw[2:14], raw[14:]
        if version != TOKEN_VERSION:
            return None
        return cipher.decrypt(nonce, ciphertext, version).decode()
    except Exception:  # noqa: BLE001 — 任何失败都按“解不开”处理
        return None


def _encrypt(plain: str, cipher: AESGCM) -> str:
    """与 core/crypto.encrypt_secret 同格式（v1: 前缀 + 随机 nonce + AES-GCM）。"""
    nonce = os.urandom(12)
    payload = TOKEN_VERSION + nonce + cipher.encrypt(nonce, plain.encode(), TOKEN_VERSION)
    return "v1:" + base64.urlsafe_b64encode(payload).decode()


def _resolve_key(cli_value: str | None, env_name: str, flag: str, what: str) -> str:
    value = (cli_value or os.environ.get(env_name, "")).strip()
    if not value:
        raise SystemExit(f"缺少{what}：请传 {flag} 或设置环境变量 {env_name}")
    return value


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        return url
    try:
        from backend.app.core.config import get_settings

        return get_settings().database_url
    except Exception:  # noqa: BLE001 — Settings 校验失败时给可行动的错误
        msg = "无法确定数据库连接：请设置 DATABASE_URL 环境变量（或提供可加载的 .env）"
        raise SystemExit(msg) from None


def _classify_rows(
    rows: Iterable[Any], old_cipher: AESGCM, new_cipher: AESGCM
) -> tuple[list[tuple[int, str, str]], int, int, list[int]]:
    """逐行分类：返回（待重加密 [(id, 旧密文, 新密文)], already_new, empty, undecryptable_ids）。"""
    todo: list[tuple[int, str, str]] = []
    already_new = empty = 0
    bad_ids: list[int] = []
    for row in rows:
        row_id, token = int(row[0]), str(row[1])
        if not token:
            empty += 1
            continue
        if _try_decrypt(token, new_cipher) is not None:
            already_new += 1  # 已是新密钥加密，幂等跳过
            continue
        plain = _try_decrypt(token, old_cipher)
        if plain is None:
            bad_ids.append(row_id)
            continue
        re_encrypted = _encrypt(plain, new_cipher)
        # 写库前先验证新密文可解且与原文一致（防格式/密钥派生不一致静默坏库）
        if _try_decrypt(re_encrypted, new_cipher) != plain:
            bad_ids.append(row_id)
            continue
        todo.append((row_id, token, re_encrypted))
    return todo, already_new, empty, bad_ids


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "ENCRYPTION_KEY 轮换：重加密 provider_channels.key_encrypted（单一事务，失败全回滚）"
        )
    )
    parser.add_argument("--old-key", help="旧 ENCRYPTION_KEY（缺省用环境变量 OLD_ENCRYPTION_KEY）")
    parser.add_argument("--new-key", help="新 ENCRYPTION_KEY（缺省用环境变量 ENCRYPTION_KEY）")
    parser.add_argument(
        "--dry-run", action="store_true", help="只统计待重加密/已更新/无法解密的行数，不写库"
    )
    args = parser.parse_args(argv)

    try:
        old_key = _resolve_key(args.old_key, "OLD_ENCRYPTION_KEY", "--old-key", "旧密钥")
        new_key = _resolve_key(args.new_key, "ENCRYPTION_KEY", "--new-key", "新密钥")
        # 新密钥同样受 Settings 强度约束（>=32 且非占位值）；旧密钥允许偏短（存量部署）
        validate_secret_strength("ENCRYPTION_KEY(新)", new_key)
    except (SystemExit, ValueError) as e:  # 缺参/强度不足：统一 stderr + 非零退出码
        print(str(e), file=sys.stderr)
        return 1

    engine = create_engine(_database_url())
    old_cipher, new_cipher = _build_cipher(old_key), _build_cipher(new_key)

    with engine.connect() as conn:
        trans = conn.begin()
        try:
            rows: Sequence[Any] = conn.execute(
                select(ProviderChannel.id, ProviderChannel.key_encrypted)
            ).all()
            todo, already_new, empty, bad_ids = _classify_rows(rows, old_cipher, new_cipher)
            if args.dry_run:
                trans.rollback()  # dry-run 绝不写库
                print(
                    f"dry-run: to_reencrypt={len(todo)} already_new={already_new} "
                    f"undecryptable={len(bad_ids)} empty={empty}"
                )
                if bad_ids:
                    print(
                        f"发现 {len(bad_ids)} 行用新旧密钥均无法解密"
                        f"（provider_channels.id 示例: {bad_ids[:5]}），"
                        "正式运行会中止并回滚。请确认 --old-key 是否正确。",
                        file=sys.stderr,
                    )
                    return 1
                return 0
            if bad_ids:
                trans.rollback()
                print(
                    f"中止：{len(bad_ids)} 行渠道密钥用新旧密钥均无法解密"
                    f"（provider_channels.id 示例: {bad_ids[:5]}），"
                    "已回滚全部改动，未写入任何数据。请确认 --old-key 是否正确。",
                    file=sys.stderr,
                )
                return 1
            for row_id, _old_token, new_token in todo:
                conn.execute(
                    update(ProviderChannel)
                    .where(ProviderChannel.id == row_id)
                    .values(key_encrypted=new_token)
                )
            trans.commit()
        except SQLAlchemyError as e:
            trans.rollback()
            print(
                f"数据库操作失败（{type(e).__name__}），已回滚。"
                "请检查 DATABASE_URL 是否正确、迁移是否已跑（alembic upgrade head）。",
                file=sys.stderr,
            )
            return 1
        except Exception:
            trans.rollback()
            raise
        print(
            f"完成: reencrypted={len(todo)} already_new={already_new} "
            f"undecryptable=0 empty={empty}（单一事务提交）"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
