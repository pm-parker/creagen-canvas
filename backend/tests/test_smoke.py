"""仓库冒烟测试：包结构与导入路径健康。"""


def test_import_app_package() -> None:
    import backend.app

    assert backend.app is not None


def test_core_package_exists() -> None:
    import backend.app.core  # noqa: F401
