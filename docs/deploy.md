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

### 反向代理与登录限流的客户端 IP

登录限流（`backend/app/routers/auth.py`）以 `request.client.host` 作为 per-IP 计数键。uvicorn 默认不解析代理头，直连时该值就是反代的 IP——所有用户共享同一个桶。因此挂在 nginx 等反代后面时，必须让 uvicorn 信任代理头且只信任代理本身：

- 启动参数加 `--proxy-headers --forwarded-allow-ips="<反代IP>"`（或设置环境变量 `FORWARDED_ALLOW_IPS`），本仓库的 uvicorn 命令在 `docker/api.Dockerfile`：

  ```dockerfile
  CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8080",
       "--proxy-headers", "--forwarded-allow-ips", "172.17.0.1"]
  ```

  反代与 api 同机 docker 网络时填网关地址（如 `172.17.0.1` 或 compose 网段）；也可改用 `environment: FORWARDED_ALLOW_IPS=...`。
- `--forwarded-allow-ips` 只能填可信反代的 IP，切勿填 `*`：`X-Forwarded-For` 可被任意客户端伪造，信任它会让人冒充他人 IP 绕过限流。
- nginx 侧配合（只转发，并覆盖客户端传入的头）：

  ```nginx
  location / {
      proxy_pass http://127.0.0.1:8080;
      proxy_set_header Host $host;
      proxy_set_header X-Forwarded-For $remote_addr;   # 直接覆盖，丢弃客户端自带的值
      proxy_set_header X-Forwarded-Proto $scheme;
  }
  ```

### 已知限制：撞库（credential stuffing）防护

同一 IP 换着账号批量试密码时，目前只有「按 IP 的失败计数」在密码错误后才累计这一层约束；按用户名（identifier）的锁定只保护单个账号。尚无全局维度的撞库防护（如按 IP+多账号的聚合限流、验证码/指数退避），有需要请在反代或 WAF 层补充。

## 验收

`BASE=http://localhost:8080 INVITE=<你的邀请码> ./scripts/acceptance-w1.sh`
