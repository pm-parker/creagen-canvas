# ---- stage 1: 前端构建 ----
FROM node:22-alpine AS web
WORKDIR /web
RUN corepack enable
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ .
# CLI 覆盖输出目录（容器内无 backend 结构）
RUN pnpm build --outDir /web/dist --emptyOutDir

# ---- stage 2: 后端运行时 ----
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 curl ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md LICENSE ./
COPY backend ./backend
COPY alembic.ini ./
COPY alembic ./alembic
# 与本地结构一致：仓库根/static（main.py 的 _STATIC_DIR 解析到此处）
COPY --from=web /web/dist ./static

RUN pip install --no-cache-dir \
    "fastapi>=0.115" "uvicorn[standard]>=0.30" "httpx>=0.27" "arq>=0.26" \
    "alembic>=1.13" "python-jose[cryptography]>=3.3" "bcrypt" \
    "python-multipart>=0.0.9" "psycopg[binary]>=3.2" "redis>=5.0" \
    "sqlalchemy>=2.0" "pillow>=12.2" "pydantic-settings" "cryptography"

EXPOSE 8080
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8080"]
