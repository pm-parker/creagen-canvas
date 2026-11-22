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

登录限流（`backend/app/routers/auth.py`）以 `request.client.host` 作为 per-IP 计数键。当前镜像还没有加上代理头参数。`docker/api.Dockerfile` 的实际命令是：

```dockerfile
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

`docker-compose.yml` 的 `api` 服务不覆盖这条命令，环境变量里也没有 `FORWARDED_ALLOW_IPS`、`--proxy-headers` 或 `--forwarded-allow-ips`。仓库因此没有把任何反代地址配成可信来源。uvicorn CLI 在未传入 `--forwarded-allow-ips`、也未设置 `FORWARDED_ALLOW_IPS` 时，信任列表只含本机回环（已核对 uvicorn 0.54 的帮助文本：`127.0.0.1,::1`；`--proxy-headers` 默认为开启）。容器网桥上的反代不在这张表里，`X-Forwarded-For` 不会被采用，`request.client.host` 仍是反代自己的地址，所有用户落在同一个限流桶。

部署在反代后面时，由部署方在启动命令里自行加上参数，地址填这次部署里反代连接 api 所用的地址：

```text
uvicorn backend.app.main:app --host 0.0.0.0 --port 8080 \
  --proxy-headers --forwarded-allow-ips=<反代IP>
```

也可以由部署方自己设置环境变量 `FORWARDED_ALLOW_IPS=<反代IP>`（本仓库的 compose 没有这项）。`--forwarded-allow-ips` 只能填可信反代的 IP，切勿填 `*`：`X-Forwarded-For` 可被任意客户端伪造，信任它会让人冒充他人 IP 绕过限流。

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
