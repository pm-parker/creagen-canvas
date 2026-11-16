# CreaGen Canvas

自托管的 AI 创作画布：多用户、自带任务队列与成本治理，生图/视频/LLM 渠道开箱可配。
在一个无限画布上写提示词、连线做参考图（图生图/首帧生视频）、批量创作与成片合成；
后端内置 arq 队列、渠道密钥加密存储与按模型计价/配额治理，适合小团队自部署。

## 快速开始

```bash
git clone https://github.com/pm-parker/creagen-canvas.git && cd creagen-canvas
cp .env.example .env   # 必改 JWT_SECRET / ENCRYPTION_KEY（各 openssl rand -hex 32）与 INVITE_CODES
docker compose up -d   # api(8080) + worker + PG16 + redis，migrate 自动跑迁移
```

## 文档

- 部署指南：[docs/deploy.md](docs/deploy.md)（三步起跑、备份、安全建议）
- 配置项说明：[.env.example](.env.example)（逐项注释，含安全校验要求）

## 开发

```bash
uv sync
ruff check . && mypy backend && pytest -m "not integration"
```

## 许可证

[MIT](LICENSE)
