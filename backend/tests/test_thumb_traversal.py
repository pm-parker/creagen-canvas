"""缩略图路径穿越回归（P2 安全）。

/outputs 静态挂载的 ?w= 缩略图分支曾用未净化的 raw path 拼 `outputs_dir / path`
再交给 Pillow 打开：`/outputs/..%2f..%2fsecret.png?w=100`（URL 编码段在路由层
被还原成 `../`）可读取 outputs 目录外的图片，且无需认证。
修复后：解析结果必须仍位于 outputs_dir 内（Path.is_relative_to），
否则回落 super().get_response（404），绝不打开目录外文件。
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.app.main import create_app


def _png(path: Path, size: tuple[int, int] = (800, 600), color: str = "red") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)


@pytest.fixture
def outputs_client(tmp_path, monkeypatch) -> TestClient:
    """cwd 切到临时目录再建 app：main.create_app() 以相对路径 `outputs` 决定是否挂载。"""
    (tmp_path / "outputs").mkdir()
    _png(tmp_path / "outputs" / "legit.png", color="blue")
    _png(tmp_path / "outside" / "secret.png", color="red")
    monkeypatch.chdir(tmp_path)
    app = create_app()
    with TestClient(app) as c:
        yield c


@pytest.mark.parametrize(
    "variant",
    [
        # URL 编码的 ../（路由层还原后 path 参数即带穿越段 —— 修复前的可利用形态）
        "encoded_parent",
        "encoded_parent_twice",
        "encoded_dotdot",
        # 明文 ../（通常被客户端/代理规范化，但服务端不得依赖这一点）
        "plain_parent",
        "plain_nested_parent",
        # 反斜杠变体
        "backslash",
        # 绝对路径（Path 拼接时绝对路径直接取代基底）
        "absolute",
    ],
)
def test_traversal_cannot_read_outside_image(outputs_client, tmp_path, variant):
    """穿越变体一律不得返回目录外图片字节。"""
    secret = tmp_path / "outside" / "secret.png"
    urls = {
        "encoded_parent": "/outputs/..%2foutside%2fsecret.png?w=100",
        "encoded_parent_twice": "/outputs/..%2F..%2Ftmp%2fout%2Fsecret.png?w=100",
        "encoded_dotdot": "/outputs/%2e%2e/outside/secret.png?w=100",
        "plain_parent": "/outputs/../outside/secret.png?w=100",
        "plain_nested_parent": "/outputs/a/../../outside/secret.png?w=100",
        "backslash": "/outputs/..%5coutside%5csecret.png?w=100",
        "absolute": "/outputs" + str(secret).replace("/", "%2F") + "?w=100",
    }
    url = urls[variant]
    r = outputs_client.get(url)
    assert r.status_code == 404, f"{url} -> {r.status_code} {r.content[:80]!r}"
    assert not r.content.startswith(b"\x89PNG")


def test_symlink_inside_outputs_is_rejected(outputs_client, tmp_path):
    """outputs 内指向目录外的符号链接同样不得经缩略图分支读出。"""
    link = tmp_path / "outputs" / "link.png"
    link.symlink_to(tmp_path / "outside" / "secret.png")
    r = outputs_client.get("/outputs/link.png?w=100")
    assert r.status_code == 404, r.content[:80]
    r2 = outputs_client.get("/outputs/link.png")
    assert r2.status_code == 404


def test_legit_thumbnail_still_works(outputs_client):
    """正例：outputs 内合法图片的缩略图照常生成（小图 + image/* 响应）。"""
    r = outputs_client.get("/outputs/legit.png?w=100")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("image/")
    import io

    img = Image.open(io.BytesIO(r.content))
    assert img.width <= 100


def test_legit_full_file_still_works(outputs_client):
    """正例：不带 ?w= 的原图服务不受影响。"""
    r = outputs_client.get("/outputs/legit.png")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/")
