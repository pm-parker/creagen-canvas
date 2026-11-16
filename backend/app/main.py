"""FastAPI 应用工厂：API 路由 + 健康检查 + SPA 静态托管 + 结构化错误处理。

W2：前端构建产物输出到 backend/static/，由 SPA catch-all 托管（深链回落 index.html，
/assets 下不存在文件 404 不回落，/api 命名空间永不回落）。
"""

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.staticfiles import StaticFiles

from backend.app.core.config import get_settings
from backend.app.core.errors import ApiError, api_error_handler, unhandled_error_handler
from backend.app.routers import (
    agent as agent_router,
)
from backend.app.routers import (
    agent_runs,
    assets,
    auth,
    canvas,
    characters,
    costs,
    media,
    openai_api,
    prompts,
    providers,
    settings,
    snapshots,
    storyboard,
    tasks,
    templates,
    uploads,
)
from backend.app.routers import (
    manifest as manifest_router,
)
from backend.app.version import __version__

# 静态目录双结构兼容：优先 backend/static（本地/热更结构），回退 <root>/static（镜像结构）
_STATIC_DIR = Path(__file__).resolve().parents[1] / "static"  # backend/static
if not _STATIC_DIR.is_dir():
    # 镜像形态：Dockerfile COPY dist ./static → /app/static
    _STATIC_DIR = Path(__file__).resolve().parents[2] / "static"



def _cors_origins_or_default() -> list[str]:
    """P2-14: CORS_ORIGINS 收紧；导入期（环境未就绪，如测试收集）回退 *。"""
    try:
        return get_settings().cors_origins
    except Exception:  # noqa: BLE001 — 模块级 create_app() 早于运行时环境
        return ["*"]


def create_app() -> FastAPI:
    app = FastAPI(title="CreaGen Canvas", version=__version__)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins_or_default(),
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.on_event("startup")
    def seed_builtin_data() -> None:
        """首次启动导入内置提示词库（表空才导入，幂等）。"""
        try:
            from backend.app.core.db import get_session_factory
            from backend.app.seed_prompts import seed_prompts_if_empty

            db = get_session_factory()()
            try:
                seed_prompts_if_empty(db)
            finally:
                db.close()
        except Exception as e:  # noqa: BLE001 — seed 失败不阻断启动（如测试内存库未建表）
            logging.getLogger("canvas.seed").warning("prompt seed skipped: %s", e)

    app.include_router(auth.router)
    app.include_router(agent_router.router)
    app.include_router(agent_runs.router)
    app.include_router(tasks.router)
    app.include_router(providers.router)
    app.include_router(canvas.router)
    app.include_router(prompts.router)
    app.include_router(costs.router)
    app.include_router(assets.router)
    app.include_router(uploads.router)
    app.include_router(snapshots.router)
    app.include_router(manifest_router.router)
    app.include_router(media.router)
    app.include_router(storyboard.router)
    app.include_router(characters.router)
    app.include_router(templates.router)
    app.include_router(settings.router)
    app.include_router(openai_api.router)

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "app": "creagen-canvas", "version": __version__}

    # Starlette handler 泛型签名过窄，运行时签名正确
    app.add_exception_handler(ApiError, api_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_error_handler)

    outputs_dir = Path("outputs")
    if outputs_dir.is_dir():

        class CachedStaticFiles(StaticFiles):
            """生成产物带长缓存头 + 缩略图支持。

            ?w=400 → Pillow 等比缩放（仅图片类型）；失败降级原图。
            文件名含随机段，内容不可变，immutable 缓存安全。
            """

            def file_response(self, *args, **kwargs):
                resp = super().file_response(*args, **kwargs)
                resp.headers["Cache-Control"] = "public, max-age=86400, immutable"
                return resp

            async def get_response(self, path: str, scope):
                from urllib.parse import parse_qs

                qs = parse_qs(scope.get("query_string", b"").decode())
                width = qs.get("w", [None])[0]
                if not width:
                    return await super().get_response(path, scope)

                try:
                    w = max(50, min(int(width), 2048))
                except ValueError:
                    return await super().get_response(path, scope)

                # 仅缩放图片
                if not path.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                    return await super().get_response(path, scope)

                # 缩放
                import io

                from PIL import Image
                from starlette.responses import Response

                full = outputs_dir / path
                # P2 安全：raw path 可能携带 ../、编码 %2e%2e%2f 或反斜杠等穿越段，
                # 解析后必须仍位于 outputs 目录内，否则一律回落原文件响应（即 404），
                # 绝不经 Pillow 打开目录外文件（含符号链接目标）。
                try:
                    inside = full.resolve().is_relative_to(outputs_dir.resolve())
                except (OSError, RuntimeError, ValueError):
                    inside = False
                if not inside or not full.is_file():
                    return await super().get_response(path, scope)

                try:
                    img: Image.Image = Image.open(full)
                    img.thumbnail((w, w * 3))
                    buf = io.BytesIO()
                    fmt = "PNG" if path.endswith(".png") else "JPEG"
                    if fmt == "JPEG" and img.mode in ("RGBA", "P"):
                        img = img.convert("RGB")
                    img.save(buf, format=fmt, quality=85)
                    return Response(
                        content=buf.getvalue(),
                        media_type=f"image/{fmt.lower()}",
                        headers={"Cache-Control": "public, max-age=86400, immutable"},
                    )
                except Exception:  # noqa: BLE001 — 缩放失败降级原图
                    return await super().get_response(path, scope)

        app.mount("/outputs", CachedStaticFiles(directory=outputs_dir), name="outputs")

    if _STATIC_DIR.is_dir():

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa_fallback(full_path: str):
            if ".." in full_path or "\x00" in full_path:
                raise HTTPException(
                    status_code=404,
                    detail={"code": "NOT_FOUND", "message": "资源不存在"},
                )
            if full_path.startswith(("api/", "outputs/")):
                raise HTTPException(
                    status_code=404,
                    detail={"code": "NOT_FOUND", "message": "资源不存在"},
                )
            candidate = (_STATIC_DIR / full_path).resolve()
            if candidate.is_file() and candidate.is_relative_to(_STATIC_DIR):
                return FileResponse(candidate)
            index = _STATIC_DIR / "index.html"
            # /assets/* 是带内容哈希的真实资源，缺失即 404，绝不回落 index.html
            if not full_path.startswith("assets/") and index.is_file():
                # index.html 每次都要新鲜：否则部署新版本后用户端启发式缓存旧页，
                # 引用已被替换的旧哈希资源，表现为"部署了但没变化，需手动强刷"
                return FileResponse(index, headers={"Cache-Control": "no-cache"})
            raise HTTPException(
                status_code=404, detail={"code": "NOT_FOUND", "message": "资源不存在"}
            )

    return app


app = create_app()
