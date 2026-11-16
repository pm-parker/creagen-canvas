# 部署指南

## 三步起跑

1. `git clone https://github.com/pm-parker/creagen-canvas.git && cd creagen-canvas`
2. `cp .env.example .env` —— 必改三项：`JWT_SECRET`、`ENCRYPTION_KEY`（各 `openssl rand -hex 32`；
   不足 16 字符会被启动校验直接拒绝，16~31 位或仍含 `change-me` 等占位值时启动会打 WARNING——
   请换成强随机值，ENCRYPTION_KEY 轮换前务必备份：换值后已存渠道 key 不可解密）、
   `INVITE_CODES`（如 `openssl rand -hex 8`；无内置默认码，invite 模式下留空则注册一律拒绝）
3. `docker compose up -d`

服务拓扑：`api`(8080) + `worker`(arq) + `db`(PG16) + `redis`(7)，`migrate` 一次性任务自动跑迁移。

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
