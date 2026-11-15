# 部署指南

## 三步起跑

1. `git clone https://github.com/SonicBotMan/creagen-canvas.git && cd creagen-canvas`
2. `cp .env.example .env` —— 必改三项：`JWT_SECRET`、`ENCRYPTION_KEY`（各 `openssl rand -hex 32`）、`INVITE_CODES`
3. `docker compose up -d`

服务拓扑：`api`(8080) + `worker`(arq) + `db`(PG16) + `redis`(7)，`migrate` 一次性任务自动跑迁移。

## 数据与备份

- 业务数据：PostgreSQL `pgdata` 卷
- 渠道密钥：AES-GCM 加密后存库，根为 `ENCRYPTION_KEY`——**此 key 丢失则已存渠道 key 全部不可解密**，请随 `.env` 一并备份
- 备份：`docker compose exec db pg_dump -U creagen creagen > backup.sql`

## 安全建议（公网部署）

- 反向代理层加 HTTPS（Caddy/Traefik/Cloudflare）
- 保持 `REGISTER_MODE=invite`
- `ENABLE_FAKE_PROVIDER=false`（默认）
- API 不直接暴露公网时，经反代限流

## 验收

`BASE=http://localhost:8080 INVITE=<你的邀请码> ./scripts/acceptance-w1.sh`
