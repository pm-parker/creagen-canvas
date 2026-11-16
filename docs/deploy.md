# 部署指南

## 三步起跑

1. `git clone https://github.com/pm-parker/creagen-canvas.git && cd creagen-canvas`
2. `cp .env.example .env` —— 必改三项：
   - `JWT_SECRET`、`ENCRYPTION_KEY`：各执行 `openssl rand -hex 32` 生成（**必须 >=32 字符**；
     过短或仍含 `change-me` / `please-change` 等占位特征时，启动校验直接报错拒绝启动，
     不再是仅告警）。注意 `ENCRYPTION_KEY` 轮换前须先重加密已存渠道密钥，见下文「密钥轮换」。
   - `INVITE_CODES`（如 `openssl rand -hex 8`；无内置默认码，invite 模式下留空则注册一律拒绝）
3. `docker compose up -d`

服务拓扑：`api`(8080) + `worker`(arq) + `db`(PG16) + `redis`(7)，`migrate` 一次性任务自动跑迁移。

## ⚠️ 升级说明（本版本的破坏性变更）

**本版本起，`JWT_SECRET` 与 `ENCRYPTION_KEY` 少于 32 字符、或仍是示例占位值
（含 `change-me` / `please-change` / `your-secret` 等特征）时，服务拒绝启动**（此前的
“偏短仅告警”策略已移除）。旧部署若用的是短密钥，升级后需先生成新密钥再按
「密钥轮换」处理，否则 `api` / `worker` 会因配置校验失败而退出。

生成新密钥：

```bash
openssl rand -hex 32   # 分别用于 JWT_SECRET 与 ENCRYPTION_KEY
```

- **`JWT_SECRET` 轮换**：直接换值重启即可，代价只是所有用户被登出（重新登录即可），
  数据无损。
- **`ENCRYPTION_KEY` 轮换**：渠道 API key 用它加密后存库，**直接换值会使已保存的渠道
  key 全部无法解密**。必须按下面步骤先重加密再换值。

## 密钥轮换（ENCRYPTION_KEY）

前提：新密钥已用 `openssl rand -hex 32` 生成且通过 >=32 / 非占位值校验。

1. **备份数据库**（必做）：

   ```bash
   docker compose exec db pg_dump -U creagen creagen > backup-$(date +%F).sql
   ```

2. **试运行**（只统计，不写库）：确认 `undecryptable=0`，`to_reencrypt` 数量符合预期。
   宿主机直跑（推荐，需能连通目标库）：

   ```bash
   OLD=$(grep '^ENCRYPTION_KEY=' .env | cut -d= -f2-)   # 旧值
   NEW=$(openssl rand -hex 32)                          # 新值（先只生成，别急着写进 .env）
   DATABASE_URL='<指向目标库的连接串>' \
     python -m scripts.reencrypt_channel_keys --old-key "$OLD" --new-key "$NEW" --dry-run
   ```

   或在 compose 容器里跑（需给 migrate 服务临时加挂载 `- ./scripts:/app/scripts:ro`，
   且因其 entrypoint 固定为 `sh -c "alembic upgrade head"`，必须用 `--entrypoint` 覆盖）：

   ```bash
   docker compose run --rm --entrypoint python \
     -e OLD_ENCRYPTION_KEY="$OLD" -e ENCRYPTION_KEY="$NEW" \
     migrate -m scripts.reencrypt_channel_keys --dry-run
   ```

3. **正式重加密**（单一事务：任何一行新旧密钥都解不开即整体回滚、非零退出，
   成功后重跑为幂等 no-op）——与第 2 步同一命令，去掉 `--dry-run`。

4. 把 `.env` 的 `ENCRYPTION_KEY` 换成第 2 步生成的 `$NEW` 并重启：`docker compose up -d`。
   验证：任一已配置渠道能正常发起生成任务（说明旧密文已可被新密钥解密）。

脚本输出只含计数（`to_reencrypt` / `already_new` / `undecryptable`），不会打印任何
密钥或渠道明文 key。

## 数据与备份

- 业务数据：PostgreSQL `pgdata` 卷
- 渠道密钥：AES-GCM 加密后存库，根为 `ENCRYPTION_KEY`——**此 key 丢失则已存渠道 key 全部不可解密**，请随 `.env` 一并备份
- 备份：`docker compose exec db pg_dump -U creagen creagen > backup.sql`

## 安全建议（公网部署）

- 反向代理层加 HTTPS（Caddy/Traefik/Cloudflare）
- 保持 `REGISTER_MODE=invite`
- `ENABLE_FAKE_PROVIDER=false`（默认；仅在本地开发/CI 需跑通 Fake 闭环时显式设 true）
- API 不直接暴露公网时，经反代限流

## 验收

`BASE=http://localhost:8080 INVITE=<你的邀请码> ./scripts/acceptance-w1.sh`

注：脚本第 5 步（FakeProvider 任务闭环）需在 `.env` 显式设 `ENABLE_FAKE_PROVIDER=true`
（默认 false）；未开启时该步会因无可用渠道而失败，其余步骤不受影响。邀请码无内置默认值，
`INVITE` 环境变量必填（即 `.env` 中 `INVITE_CODES` 配置的那个码）。
