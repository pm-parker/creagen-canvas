"""SPA 托管契约（Review Focus #1）：深链回落 + 资源/命名空间不误回落。"""


def test_spa_deep_link_returns_index(client):
    r = client.get("/canvas/5")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_missing_asset_404_not_fallback(client):
    r = client.get("/assets/nonexistent.js")
    assert r.status_code == 404


def test_api_namespace_never_falls_back(client):
    r = client.get("/api/definitely-not-a-route")
    assert r.status_code == 404
    assert "text/html" not in r.headers.get("content-type", "")


def test_path_traversal_rejected(client):
    """审计回归：路径穿越段一律 404，不回落 index.html。"""
    # httpx 会规范化字面 ../，故用 %2e%2e 编码形式驱动真实攻击向量
    for path in ("/static/%2e%2e/%2e%2e/etc/passwd", "/x/%2e%2e/%2e%2e/etc/passwd"):
        r = client.get(path)
        assert r.status_code == 404, path


def test_health_version_matches_single_source(client):
    """版本号单一来源：/api/health == backend.app.version.__version__ == 仓库 VERSION 文件。"""
    from pathlib import Path

    from backend.app.version import __version__

    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["version"] == __version__

    repo_version = (Path(__file__).resolve().parents[2] / "VERSION").read_text().strip()
    assert repo_version == __version__, (
        f"VERSION 文件({repo_version})与代码版本({__version__})不一致"
    )


def test_spa_index_no_cache(client):
    """index.html 必须带 no-cache：否则部署新版本后用户端缓存旧页引用旧哈希资源。"""
    r = client.get("/some/deep/link")
    assert r.status_code == 200
    assert r.headers.get("cache-control") == "no-cache"
