# AGENTS.md — CreaGen Canvas 开发规范

> 本文件供人类与 AI coding agent 共同遵守。参照 BeefTV / infinite-canvas 的 agent 协作模式。

## 分支与提交

- 分支命名：`codex/<scope>-<topic>-<date>`（如 `codex/canvas-sse-20261001`）；`main` 保持可运行。
- Commit 格式：`type(scope): 中文描述`，type ∈ feat/fix/test/style/chore/docs/release。
- 一个 commit 只做一件事；回归与实现同 commit 提交。

## 质量门禁（每个 commit 前必须全绿）

```bash
ruff check . && mypy backend && pytest -m "not integration"
```

- 标记 `@pytest.mark.integration` 的用例需要真实 PG/Redis（CI 中跑）。
- TDD：先写失败测试，看过 RED 才写实现。

## 架构约束（违反即打回）

- 生产 fnos（/vol1/1000/docker/creagen）只读，任何抽取经 `ssh fnos cat`。
- 渠道 key 只存密文；API 响应/日志禁止明文。
- 队列白名单：`generate` / `maintenance`。
- 错误一律走 `core/errors.py` 的结构化 `ApiError(code, http_status, message, hint)`，禁止裸 500。

## 抽取约定（W1）

- 从生产版抽取的文件头部保留注释 `# derived from creagen prod <path> @ <date>`，便于 cherry-pick 回流。
- 生产代码中 AIHOT/GPU/发布/内网地址（192.168.*、127.0.0.1 硬编码）不得进入本仓库。
