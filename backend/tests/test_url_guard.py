"""URL 安全校验（SSRF 防护）：拒绝私网/环回/链路本地/保留地址目标。"""

from unittest.mock import patch

import pytest

from backend.app.services.url_guard import UrlBlocked, assert_public_http_url


def _resolve(ip: str):
    """mock getaddrinfo：任何主机名解析到指定 IP。"""
    return patch(
        "backend.app.services.url_guard.socket.getaddrinfo",
        return_value=[(None, None, None, "", (ip, 0))],
    )


class TestAssertPublicHttpUrl:
    def test_private_and_loopback_blocked(self):
        for ip in [
            "127.0.0.1",
            "10.0.0.5",
            "192.168.1.147",
            "172.16.0.1",
            "169.254.169.254",  # 云元数据端点
            "::1",
            "fe80::1",
            "fc00::1",
        ]:
            with _resolve(ip), pytest.raises(UrlBlocked):
                assert_public_http_url("http://evil.example.com/x")

    def test_public_passes(self):
        with _resolve("93.184.216.34"):
            assert_public_http_url("https://example.com/data.json")

        with _resolve("2606:2800:220:1:248:1893:25c8:1946"):
            assert_public_http_url("https://example.com/v6")

    def test_scheme_and_form_validated(self):
        for bad in ["ftp://x", "file:///etc/passwd", "not-a-url", "http://"]:
            with pytest.raises(UrlBlocked):
                assert_public_http_url(bad)

    def test_hostname_resolution_failure_blocked(self):
        with patch(
            "backend.app.services.url_guard.socket.getaddrinfo",
            side_effect=OSError("dns"),
        ), pytest.raises(UrlBlocked):
            assert_public_http_url("https://nonexistent.invalid/x")


class TestImportUrlGuard:
    """import-url 路由接驳：私网目标 422 SSRF_BLOCKED。"""

    def test_import_url_rejects_private(self, client):
        headers = {}
        r = client.post(
            "/api/auth/register",
            json={
                "username": "ssrfuser",
                "email": "ssrf@x.io",
                "password": "password123",
                "invite_code": "letmein",
            },
        )
        headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
        r = client.post(
            "/api/prompts/import-url",
            json={"url": "http://127.0.0.1:8080/api/health"},
            headers=headers,
        )
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "SSRF_BLOCKED"
