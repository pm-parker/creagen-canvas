"""scripts/reencrypt_channel_keys：ENCRYPTION_KEY 轮换脚本（pr4）。

场景：dry-run 不写库；正常迁移全部重加密；重跑幂等；坏行整体回滚。
用 sqlite（StaticPool 内存库）按既有单测方式跑，不依赖真实 PG。
"""

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.models import Base, ProviderChannel
from scripts import reencrypt_channel_keys as mod

# 测试密钥：旧密钥故意偏短（存量部署允许），新密钥满足 >=32 规则
OLD_KEY = "short-legacy-key"
NEW_KEY = "n" * 40
OTHER_KEY = "o" * 40  # 第三把密钥（与旧/新均不同的错误密钥，用于构造坏行）


def _cipher(key: str):
    return mod._build_cipher(key)


@pytest.fixture
def renc_db(monkeypatch):
    """同一内存库给测试与脚本用（脚本内部 create_engine 指回本 fixture 的引擎）。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(mod, "create_engine", lambda _url: engine)
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    return engine


def _seed(engine, rows):
    s = sessionmaker(bind=engine)()
    for name, token in rows:
        s.add(
            ProviderChannel(
                domain="image",
                name=name,
                base_url="https://x.example/v1",
                key_encrypted=token,
            )
        )
    s.commit()
    s.close()


def _tokens(engine):
    s = sessionmaker(bind=engine)()
    try:
        rows = s.execute(select(ProviderChannel.name, ProviderChannel.key_encrypted)).all()
        return dict(rows)
    finally:
        s.close()


def test_dry_run_writes_nothing(renc_db, capsys):
    _seed(renc_db, [("ch1", mod._encrypt("sk-1", _cipher(OLD_KEY)))])
    before = _tokens(renc_db)

    rc = mod.main(["--old-key", OLD_KEY, "--new-key", NEW_KEY, "--dry-run"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "to_reencrypt=1" in out and "already_new=0" in out and "undecryptable=0" in out
    assert _tokens(renc_db) == before, "dry-run 不得写库"


def test_dry_run_reports_undecryptable_count(renc_db, capsys):
    """dry-run 遇坏行：报告 undecryptable 计数、非零退出，但不写库。"""
    before_rows = [("good", mod._encrypt("sk-1", _cipher(OLD_KEY))), ("bad", "v1:AAAAbadAAAA")]
    _seed(renc_db, before_rows)
    before = _tokens(renc_db)

    rc = mod.main(["--old-key", OLD_KEY, "--new-key", NEW_KEY, "--dry-run"])
    captured = capsys.readouterr()

    assert rc == 1
    assert "undecryptable=1" in captured.out
    assert "无法解密" in captured.err
    assert _tokens(renc_db) == before


def test_reencrypts_all_rows_and_idempotent_rerun(renc_db, capsys):
    _seed(
        renc_db,
        [
            ("ch1", mod._encrypt("sk-1", _cipher(OLD_KEY))),
            ("ch2", mod._encrypt("sk-2", _cipher(OLD_KEY))),
            ("empty", ""),
        ],
    )

    rc = mod.main(["--old-key", OLD_KEY, "--new-key", NEW_KEY])
    assert rc == 0
    out = capsys.readouterr().out
    assert "reencrypted=2" in out and "already_new=0" in out

    tokens = _tokens(renc_db)
    assert mod._try_decrypt(tokens["ch1"], _cipher(NEW_KEY)) == "sk-1"
    assert mod._try_decrypt(tokens["ch2"], _cipher(NEW_KEY)) == "sk-2"
    assert tokens["empty"] == ""

    # 重跑：全部已是新密钥加密 -> 幂等 no-op
    rc = mod.main(["--old-key", OLD_KEY, "--new-key", NEW_KEY])
    assert rc == 0
    out = capsys.readouterr().out
    assert "reencrypted=0" in out and "already_new=2" in out
    assert _tokens(renc_db) == tokens


def test_mixed_rows_partial_already_new(renc_db, capsys):
    """旧密文与已用新密钥加密的行并存：只重加密旧的那部分。"""
    _seed(
        renc_db,
        [
            ("old-ch", mod._encrypt("sk-old", _cipher(OLD_KEY))),
            ("new-ch", mod._encrypt("sk-new", _cipher(NEW_KEY))),
        ],
    )

    rc = mod.main(["--old-key", OLD_KEY, "--new-key", NEW_KEY])
    assert rc == 0
    out = capsys.readouterr().out
    assert "reencrypted=1" in out and "already_new=1" in out

    tokens = _tokens(renc_db)
    assert mod._try_decrypt(tokens["old-ch"], _cipher(NEW_KEY)) == "sk-old"
    assert mod._try_decrypt(tokens["new-ch"], _cipher(NEW_KEY)) == "sk-new"


def test_undecryptable_row_rolls_back_everything(renc_db, capsys):
    """任一行新旧密钥都解不开：非零退出且所有行保持原样（单一事务回滚）。"""
    plain_tokens = {
        "good-1": mod._encrypt("sk-1", _cipher(OLD_KEY)),
        "good-2": mod._encrypt("sk-2", _cipher(OLD_KEY)),
        "bad": "v1:AAAAnotdecryptableAAAA",
    }
    _seed(renc_db, list(plain_tokens.items()))
    before = _tokens(renc_db)

    rc = mod.main(["--old-key", OLD_KEY, "--new-key", NEW_KEY])
    assert rc == 1
    err = capsys.readouterr().err
    assert "回滚" in err and "无法解密" in err

    after = _tokens(renc_db)
    assert after == before, "坏行必须导致整体回滚，早先行不得被改动"
    # 早前可解密的行仍用旧密钥可解（未被半迁移）
    assert mod._try_decrypt(after["good-1"], _cipher(OLD_KEY)) == "sk-1"
    assert mod._try_decrypt(after["good-2"], _cipher(OLD_KEY)) == "sk-2"

    # dry-run 遇坏行同样中止且不写库
    rc = mod.main(["--old-key", OLD_KEY, "--new-key", NEW_KEY, "--dry-run"])
    assert rc == 1
    assert _tokens(renc_db) == before


def test_keys_resolved_from_env(renc_db, monkeypatch, capsys):
    """--old-key/--new-key 缺省回落 OLD_ENCRYPTION_KEY / ENCRYPTION_KEY 环境变量。"""
    _seed(renc_db, [("ch", mod._encrypt("sk-env", _cipher(OLD_KEY)))])
    monkeypatch.setenv("OLD_ENCRYPTION_KEY", OLD_KEY)
    monkeypatch.setenv("ENCRYPTION_KEY", OTHER_KEY)

    rc = mod.main(["--dry-run"])
    assert rc == 0
    assert "to_reencrypt=1" in capsys.readouterr().out


def test_weak_new_key_rejected_before_touching_db(renc_db, capsys):
    _seed(renc_db, [("ch", mod._encrypt("sk-1", _cipher(OLD_KEY)))])
    before = _tokens(renc_db)

    rc = mod.main(["--old-key", OLD_KEY, "--new-key", "tooshort", "--dry-run"])
    assert rc == 1
    assert "ENCRYPTION_KEY" in capsys.readouterr().err
    assert _tokens(renc_db) == before


def test_missing_key_args_exit_nonzero(monkeypatch, capsys):
    monkeypatch.delenv("OLD_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    rc = mod.main(["--old-key", OLD_KEY])  # 缺 --new-key 且未设 ENCRYPTION_KEY
    assert rc == 1
    assert "ENCRYPTION_KEY" in capsys.readouterr().err


def test_no_key_material_in_output(renc_db, capsys):
    """输出（成功/失败）不得包含任何密钥或渠道明文 key。"""
    _seed(
        renc_db,
        [("ch", mod._encrypt("sk-topsecret", _cipher(OLD_KEY)))],
    )
    mod.main(["--old-key", OLD_KEY, "--new-key", NEW_KEY])
    mod.main(["--old-key", OLD_KEY, "--new-key", NEW_KEY])  # 幂等重跑
    # 用一把未知密钥加密新行（两把密钥都解不开）触发失败路径，不碰已迁移行
    _seed(renc_db, [("foreign", mod._encrypt("sk-other", _cipher(OTHER_KEY)))])
    rc = mod.main(["--old-key", OLD_KEY, "--new-key", NEW_KEY])  # foreign 行 -> 坏行
    captured = capsys.readouterr()
    assert rc == 1
    for secret in (OLD_KEY, NEW_KEY, OTHER_KEY, "sk-topsecret"):
        assert secret not in captured.out
        assert secret not in captured.err
